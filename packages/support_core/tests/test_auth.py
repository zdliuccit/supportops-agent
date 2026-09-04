from uuid import uuid4

import pytest
from supportops_core.auth import AuthenticationError, create_access_token, decode_access_token
from supportops_core.config import Settings


def test_access_token_round_trip() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite://",
        jwt_secret="test-secret-with-at-least-thirty-two-characters",
    )
    tenant_id = uuid4()
    user_id = uuid4()
    token = create_access_token(
        settings,
        user_id=user_id,
        tenant_id=tenant_id,
        roles=("employee",),
        display_name="Alice",
    )

    principal = decode_access_token(token, settings)

    assert principal.user_id == user_id
    assert principal.tenant_id == tenant_id
    assert principal.roles == ("employee",)


def test_invalid_token_is_rejected() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite://",
        jwt_secret="test-secret-with-at-least-thirty-two-characters",
    )
    with pytest.raises(AuthenticationError):
        decode_access_token("not-a-token", settings)


def test_production_rejects_default_secret_and_bootstrap() -> None:
    with pytest.raises(ValueError):
        Settings(environment="production")
