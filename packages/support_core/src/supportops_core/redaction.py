"""工具结果、审批理由和升级问题共用的凭据样式脱敏。"""

import re

from supportops_core.agent_config import API_KEY_VALUE_PATTERN

_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?"
    r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|\Z)"
)
_SENSITIVE_NAMES = (
    r"password|secret|client[_-]?secret|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|session[_-]?token|auth[_-]?token|cookie|set-cookie|"
    r"private[_-]?key|(?:x[-_])?api[_-]?key|authorization|proxy-authorization|bearer"
)
_FIELD_VALUE = r'''(?:"(?:\\.|[^"\\])*(?:"|$)|'(?:\\.|[^'\\])*(?:'|$)|(?:Bearer|Basic)[ \t]+[^\s,;}\]>"']+|[^\s,;}\]>"']+)'''
_SENSITIVE_FIELD = re.compile(
    rf'''(?i)(?<![\w-])(["']?(?:{_SENSITIVE_NAMES})["']?\s*[:=]\s*)''' + _FIELD_VALUE
)
_CREDENTIAL_HEADER = re.compile(
    r'''(?im)(?<![\w-])((?:proxy-authorization|authorization|set-cookie|cookie)'''
    r'''[ \t]*[:=][ \t]*)([^\s"'][^\r\n]*)'''
)
_AUTH_SCHEME = re.compile(r"(?i)\b(Bearer|Basic)[ \t]+[A-Za-z0-9._~+/-]+=*")


def redact_sensitive_text(value: str, *, secrets: tuple[str, ...] = ()) -> str:
    """先处理完整认证字段，避免只遮掉Bearer后残留真正令牌。"""
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    value = _PRIVATE_KEY.sub("[REDACTED]", value)
    # Cookie中的分号和认证方案中的空格都属于完整头值，不能只隐藏第一段。
    value = _CREDENTIAL_HEADER.sub(r'\1"[REDACTED]"', value)
    value = _SENSITIVE_FIELD.sub(r'\1"[REDACTED]"', value)
    value = _AUTH_SCHEME.sub(r"\1 [REDACTED]", value)
    return API_KEY_VALUE_PATTERN.sub("[REDACTED]", value)
