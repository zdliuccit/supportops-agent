"""模型 API Key 的密封存储抽象及本地信封加密实现。"""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING, Protocol

from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from supportops_core.config import Settings


class SecretProviderError(RuntimeError):
    """密钥材料不合法或无法完成加解密。"""

    pass


class SecretProvider(Protocol):
    """凭据密封服务接口，便于后续替换为 KMS 或企业密钥服务。"""

    provider_name: str

    def seal(self, secret: str) -> str: ...

    def open(self, sealed_secret: str) -> str: ...


class LocalEnvelopeSecretProvider:
    """使用实例主密钥派生 Fernet 密钥的本地开发实现。"""

    provider_name = "local_envelope_v1"

    def __init__(self, master_key: str) -> None:
        """从配置主密钥稳定派生对称加密密钥。"""

        digest = hashlib.sha256(master_key.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def seal(self, secret: str) -> str:
        """加密并编码明文凭据，供数据库安全持久化。"""

        value = secret.strip()
        if len(value) < 8:
            raise SecretProviderError("模型 API Key 长度不足")
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def open(self, sealed_secret: str) -> str:
        """解密持久化凭据；密钥不匹配或内容损坏时统一报错。"""

        try:
            return self._fernet.decrypt(sealed_secret.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError) as exc:
            raise SecretProviderError("模型凭据无法解密") from exc


def create_secret_provider(settings: Settings) -> SecretProvider:
    """API、推理与独立探测共用显式 provider 策略；未知实现不能静默降级。"""
    if settings.secret_provider == LocalEnvelopeSecretProvider.provider_name:
        return LocalEnvelopeSecretProvider(settings.secret_encryption_key)
    raise SecretProviderError("配置的秘密提供者尚未实现，不能自动改用本地密封")


def masked_secret_hint(secret: str) -> str:
    """仅保留凭据末四位，生成可展示但不可用于认证的提示。"""

    value = secret.strip()
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"
