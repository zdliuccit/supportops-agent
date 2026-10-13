"""工具结果、审批理由和升级问题共用的凭据样式脱敏。"""

import re
from supportops_core.agent_config import SECRET_PATTERNS

_SENSITIVE_FIELD = re.compile(r'(?i)("?(?:password|secret|access_token|refresh_token|cookie|private_key)"?\s*[:=]\s*)("[^"\n]*"|[^\s,}]+)')


def redact_sensitive_text(value: str) -> str:
    for pattern in SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return _SENSITIVE_FIELD.sub(r'\1"[REDACTED]"', value)
