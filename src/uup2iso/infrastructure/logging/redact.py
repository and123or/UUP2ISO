"""脱敏（日志 §5.5，强制）。

三类内容必须处理，且**在写入前**完成（含审计与诊断包）：

| 内容 | 处理 |
| --- | --- |
| 代理账号/密码 | `user=*** password=***`；`user:pass@host` 里的口令替换为 `***` |
| 产品密钥 | 仅留最后一组：`XXXXX-XXXXX-XXXXX-XXXXX-ABCDE` |
| URL 签名参数 | 删除 query 里的 `sig`/`token`/`skoid`/`skt`/`ske`/`skv` 等 |

脱敏是**幂等**的：重复调用不会把 ``***`` 再加工一遍。
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["SENSITIVE_QUERY_KEYS", "redact_text", "redact_url"]

# 口径统一成「键=***」：键名可能带前缀（proxy-password / proxy_user），值到空白或 @ & | 为止。
# 负向后顾排除 URL 内部（`http://user:***@host` 里的 `user:` 不该被再加工）。
_CREDENTIAL = re.compile(
    r"(?i)(?<![:\w/])((?:[\w-]*(?:pass|passwd|pwd|secret|token)[\w-]*|user|username))"
    r"\s*[:=]\s*([^\s@&|]+)"
)
# 口令里可能含 @（未编码时），所以第三段用贪婪匹配、以最后一个 @ 作为界。
_URL_CREDENTIALS = re.compile(r"(?i)(https?://)([^\s:/@]+):([^\s/]+)@")
_PRODUCT_KEY = re.compile(r"\b([A-Z0-9]{5}(?:-[A-Z0-9]{5}){4})\b")

SENSITIVE_QUERY_KEYS: frozenset[str] = frozenset(
    {"sig", "signature", "token", "access_token", "skoid", "skt", "ske", "skv", "sks", "sp", "se"}
)
"""URL query 中必须删除的参数（引擎 §9.1 的签名串也在这里）。"""


def redact_url(url: str) -> str:
    """删除 URL query 中的签名/令牌参数，仅保留 host + path + 其它参数。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url

    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in SENSITIVE_QUERY_KEYS
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def redact_text(text: str) -> str:
    """对一行文本做全部脱敏（幂等）。"""
    if not text:
        return text

    result = _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}{m.group(2)}:***@", text)
    result = _CREDENTIAL.sub(lambda m: f"{m.group(1)}=***", result)
    result = _PRODUCT_KEY.sub(lambda m: f"XXXXX-XXXXX-XXXXX-XXXXX-{m.group(1)[-5:]}", result)

    # 文本里出现的 URL 也要去掉签名参数
    def _strip(match: re.Match[str]) -> str:
        return redact_url(match.group(0))

    return re.sub(r"https?://[^\s|]+", _strip, result)
