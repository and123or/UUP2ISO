"""解析转换器产物文件名（`<build>.<rev>.<date>-<time>.<tag>_<TYPE>_<EDITION>_<CHANNEL>_<ARCH>FRE_<LANG>`）。

为什么值得解析：这些字段是**镜像自己**的信息（转换器从 `install.wim` 元数据拼出来的），
比配置里手写的更可信。实测样本：

```
26100.1.240331-1435.GE_RELEASE_CLIENTPRO_OEMRET_X64FRE_ZH-CN.ISO
└ build  └ rev └ date-time └tag └ type  └ edition  └channel └arch └lang
```

只做**保守映射**：认得的 token 翻成我们的取值，认不得的原样小写保留——
宁可给出 `clientpro`，也不退回 `unknown`，更不猜。

注意：`build` 是**镜像基线**（未集成 LCU 时等于原始 build，例如 26100），
与"数据源构建号"（如 26300.9818）是两个概念，不能互相覆盖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["ImageNameInfo", "parse_converter_iso_name"]

# 两种实测格式都要支持：
#   26100.1.240331-1435.GE_RELEASE_CLIENTPRO_OEMRET_X64FRE_ZH-CN          （未集成）
#   26300.9818.261025-0959.26H2_GE_RELEASE_SVC_PROD3_CLIENTPRO_OEMRET_X64FRE_ZH-CN（集成后）
# 差别：多一个版本族标签（26H2），且发布类型可带下划线（SVC_PROD3）。
# 用「右锚定」解析：channel/arch/lang 固定在尾部，向左回溯切出 edition，其余归 prefix。
_PATTERN = re.compile(
    r"^(?P<build>\d{5})\.(?P<revision>\d+)\.(?P<date>\d{6})-(?P<time>\d{4})\."
    r"(?P<prefix>.+?)_(?P<edition>[A-Za-z0-9]+)_(?P<channel>OEMRET|VOL)_"
    r"(?P<arch>X64|X86|ARM64)FRE_(?P<lang>[A-Za-z]{2}-[A-Za-z]{2})$",
    re.IGNORECASE,
)
_FAMILY_IN_PREFIX = re.compile(r"^(?P<family>\d{2}H\d)_", re.IGNORECASE)

_ARCH = {"X64": "x64", "X86": "x86", "ARM64": "arm64"}

_EDITION = {
    "CLIENTPRO": "professional",
    "PRO": "professional",
    "PROFESSIONALVL": "professional",
    "CLIENTCORE": "home",
    "CORE": "home",
    "COREN": "home_n",
    "PROFESSIONALN": "professional_n",
    "PROEDUCATION": "professional_education",
    "PROEDUCATIONN": "professional_education_n",
    "PROWORKSTATION": "professional_workstation",
    "PROWORKSTATIONN": "professional_workstation_n",
    "CLOUD": "cloud",
    "CLOUDN": "cloud_n",
    "STANDARD": "standard",
    "STANDARDCORE": "standard_core",
    "ENTERPRISE": "enterprise",
    "EDUCATION": "education",
}


@dataclass(frozen=True, slots=True)
class ImageNameInfo:
    """从产物文件名解析出的镜像信息（字段缺失为空串）。"""

    build: str = ""
    revision: str = ""
    date: str = ""
    family: str = ""
    """版本族标签（如 `26H2`），仅集成后的产物名才带。"""
    tag: str = ""
    release_type: str = ""
    edition_token: str = ""
    edition: str = ""
    channel: str = ""
    arch: str = ""
    language: str = ""

    @property
    def build_revision(self) -> str:
        """`26100.1` 这种「主版本.修订」串（产物名的 `{build}` 用它）。"""
        if not (self.build and self.revision):
            return ""
        return f"{self.build}.{self.revision}"

    @property
    def baseline(self) -> str:
        """`26100.1.240331-1435` 这种基线串（不含类型/版本信息）。"""
        if not (self.build and self.revision and self.date):
            return ""
        return f"{self.build}.{self.revision}.{self.date}"

    def summary(self) -> str:
        parts = [item for item in (self.edition, self.arch, self.language, self.channel) if item]
        return "/".join(parts)


def parse_converter_iso_name(name: str) -> ImageNameInfo | None:
    """解析转换器产物名；不符合该命名规则时返回 ``None``（调用方保持原值）。"""
    stem = name
    for suffix in (".iso", ".esd", ".wim"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    match = _PATTERN.match(stem)
    if match is None:
        return None

    prefix = match.group("prefix")
    family = ""
    family_match = _FAMILY_IN_PREFIX.match(prefix)
    if family_match is not None:
        family = family_match.group("family").upper()
        prefix = prefix[family_match.end() :]
    parts = prefix.split("_", 1)
    tag = parts[0].upper()
    release_type = (parts[1] if len(parts) > 1 else "").upper()

    edition_token = match.group("edition").upper()
    return ImageNameInfo(
        build=match.group("build"),
        revision=match.group("revision"),
        date=f"{match.group('date')}-{match.group('time')}",
        family=family,
        tag=tag,
        release_type=release_type,
        edition_token=edition_token,
        edition=_EDITION.get(edition_token, edition_token.lower()),
        channel=match.group("channel").upper(),
        arch=_ARCH.get(match.group("arch").upper(), match.group("arch").lower()),
        language=match.group("lang").lower(),
    )
