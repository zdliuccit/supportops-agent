"""本地账号密码策略与版本化 scrypt 单向哈希。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

SCRYPT_VERSION = "1"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
DERIVED_KEY_BYTES = 32


class PasswordPolicyError(ValueError):
    """密码不满足平台最小安全要求。"""


def validate_password(password: str) -> None:
    """要求密码具备可接受长度，避免脆弱口令和异常大输入。"""
    if len(password) < 10:
        raise PasswordPolicyError("密码至少需要 10 个字符")
    if len(password) > 256:
        raise PasswordPolicyError("密码不能超过 256 个字符")
    if password.isspace():
        raise PasswordPolicyError("密码不能只包含空白字符")


def hash_password(password: str) -> str:
    """使用独立随机盐生成可升级的 scrypt 密码哈希字符串。"""
    validate_password(password)
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=DERIVED_KEY_BYTES,
    )
    return "$".join(
        (
            "scrypt",
            SCRYPT_VERSION,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    """解析并常量时间比较密码哈希；损坏或未知格式统一视为不匹配。"""
    try:
        algorithm, version, n, r, p, salt_value, expected_value = encoded_hash.split("$")
        if algorithm != "scrypt" or version != SCRYPT_VERSION:
            return False
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_value.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeError):
        return False
