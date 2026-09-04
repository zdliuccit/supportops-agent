import pytest
from supportops_core.passwords import PasswordPolicyError, hash_password, verify_password


def test_scrypt_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("Correct-password-123")
    second = hash_password("Correct-password-123")
    assert first != second
    assert first.startswith("scrypt$1$")
    assert verify_password("Correct-password-123", first)
    assert not verify_password("Wrong-password-123", first)


def test_short_password_is_rejected() -> None:
    with pytest.raises(PasswordPolicyError):
        hash_password("short")
