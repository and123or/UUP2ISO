"""TaskSession 门禁（编排 §4、§6、§9）。

验收对应：**子步骤级续跑**、**配置/输入变更时拒绝续跑**、**四条件不满足即重做**。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from uup2iso.application.task_session import TaskSession
from uup2iso.core.models.checkpoint import ResumeDecision
from uup2iso.core.models.enums import StageState, SubstepState, TaskState
from uup2iso.core.models.pipeline import PIPELINE_ORDER

NOW = datetime(2026, 11, 1, 12, 0, 0)


def make_session(tmp_path: Path, *, config_fingerprint: str = "cfg-1") -> tuple[TaskSession, Path]:
    work = tmp_path / "work" / "7f3a1b2c"
    session = TaskSession(
        task_id="7f3a1b2c",
        work_dir=work,
        config_fingerprint=config_fingerprint,
        clock=lambda: NOW,
    )
    return session, work


def write_artifact(work: Path, name: str = "install.wim", data: bytes = b"WIMDATA") -> Path:
    path = work / "image" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# --- 状态机 ---------------------------------------------------------------


def test_start_creates_running_checkpoint(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    checkpoint = session.start(input_fingerprint="in-1")

    assert checkpoint.status is TaskState.RUNNING
    assert checkpoint.config_fingerprint == "cfg-1"
    assert checkpoint.input_fingerprint == "in-1"
    assert session.store.path.is_file()
    assert session.store.path.parent == work


def test_stage_transitions_are_persisted(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    session.start()

    session.begin_stage("Convert")
    assert session.checkpoint is not None
    convert = session.checkpoint.stages["Convert"]
    assert convert.status is StageState.RUNNING and convert.attempts == 1

    session.end_stage("Convert", StageState.SUCCEEDED)

    reloaded = TaskSession(task_id="7f3a1b2c", work_dir=work, config_fingerprint="cfg-1")
    reloaded.load()
    assert reloaded.checkpoint is not None
    assert reloaded.checkpoint.stages["Convert"].status is StageState.SUCCEEDED
    assert reloaded.checkpoint.stages["Convert"].finished_at is not None


def test_substeps_are_written_immediately(tmp_path: Path) -> None:
    """子步骤完成即写（编排 §3 规则 1）：40 分钟的 aggregate 不能白做。"""
    session, work = make_session(tmp_path)
    session.start()

    session.begin_stage("Convert")
    for substep in ("config", "cab", "aggregate"):
        session.begin_substep("Convert", substep)
        session.end_substep("Convert", substep, SubstepState.DONE)

    reloaded = TaskSession(task_id="7f3a1b2c", work_dir=work, config_fingerprint="cfg-1")
    reloaded.load()
    assert reloaded.checkpoint is not None
    done = reloaded.checkpoint.stages["Convert"].substeps
    assert set(done) == {"config", "cab", "aggregate"}
    assert all(item.status is SubstepState.DONE for item in done.values())


def test_record_artifact_computes_size_and_hash(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    session.start()
    path = write_artifact(work)

    record = session.record_artifact("Convert", "install.wim", path)

    assert record.size == len(b"WIMDATA")
    assert len(record.sha256) == 64
    assert record.path.endswith("install.wim"), "应存相对路径，便于整目录搬走"


def test_finish_and_interrupt(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    session.start()

    assert session.interrupt() is True
    assert session.checkpoint is not None
    assert session.checkpoint.status is TaskState.INTERRUPTED

    assert session.interrupt() is False, "已不是 running，不应重复标中断"

    session.finish(TaskState.FAILED)
    assert session.checkpoint.status is TaskState.FAILED


# --- 续跑判定 -------------------------------------------------------------


def test_no_checkpoint_is_new_task(tmp_path: Path) -> None:
    session, _work = make_session(tmp_path)
    plan = session.plan_resume()

    assert plan.decision is ResumeDecision.NEW_TASK
    assert plan.needs_full_restart


def test_corrupt_checkpoint_falls_back_to_new_task(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    work.mkdir(parents=True)
    (work / "checkpoint.json").write_text("{ broken", encoding="utf-8")

    plan = session.plan_resume()
    assert plan.decision is ResumeDecision.NEW_TASK
    assert "检查点不可用" in plan.message


def test_succeeded_task_is_not_resumed(tmp_path: Path) -> None:
    session, _work = make_session(tmp_path)
    session.start()
    session.finish(TaskState.SUCCEEDED)

    plan = session.plan_resume()
    assert plan.decision is ResumeDecision.ALREADY_SUCCEEDED
    assert plan.resumable is False


def test_config_change_is_rejected(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    session.start()
    session.end_stage("Prepare", StageState.SUCCEEDED)

    fresh = TaskSession(task_id="7f3a1b2c", work_dir=work, config_fingerprint="cfg-2")
    plan = fresh.plan_resume()

    assert plan.decision is ResumeDecision.REJECT_CONFIG_CHANGED
    assert "新建任务" in plan.message
    assert plan.resumable is False


def test_input_change_forces_full_restart(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    session.start(input_fingerprint="in-1")
    session.end_stage("Prepare", StageState.SUCCEEDED)

    fresh = TaskSession(task_id="7f3a1b2c", work_dir=work, config_fingerprint="cfg-1")
    plan = fresh.plan_resume(input_fingerprint="in-2")

    assert plan.decision is ResumeDecision.REJECT_INPUT_CHANGED
    assert plan.redo_stages == tuple(stage.value for stage in PIPELINE_ORDER)


def test_partial_progress_resumes_from_first_unfinished_stage(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    session.start(input_fingerprint="in-1")
    session.end_stage("Prepare", StageState.SUCCEEDED)
    session.end_stage("Fetch", StageState.SUCCEEDED)
    session.end_stage("Convert", StageState.FAILED)
    session.finish(TaskState.FAILED)

    plan = session.plan_resume(input_fingerprint="in-1")

    assert plan.decision is ResumeDecision.RESUME
    assert plan.reuse_stages == ("Prepare", "Fetch")
    assert plan.redo_stages[0] == "Convert"
    assert "Convert" not in plan.reuse_stages


def test_running_checkpoint_is_interrupted(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    session.start()
    session.begin_stage("Prepare")

    fresh = TaskSession(task_id="7f3a1b2c", work_dir=work, config_fingerprint="cfg-1")
    plan = fresh.plan_resume()

    assert plan.decision is ResumeDecision.INTERRUPTED
    assert plan.resumable is True


def test_substep_level_resume_point(tmp_path: Path) -> None:
    """在 aggregate 完成、skeleton 未开始时中断 → 续跑从 skeleton 起。"""
    session, work = make_session(tmp_path)
    session.start(input_fingerprint="in-1")
    session.begin_stage("Convert")
    for substep in ("config", "cab", "aggregate"):
        session.end_substep("Convert", substep, SubstepState.DONE)
    session.begin_substep("Convert", "skeleton")

    plan = session.plan_resume(input_fingerprint="in-1")

    assert plan.resume_from_substep.get("Convert") == "skeleton"


# --- 产物复用四条件 -------------------------------------------------------


def test_artifact_valid_allows_stage_reuse(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    session.start(input_fingerprint="in-1")
    session.end_stage("Prepare", StageState.SUCCEEDED)
    session.end_stage("Fetch", StageState.SKIPPED, skip_reason="input_satisfied")
    path = write_artifact(work)
    session.record_artifact("Convert", "install.wim", path)
    session.end_stage("Convert", StageState.SUCCEEDED)
    session.finish(TaskState.CANCELLED)

    plan = session.plan_resume(input_fingerprint="in-1")
    assert plan.reuse_stages[:3] == ("Prepare", "Fetch", "Convert")
    assert plan.reuse_artifacts == ("install.wim",)


def test_size_mismatch_invalidates_artifact(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    session.start(input_fingerprint="in-1")
    path = write_artifact(work)
    session.record_artifact("Convert", "install.wim", path)
    session.end_stage("Convert", StageState.SUCCEEDED)

    path.write_bytes(b"TAMPERED-LONGER-CONTENT")

    assert session.reusable_artifacts() == (), "大小变了就不能复用"


def test_missing_artifact_invalidates_stage(tmp_path: Path) -> None:
    session, work = make_session(tmp_path)
    session.start(input_fingerprint="in-1")
    path = write_artifact(work)
    session.record_artifact("Convert", "install.wim", path)
    session.end_stage("Convert", StageState.SUCCEEDED)
    path.unlink()

    assert session.reusable_artifacts() == (), "产物没了就不能复用"


def test_part_files_are_never_reused(tmp_path: Path) -> None:
    """`.part` 永不参与复用（编排 §6 补充规则）。"""
    session, work = make_session(tmp_path)
    session.start(input_fingerprint="in-1")
    path = write_artifact(work, "install.wim.part")
    session.record_artifact("Convert", "install.wim.part", path)
    session.end_stage("Convert", StageState.SUCCEEDED)

    assert session.reusable_artifacts() == (), ".part 永不参与复用"


def test_skipped_stage_counts_as_reusable(tmp_path: Path) -> None:
    session, _work = make_session(tmp_path)
    session.start(input_fingerprint="in-1")
    session.end_stage("Prepare", StageState.SUCCEEDED)
    session.end_stage("Fetch", StageState.SKIPPED, skip_reason="input_satisfied")
    session.finish(TaskState.CANCELLED)

    plan = session.plan_resume(input_fingerprint="in-1")
    assert plan.reuse_stages == ("Prepare", "Fetch")
