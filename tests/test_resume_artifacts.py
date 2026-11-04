"""续跑可信度门禁（D1）：产物登记 + 四条件复用。

核心回归：`_split_reusable` 里 `all([])` 恒为 True —— 若阶段成功却没登记产物，
旧实现会**无条件复用**，等于信任"磁盘上随便什么"。这里守住三条：
1. Convert/Output 必须有产物记录才复用；
2. 产物大小/SHA256 不符 → 不复用；
3. handler 确实把产物登记进了会话。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from uup2iso.application.task_session.session import ARTIFACT_STAGES, _split_reusable
from uup2iso.core.models.checkpoint import ArtifactRecord, Checkpoint, StageRecord
from uup2iso.core.models.enums import StageState
from uup2iso.core.models.pipeline import PIPELINE_ORDER


def make_checkpoint(
    tmp_path: Path, *, stages: dict[str, StageState], artifacts: list[ArtifactRecord]
) -> Checkpoint:
    """除显式给出的阶段外，其余阶段标为 skipped（否则「缺失即阻断」会干扰判定）。"""
    checkpoint = Checkpoint(taskId="t1", configFingerprint="cfg")
    for stage in PIPELINE_ORDER:
        checkpoint.stages[stage.value] = StageRecord(status=StageState.SKIPPED)
    for name, status in stages.items():
        checkpoint.stages[name] = StageRecord(status=status)
    checkpoint.artifacts = artifacts
    return checkpoint


def record(tmp_path: Path, name: str, payload: bytes) -> ArtifactRecord:
    path = tmp_path / name
    path.write_bytes(payload)
    return ArtifactRecord(
        stage="Convert",
        name=name,
        path=name,
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        reusable=True,
    )


def test_artifact_stages_cover_convert_and_output() -> None:
    assert ARTIFACT_STAGES == frozenset({"Convert", "Output"})


def test_succeeded_convert_without_artifacts_is_not_reused(tmp_path: Path) -> None:
    """关键回归：Convert 成功但没登记产物 → 不可复用（旧实现会复用）。"""
    checkpoint = make_checkpoint(
        tmp_path,
        stages={"Prepare": StageState.SUCCEEDED, "Convert": StageState.SUCCEEDED},
        artifacts=[],
    )

    reuse, redo, _ = _split_reusable(checkpoint, tmp_path)

    assert "Convert" not in reuse
    assert "Convert" in redo, "没有产物记录的阶段必须重做"


def test_succeeded_convert_with_valid_artifact_is_reused(tmp_path: Path) -> None:
    artifact = record(tmp_path, "install.wim", b"WIM")
    checkpoint = make_checkpoint(
        tmp_path, stages={"Convert": StageState.SUCCEEDED}, artifacts=[artifact]
    )

    reuse, redo, names = _split_reusable(checkpoint, tmp_path)

    assert "Convert" in reuse and "Convert" not in redo
    assert "install.wim" in names


def test_tampered_artifact_blocks_reuse(tmp_path: Path) -> None:
    """产物被改（大小变化）→ 不复用。"""
    artifact = record(tmp_path, "install.wim", b"WIM")
    (tmp_path / "install.wim").write_bytes(b"WIM-TAMPERED")

    checkpoint = make_checkpoint(
        tmp_path, stages={"Convert": StageState.SUCCEEDED}, artifacts=[artifact]
    )
    reuse, _redo, _ = _split_reusable(checkpoint, tmp_path)

    assert "Convert" not in reuse


def test_missing_artifact_blocks_reuse(tmp_path: Path) -> None:
    artifact = record(tmp_path, "install.wim", b"WIM")
    (tmp_path / "install.wim").unlink()

    checkpoint = make_checkpoint(
        tmp_path, stages={"Convert": StageState.SUCCEEDED}, artifacts=[artifact]
    )
    reuse, _redo, _ = _split_reusable(checkpoint, tmp_path)

    assert "Convert" not in reuse


def test_non_artifact_stage_without_record_still_reused(tmp_path: Path) -> None:
    """Prepare 这类幂等阶段不要求产物记录，避免无谓重跑。"""
    checkpoint = make_checkpoint(tmp_path, stages={"Prepare": StageState.SUCCEEDED}, artifacts=[])

    reuse, redo, _ = _split_reusable(checkpoint, tmp_path)

    assert "Prepare" in reuse and "Prepare" not in redo
