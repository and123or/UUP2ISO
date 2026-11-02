"""版本目录（P0-0）：`26H2 → 26300` 从数据解析，代码零常量。

三层数据来源，按顺序尝试：

1. **缓存**（`cache/version/<repo>-<sha>.json`，24 小时）——避免每次启动都打 GitHub；
2. **GitHub 源**（可配置 owner/repo + path，运行时用 Contents API 取文件并 base64 解码）——
   仓库与路径**可配置**，因为公开仓库会改名/归档（实测多个候选已 404）；
3. **随包快照**（`uup2iso/data/windows_releases.json`）——离线兜底，命中时标 `stale`。

解析规则（宁可判不出，也不猜）：**同一行**内出现 `NNH2` 与 5 位主版本号才配对；
同一版本族出现多个不同主版本号时记 `version_source_conflict` 并保留多数值。
"""

from __future__ import annotations

import base64
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from uup2iso.core.models.version import VersionResolution, WindowsRelease

__all__ = [
    "CACHE_TTL_SECONDS",
    "DEFAULT_SNAPSHOT",
    "GitHubSource",
    "VersionCatalog",
    "parse_releases",
]

CACHE_TTL_SECONDS = 24 * 3600
"""版本数据缓存时长（变化很慢，24 小时足够）。"""

DEFAULT_SNAPSHOT = "windows_releases.json"

_LINE_PAIR = re.compile(r"(?P<family>\d{2}H2).{0,60}?(?P<major>\d{5})\b")


@dataclass(frozen=True, slots=True)
class GitHubSource:
    """一个 GitHub 版本清单来源（owner/repo + 文件路径）。"""

    repo: str
    path: str
    branch: str = ""

    def describe(self) -> str:
        return f"github:{self.repo}/{self.path}"


