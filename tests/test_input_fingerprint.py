"""输入指纹门禁（D2）：换输入源必须被续跑判定发现。

之前 `input_fingerprint` 恒为空串 → `plan_resume` 里「输入已变更」这条**永不触发**，
换一份 UUP 源仍会被判可续跑，复用旧源的 install.wim。
"""

from __future__ import annotations

from pathlib import Path

from uup2iso.infrastructure.input.fingerprint import folder_fingerprint, paths_fingerprint


def make_folder(root: Path) -> Path:
    folder = root / "uups"
    folder.mkdir()
    (folder / "a.esd").write_bytes(b"ESD")
    (folder / "AggregatedMetadata.cab").write_bytes(b"CAB")
    nested = folder / "Apps" / "Contoso"
    nested.mkdir(parents=True)
    (nested / "p.msix").write_bytes(b"MSIX")
    return folder


def test_stable_for_same_tree(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    assert folder_fingerprint(folder) == folder_fingerprint(folder)


def test_changes_when_file_added(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    before = folder_fingerprint(folder)
    (folder / "new.cab").write_bytes(b"NEW")
    assert folder_fingerprint(folder) != before


def test_changes_when_size_changes(tmp_path: Path) -> None:
    folder = make_folder(tmp_path)
    before = folder_fingerprint(folder)
    (folder / "a.esd").write_bytes(b"ESD-LONGER")
    assert folder_fingerprint(folder) != before


def test_detects_nested_changes(tmp_path: Path) -> None:
    """转换器会把 appx 收进子目录，子目录变化也要影响指纹。"""
    folder = make_folder(tmp_path)
    before = folder_fingerprint(folder)
    (folder / "Apps" / "Contoso" / "p.msix").write_bytes(b"MSIX-2")
    assert folder_fingerprint(folder) != before


def test_missing_folder_is_empty(tmp_path: Path) -> None:
    assert folder_fingerprint(tmp_path / "nope") == ""


def test_paths_fingerprint_order_independent(tmp_path: Path) -> None:
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"A")
    b.write_bytes(b"B")
    assert paths_fingerprint([a, b]) == paths_fingerprint([b, a])


# --- 与续跑判定联动 ---------------------------------------------------------


def test_resume_rejects_changed_input(tmp_path: Path) -> None:
    """检查点记录的输入指纹与当前不一致 → 拒绝续跑。"""
    from uup2iso.application.task_session.session import TaskSession
    from uup2iso.infrastructure.storage import CheckpointStore

    work = tmp_path / "work"
    work.mkdir()
    session = TaskSession(
        task_id="t1", work_dir=work, config_fingerprint="cfg", store=CheckpointStore(work)
    )
    session.start(input_fingerprint="old-input")
    session.finish  # noqa: B018 - 仅保持状态为 running

    plan = session.plan_resume(input_fingerprint="new-input")

    assert plan.decision.value == "reject_input_changed"
