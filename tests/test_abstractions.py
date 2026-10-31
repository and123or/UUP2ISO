"""接口契约门禁（WP0-T3 验收：接口有类型注解与文档字符串）。

断言四件事：

1. 八个能力接口都是 `Protocol` 且 `runtime_checkable`（组合根可做启动自检）；
2. **每个公开方法都有返回类型注解**（参数注解由 mypy 在实现侧兜底，这里查返回）；
3. **每个公开方法都有文档字符串**（契约靠文档表达，不是靠注释）；
4. 异步/同步的划分符合决策 D3：I/O 一律 ``async def``，纯内存操作为同步。
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from uup2iso.core.abstractions import (
    BuildQuery,
    CancelToken,
    IDownloader,
    IImageEngine,
    IIsoBuilder,
    IOptimizer,
    IPresetStore,
    ISettings,
    IToolProvider,
    IUupSource,
    ProgressSink,
)

# 八个能力接口（架构 §6）+ 两个横切契约
CAPABILITY_PROTOCOLS: tuple[type, ...] = (
    IUupSource,
    IDownloader,
    IToolProvider,
    IImageEngine,
    IOptimizer,
    IIsoBuilder,
    IPresetStore,
    ISettings,
)
SUPPORT_PROTOCOLS: tuple[type, ...] = (CancelToken, ProgressSink)
ALL_PROTOCOLS: tuple[type, ...] = CAPABILITY_PROTOCOLS + SUPPORT_PROTOCOLS


def _public_members(proto: type) -> dict[str, Any]:
    """取 Protocol 上的公开成员（函数与 property）。"""
    members: dict[str, Any] = {}
    for name, value in vars(proto).items():
        if name.startswith("_"):
            continue
        if isinstance(value, property) or inspect.isfunction(value):
            members[name] = value
    return members


def test_capability_protocol_count() -> None:
    """架构 §6 定义 8 个能力接口，少一个就说明有实现无处挂靠。"""
    assert len(CAPABILITY_PROTOCOLS) == 8


def _make_stub(proto: type) -> object:
    """按协议成员动态造一个「鸭子类型」替身实例。

    不能用 ``MagicMock``：Python 3.12 起 ``runtime_checkable`` 的 ``isinstance``
    改用 ``inspect.getattr_static``（避免触发属性副作用），而 MagicMock 的属性靠
    ``__getattr__`` 动态产生，静态查不到，会被判为「不满足协议」。
    """
    namespace: dict[str, Any] = {}

    def _sync(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _async(*_args: Any, **_kwargs: Any) -> None:
        return None

    for name in sorted(getattr(proto, "__protocol_attrs__", ())):
        declared = vars(proto).get(name)
        if isinstance(declared, property):
            namespace[name] = property(lambda self: "")
        elif inspect.iscoroutinefunction(declared):
            namespace[name] = _async
        else:
            namespace[name] = _sync

    return type(f"Stub{proto.__name__}", (), namespace)()


@pytest.mark.parametrize("proto", ALL_PROTOCOLS, ids=lambda p: p.__name__)
def test_protocol_flags(proto: type) -> None:
    """必须是 Protocol、标了 runtime_checkable，且鸭子类型替身能通过 isinstance。"""
    assert getattr(proto, "_is_protocol", False), f"{proto.__name__} 不是 Protocol"
    assert getattr(proto, "_is_runtime_protocol", False), (
        f"{proto.__name__} 未标注 runtime_checkable"
    )
    assert isinstance(_make_stub(proto), proto), f"{proto.__name__} 的替身未通过 isinstance"


@pytest.mark.parametrize("proto", ALL_PROTOCOLS, ids=lambda p: p.__name__)
def test_members_are_documented_and_typed(proto: type) -> None:
    """每个公开成员都必须有文档字符串与返回类型注解。"""
    members = _public_members(proto)
    assert members, f"{proto.__name__} 没有任何公开成员"

    missing_doc = [name for name, member in members.items() if not _doc_of(member)]
    assert not missing_doc, f"{proto.__name__} 缺少文档字符串：{missing_doc}"

    missing_annotation = [
        name for name, member in members.items() if "return" not in _annotations_of(member)
    ]
    assert not missing_annotation, f"{proto.__name__} 缺少返回类型注解：{missing_annotation}"


def _doc_of(member: Any) -> str | None:
    return (member.fget if isinstance(member, property) else member).__doc__


def _annotations_of(member: Any) -> dict[str, Any]:
    return getattr(member.fget if isinstance(member, property) else member, "__annotations__", {})


@pytest.mark.parametrize("proto", ALL_PROTOCOLS, ids=lambda p: p.__name__)
def test_parameters_are_annotated(proto: type) -> None:
    """除 self 外，每个参数都必须有注解（`from __future__ import annotations` 下为字符串）。"""
    problems: list[str] = []
    for name, member in _public_members(proto).items():
        if isinstance(member, property):
            continue
        signature = inspect.signature(member)
        for param in signature.parameters.values():
            if param.name == "self":
                continue
            if param.annotation is inspect.Parameter.empty:
                problems.append(f"{name}({param.name})")
    assert not problems, f"{proto.__name__} 参数缺注解：{problems}"


def test_io_methods_are_async() -> None:
    """决策 D3：I/O 接口必须是协程函数（GUI框架 §9：所有 I/O 异步）。"""
    async_members: dict[type, tuple[str, ...]] = {
        IUupSource: ("list_builds", "get_build", "build_manifest"),
        IDownloader: ("download",),
        IToolProvider: ("ensure",),
        IImageEngine: ("get_info", "export", "mount", "unmount", "verify"),
        IOptimizer: ("reclaim", "apply_tweaks", "compact"),
        IIsoBuilder: ("build",),
        IPresetStore: ("list", "load", "save", "export_", "import_"),
        ISettings: ("save",),
    }
    for proto, names in async_members.items():
        for name in names:
            member = vars(proto)[name]
            assert inspect.iscoroutinefunction(member), f"{proto.__name__}.{name} 应为 async"


def test_pure_queries_are_sync() -> None:
    """纯内存/纯查询不引入协程开销，避免调用方被迫 `await`。"""
    sync_members: dict[type, tuple[str, ...]] = {
        IToolProvider: ("locate", "capabilities", "is_available"),
        ISettings: ("get", "set", "as_mapping"),
    }
    for proto, names in sync_members.items():
        for name in names:
            member = vars(proto)[name]
            assert not inspect.iscoroutinefunction(member), f"{proto.__name__}.{name} 不应是 async"


def test_downloader_accepts_progress_and_cancel() -> None:
    """长任务接口必须能接进度与取消（日志 §5.2、编排 §8）。"""
    signature = inspect.signature(IDownloader.download)
    assert "progress" in signature.parameters
    assert "cancel" in signature.parameters
    assert signature.parameters["progress"].default is None
    assert signature.parameters["cancel"].default is None


def test_cancel_token_contract() -> None:
    """取消令牌必须有「查询」与「抛错」两种用法（编排 §8）。"""
    names = set(_public_members(CancelToken))
    assert {"is_cancelled", "raise_if_cancelled"} <= names


def test_progress_sink_marks_percent_optional() -> None:
    """进度百分比允许未知（不确定态进度条），默认值为 None。"""
    signature = inspect.signature(ProgressSink.report)
    assert signature.parameters["percent"].default is None


def test_build_query_defaults() -> None:
    """筛选项默认全空 = 不过滤；limit 有默认值（下载 §2.1）。"""
    query = BuildQuery()
    assert query.channel is None
    assert query.arch is None
    assert query.language is None
    assert query.keyword is None
    assert query.limit == 50


def test_protocols_are_not_instantiable_implementations() -> None:
    """Protocol 不能被实例化——防止有人把接口当默认实现直接用。"""
    with pytest.raises(TypeError):
        IUupSource()  # type: ignore[misc]