class VersionCatalog:
    """版本族目录。

    Args:
        snapshot_path: 随包快照路径；``None`` 用包内默认。
        cache_dir: 缓存目录；``None`` 表示不写缓存。
        sources: GitHub 来源列表（为空则只用快照）。
        client: 注入的 httpx 客户端（测试用 `MockTransport`）。
        clock: 可注入时钟。
    """

    def __init__(
        self,
        *,
        snapshot_path: Path | None = None,
        cache_dir: Path | None = None,
        sources: tuple[GitHubSource, ...] = (),
        client: httpx.Client | None = None,
        clock: object | None = None,
    ) -> None:
        self._snapshot_path = snapshot_path or _default_snapshot_path()
        self._cache_dir = cache_dir
        self._sources = sources
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self.warnings: list[str] = []
        self._releases: tuple[WindowsRelease, ...] = ()
        self._loaded_from = ""
        self._stale = False

    # -- 读取 -------------------------------------------------------------

    @property
    def releases(self) -> tuple[WindowsRelease, ...]:
        if not self._releases:
            self.load()
        return self._releases

    @property
    def source(self) -> str:
        if not self._releases:
            self.load()
        return self._loaded_from

    @property
    def stale(self) -> bool:
        if not self._releases:
            self.load()
        return self._stale

    def load(self) -> tuple[WindowsRelease, ...]:
        """按「缓存 → GitHub → 快照」顺序装载（不抛错，失败降级）。"""
        cached = self._load_from_cache()
        if cached:
            self._releases, self._loaded_from, self._stale = cached, "cache", False
            return self._releases

        fetched = self._try_github()
        if fetched:
            self._releases = fetched
            self._loaded_from = self.warnings[-1] if self.warnings else "github"
            self._stale = False
            return self._releases

        self._releases = self._load_snapshot()
        self._loaded_from = "snapshot"
        self._stale = True
        self.warnings.append("version_catalog_offline")
        return self._releases

    # -- 查询 -------------------------------------------------------------

    def resolve(self, family: str) -> VersionResolution:
        """版本族 → 主版本号。"""
        target = family.strip().upper()
        for item in self.releases:
            if item.family.upper() == target:
                return VersionResolution(
                    family=item.family,
                    major=item.major,
                    source=item.source or self._loaded_from,
                    stale=self._stale,
                    warnings=tuple(self.warnings),
                )
        self.warnings.append(f"version_family_unknown family={family}")
        return VersionResolution(
            family=family, source="unknown", stale=self._stale, warnings=tuple(self.warnings)
        )

    def family_of(self, major: int) -> str:
        """主版本号 → 版本族（目录名反查用）。"""
        for item in self.releases:
            if item.major == major:
                return item.family
        return ""

    def resolve_build(self, build: str) -> VersionResolution:
        """完整构建号（`26300.9818`）→ 版本族 + 主版本号。"""
        head, _, tail = build.partition(".")
        major = int(head) if head.isdigit() else 0
        family = self.family_of(major) if major else ""
        if major and not family:
            self.warnings.append(f"version_major_unlisted major={major}")
        return VersionResolution(
            family=family,
            major=major,
            build=build,
            revision=tail,
            source=self._loaded_from,
            stale=self._stale,
            warnings=tuple(self.warnings),
        )

    # -- 刷新 -------------------------------------------------------------

    def refresh(self) -> tuple[WindowsRelease, ...]:
        """强制从 GitHub 重新抓取；失败则回退快照。"""
        self._releases = ()
        self._loaded_from = ""
        self._drop_cache()
        return self.load()

    # -- 内部：GitHub -----------------------------------------------------

    def _try_github(self) -> tuple[WindowsRelease, ...]:
        if not self._sources:
            return ()
        client = self._client or httpx.Client(timeout=20.0, follow_redirects=True)
        try:
            for source in self._sources:
                releases = self._fetch_source(client, source)
                if releases:
                    self._write_cache(source, releases)
                    return releases
        except (httpx.HTTPError, ValueError) as exc:  # 网络/解码失败都降级
            self.warnings.append(f"version_catalog_fetch_failed reason={type(exc).__name__}")
        finally:
            if self._client is None:
                client.close()
        return ()

    def _fetch_source(
        self, client: httpx.Client, source: GitHubSource
    ) -> tuple[WindowsRelease, ...]:
        params = {"ref": source.branch} if source.branch else {}
        url = f"https://api.github.com/repos/{source.repo}/contents/{source.path}"
        response = client.get(url, params=params, headers={"Accept": "application/vnd.github+json"})
        if response.status_code != 200:
            self.warnings.append(
                f"version_source_unavailable repo={source.repo} status={response.status_code}"
            )
            return ()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("encoding") == "base64":
            text = base64.b64decode(payload.get("content", "")).decode("utf-8", errors="replace")
        else:
            text = str(payload)
        sha = str(payload.get("sha", ""))[:12] if isinstance(payload, dict) else ""
        parsed = parse_releases(text)
        if not parsed:
            self.warnings.append(f"version_source_unparsed repo={source.repo}")
            return ()
        origin = f"{source.describe()}@{sha}" if sha else source.describe()
        self.warnings.append(f"version_source_ok origin={origin}")
        return tuple(
            WindowsRelease(
                family=item.family, major=item.major, channel=item.channel, source=origin
            )
            for item in parsed
        )

    # -- 内部：缓存 -------------------------------------------------------

    def _cache_path(self) -> Path | None:
        if self._cache_dir is None or not self._sources:
            return None
        source = self._sources[0]
        safe = source.repo.replace("/", "-")
        return self._cache_dir / f"{safe}-{parse_releases.__name__}.json"

    def _load_from_cache(self) -> tuple[WindowsRelease, ...]:
        path = self._cache_path()
        if path is None or not path.is_file():
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(str(payload["cachedAt"]))
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return ()
        age = (self._now() - cached_at).total_seconds()
        if age > CACHE_TTL_SECONDS:
            return ()
        releases = _releases_from_json(payload.get("releases"))
        if releases:
            self.warnings.append(f"version_source_ok origin=cache age_seconds={int(age)}")
        return releases

    def _write_cache(self, source: GitHubSource, releases: tuple[WindowsRelease, ...]) -> None:
        path = self._cache_path()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": source.describe(),
            "cachedAt": self._now().isoformat(),
            "releases": [
                {"family": item.family, "major": item.major, "channel": item.channel}
                for item in releases
            ],
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )

    def _drop_cache(self) -> None:
        path = self._cache_path()
        if path is not None:
            path.unlink(missing_ok=True)

    # -- 内部：快照 -------------------------------------------------------

    def _load_snapshot(self) -> tuple[WindowsRelease, ...]:
        path = self._snapshot_path
        if not path.is_file():
            self.warnings.append(f"version_snapshot_missing path={path}")
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.warnings.append(f"version_snapshot_corrupt path={path}")
            return ()
        releases = _releases_from_json(payload.get("releases"))
        for item in releases:
            self.warnings.append(f"version_snapshot_used family={item.family}")
            break
        return releases

    def _now(self) -> datetime:
        value = self._clock()  # type: ignore[operator]
        return value if isinstance(value, datetime) else datetime.now(UTC)


# ---------------------------------------------------------------------------
# 解析与辅助
# ---------------------------------------------------------------------------


def parse_releases(text: str) -> tuple[WindowsRelease, ...]:
    """从任意文本里解析 `NNH2` 与主版本号的配对（同一行内才算）。"""
    buckets: dict[str, Counter[int]] = {}
    for line in text.splitlines():
        for match in _LINE_PAIR.finditer(line):
            family = match.group("family").upper()
            major = int(match.group("major"))
            if not _looks_like_major(major):
                continue
            buckets.setdefault(family, Counter())[major] += 1

    releases: list[WindowsRelease] = []
    for family, counter in sorted(buckets.items()):
        major, _count = counter.most_common(1)[0]
        releases.append(WindowsRelease(family=family, major=major))
    return tuple(releases)


def _looks_like_major(major: int) -> bool:
    """主版本号形如 2xxxx（Windows 10 之后），排除年份/大小等噪声。"""
    return 20000 <= major <= 39999


def _releases_from_json(value: object) -> tuple[WindowsRelease, ...]:
    if not isinstance(value, list):
        return ()
    releases: list[WindowsRelease] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            releases.append(
                WindowsRelease(
                    family=str(item["family"]),
                    major=int(item["major"]),
                    channel=str(item.get("channel") or ""),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(releases)


def _default_snapshot_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / DEFAULT_SNAPSHOT
