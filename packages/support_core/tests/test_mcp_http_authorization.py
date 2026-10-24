"""HTTP MCP 授权编排的隔离契约；broker/审计均为显式内存替身。"""

import asyncio
import base64
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import SecretStr, ValidationError
from supportops_core.mcp_http_authorization import (
    HttpMcpAuthorizationError,
    HttpMcpTokenGrant,
    revoke_http_mcp_lease,
    run_authorized_http_mcp_call,
)
from supportops_core.mcp_http_authorization_contracts import (
    HttpMcpAuthorizationAudit,
    HttpMcpAuthorizationPolicy,
    HttpMcpCallBinding,
    HttpMcpCredentialRequest,
    HttpMcpTokenState,
)
from supportops_core.mcp_http_jwt import (
    HttpMcpJwtVerificationPolicy,
    HttpMcpRsaVerificationKey,
    VerifiedJwtHttpMcpCredentialBroker,
    verify_http_mcp_jwt,
)

PLATFORM_ISSUER = "supportops-test"
PLATFORM_AUDIENCE = "supportops-api-test"


@pytest.fixture
def credential_request() -> HttpMcpCredentialRequest:
    expires = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=120)
    tenant_id = uuid4()
    return HttpMcpCredentialRequest(
        policy=HttpMcpAuthorizationPolicy(
            tenant_id=tenant_id,
            source_key="isolated-test",
            source_record_id=uuid4(),
            source_policy_digest="1" * 64,
            provider_id="mock-broker",
            credential_id=uuid4(),
            credential_revision=1,
            issuer="https://identity.example.invalid",
            resource="https://mcp.example.invalid/tools",
            server_name="isolated-test",
            server_version="1",
            scopes=("tools:read",),
            valid_until=expires,
        ),
        binding=HttpMcpCallBinding(
            tenant_id=tenant_id,
            actor_user_id=uuid4(),
            run_id=uuid4(),
            attempt_id=uuid4(),
            generation=1,
            invocation_id=uuid4(),
            dispatch_id=uuid4(),
            tool_id="mcp_test",
            implementation_version=1,
            descriptor_digest="2" * 64,
            arguments_digest="3" * 64,
            expires_at=expires,
        ),
    )


def token_grant(
    request: HttpMcpCredentialRequest, token: str = "isolated-test-opaque-token"
) -> HttpMcpTokenGrant:
    issued = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=1)
    return HttpMcpTokenGrant(
        state=HttpMcpTokenState(
            lease_id=uuid4(),
            request_digest=request.request_digest,
            provider_id=request.policy.provider_id,
            credential_id=request.policy.credential_id,
            credential_revision=request.policy.credential_revision,
            tenant_id=request.binding.tenant_id,
            actor_user_id=request.binding.actor_user_id,
            issuer=request.policy.issuer,
            audiences=(request.policy.resource,),
            scopes=request.policy.scopes,
            token_digest=hashlib.sha256(token.encode()).hexdigest(),
            issued_at=issued,
            expires_at=issued + timedelta(seconds=60),
        ),
        access_token=SecretStr(token),
    )


class MemoryBroker:
    """仅用于验证调用顺序，不实现真实领取、签发、撤销或持久化。"""

    provider_id = "mock-broker"

    def __init__(self, request: HttpMcpCredentialRequest, grant: HttpMcpTokenGrant | None = None):
        self.grant = grant or token_grant(request)
        self.state = self.grant.state
        self.claims = self.revocations = self.checks = 0
        self.allowed = True
        self.revoke_effect = True

    async def assert_current_permit(self, request: HttpMcpCredentialRequest) -> None:
        self.checks += 1
        if not self.allowed:
            raise HttpMcpAuthorizationError("MCP_HTTP_AUTHORIZATION_CHANGED")

    async def claim(self, request: HttpMcpCredentialRequest) -> HttpMcpTokenGrant:
        self.claims += 1
        return self.grant

    async def current(self, request: HttpMcpCredentialRequest, lease_id: UUID) -> HttpMcpTokenState:
        assert lease_id == self.state.lease_id
        return self.state

    async def revoke(self, request: HttpMcpCredentialRequest, lease_id: UUID) -> None:
        assert lease_id == self.state.lease_id
        self.revocations += 1
        if self.revoke_effect and self.state.revoked_at is None:
            self.state = self.state.model_copy(update={"revoked_at": datetime.now(UTC)})


class MemoryAudit:
    def __init__(self):
        self.events: list[HttpMcpAuthorizationAudit] = []
        self.on_record: Callable[[HttpMcpAuthorizationAudit], None] = lambda _: None

    async def record(self, event: HttpMcpAuthorizationAudit) -> None:
        self.events.append(event)
        self.on_record(event)


async def call(
    request: HttpMcpCredentialRequest,
    broker: MemoryBroker | None,
    audit: MemoryAudit | None,
    operation: Callable[[SecretStr, Callable[[], Awaitable[None]]], Awaitable[object]],
):
    return await run_authorized_http_mcp_call(
        request,
        broker=broker,
        audit_sink=audit,
        operation=operation,
        platform_issuer=PLATFORM_ISSUER,
        platform_audience=PLATFORM_AUDIENCE,
    )


async def forbidden_operation(*args: object) -> object:
    raise AssertionError("无效许可不能进入适配器")


async def test_one_claim_one_call_current_rechecks_and_callback_expires(credential_request):
    request = credential_request
    broker, audit = MemoryBroker(request), MemoryAudit()
    retained = []
    calls = 0

    async def operation(token, recheck):
        nonlocal calls
        calls += 1
        assert isinstance(token, SecretStr) and token == broker.grant.access_token
        retained.append(recheck)
        await recheck()
        return {"untrusted_observation": "isolated"}

    assert await call(request, broker, audit, operation) == {"untrusted_observation": "isolated"}
    assert broker.claims == calls == 1 and broker.checks >= 5
    assert [event.event for event in audit.events] == ["credential_acquired", "call_confirmed"]
    serialized = " ".join(event.model_dump_json() for event in audit.events)
    for secret in (
        broker.grant.access_token.get_secret_value(),
        request.policy.issuer,
        request.policy.resource,
        "tools:read",
    ):
        assert secret not in serialized
    with pytest.raises(HttpMcpAuthorizationError, match="MCP_HTTP_AUTHORIZATION_EXPIRED"):
        await retained[0]()


@pytest.mark.parametrize("missing", ["broker", "audit", "both"])
async def test_missing_provider_rejects_without_operation(credential_request, missing):
    broker, audit = MemoryBroker(credential_request), MemoryAudit()
    with pytest.raises(HttpMcpAuthorizationError) as error:
        await call(
            credential_request,
            None if missing != "audit" else broker,
            None if missing != "broker" else audit,
            forbidden_operation,
        )
    assert error.value.code == "MCP_HTTP_PROVIDER_NOT_CONFIGURED"
    assert not error.value.call_started and broker.claims == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_digest", "0" * 64),
        ("provider_id", "other-provider"),
        ("credential_id", uuid4()),
        ("credential_revision", 2),
        ("tenant_id", uuid4()),
        ("actor_user_id", uuid4()),
        ("issuer", "https://other.example.invalid"),
        ("audiences", ("https://other.example.invalid/tools",)),
        ("scopes", ("tools:read", "tools:write")),
    ],
)
async def test_broker_scope_mismatch_never_enters_adapter(credential_request, field, value):
    broker, audit = MemoryBroker(credential_request), MemoryAudit()
    state = broker.state.model_copy(update={field: value})
    broker.state = state
    broker.grant = HttpMcpTokenGrant(state=state, access_token=broker.grant.access_token)
    with pytest.raises(HttpMcpAuthorizationError) as error:
        await call(credential_request, broker, audit, forbidden_operation)
    assert error.value.code == "MCP_HTTP_CREDENTIAL_SCOPE_REJECTED"
    assert not error.value.call_started and broker.claims == 1
    assert [event.event for event in audit.events] == ["authorization_rejected"]


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": PLATFORM_ISSUER},
        {"aud": PLATFORM_AUDIENCE},
        {"aud": ["other", PLATFORM_AUDIENCE]},
    ],
)
async def test_platform_jwt_cannot_be_forwarded(credential_request, claims):
    token = jwt.encode(claims, "isolated-test-signing-key-of-sufficient-length", algorithm="HS256")
    broker, audit = (
        MemoryBroker(credential_request, token_grant(credential_request, token)),
        MemoryAudit(),
    )
    with pytest.raises(HttpMcpAuthorizationError) as error:
        await call(credential_request, broker, audit, forbidden_operation)
    assert error.value.code == "MCP_HTTP_PLATFORM_TOKEN_REJECTED"
    assert not error.value.call_started
    assert token not in str(error.value) + repr(audit.events)


@pytest.mark.parametrize(
    "change,expected",
    [
        ("digest", "MCP_HTTP_CREDENTIAL_REJECTED"),
        ("expired", "MCP_HTTP_CREDENTIAL_EXPIRED"),
        ("revoked", "MCP_HTTP_CREDENTIAL_REVOKED"),
        ("future", "MCP_HTTP_CREDENTIAL_EXPIRED"),
    ],
)
async def test_invalid_token_state_rejects_before_call(credential_request, change, expected):
    broker, audit = MemoryBroker(credential_request), MemoryAudit()
    now = datetime.now(UTC)
    updates = {
        "digest": {"token_digest": "0" * 64},
        "expired": {
            "issued_at": now - timedelta(seconds=60),
            "expires_at": now - timedelta(seconds=1),
        },
        "revoked": {"revoked_at": now},
        "future": {
            "issued_at": now + timedelta(seconds=5),
            "expires_at": now + timedelta(seconds=30),
        },
    }[change]
    broker.state = broker.state.model_copy(update=updates)
    broker.grant = HttpMcpTokenGrant(state=broker.state, access_token=broker.grant.access_token)
    with pytest.raises(HttpMcpAuthorizationError) as error:
        await call(credential_request, broker, audit, forbidden_operation)
    assert error.value.code == expected and not error.value.call_started


@pytest.mark.parametrize("phase", ["credential_audit", "adapter", "completion_audit"])
async def test_current_revocation_is_checked_after_each_wait(credential_request, phase):
    broker, audit = MemoryBroker(credential_request), MemoryAudit()
    calls = 0

    def on_record(event):
        if (phase, event.event) in {
            ("credential_audit", "credential_acquired"),
            ("completion_audit", "call_confirmed"),
        }:
            broker.allowed = False

    audit.on_record = on_record

    async def operation(token, recheck):
        nonlocal calls
        calls += 1
        if phase == "adapter":
            broker.allowed = False
            await recheck()
        return "late-result"

    with pytest.raises(HttpMcpAuthorizationError) as error:
        await call(credential_request, broker, audit, operation)
    assert error.value.code == "MCP_HTTP_AUTHORIZATION_CHANGED"
    assert error.value.call_started is (phase != "credential_audit")
    assert calls == (0 if phase == "credential_audit" else 1)
    assert broker.claims == 1
    assert audit.events[-1].event == (
        "authorization_rejected" if calls == 0 else "call_unconfirmed"
    )


@pytest.mark.parametrize("event", ["credential_acquired", "call_confirmed"])
async def test_audit_failure_does_not_retry_or_confirm_adapter(credential_request, event):
    broker, audit = MemoryBroker(credential_request), MemoryAudit()
    calls = 0

    def fail(record):
        if record.event == event:
            raise OSError("isolated audit storage error")

    audit.on_record = fail

    async def operation(token, recheck):
        nonlocal calls
        calls += 1
        return "observation"

    with pytest.raises(HttpMcpAuthorizationError) as error:
        await call(credential_request, broker, audit, operation)
    assert error.value.code == "MCP_HTTP_AUDIT_UNAVAILABLE"
    assert error.value.call_started is (event == "call_confirmed")
    assert calls == (1 if event == "call_confirmed" else 0) and broker.claims == 1


@pytest.mark.parametrize(
    "failure", [RuntimeError("secret upstream detail"), TimeoutError(), asyncio.CancelledError()]
)
async def test_adapter_failure_or_cancellation_never_reclaims(credential_request, failure):
    broker, audit = MemoryBroker(credential_request), MemoryAudit()
    calls = 0

    async def operation(token, recheck):
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(
        asyncio.CancelledError
        if isinstance(failure, asyncio.CancelledError)
        else HttpMcpAuthorizationError
    ) as error:
        await call(credential_request, broker, audit, operation)
    assert broker.claims == calls == 1
    assert "secret upstream detail" not in str(error.value)
    if not isinstance(failure, asyncio.CancelledError):
        assert error.value.call_started
        assert audit.events[-1].event == "call_unconfirmed"


@pytest.mark.parametrize("expired", [False, True])
async def test_original_lease_can_be_revoked_and_first_timestamp_is_preserved(
    credential_request, expired
):
    broker, audit = MemoryBroker(credential_request), MemoryAudit()
    if expired:
        now = datetime.now(UTC)
        broker.state = broker.state.model_copy(
            update={
                "issued_at": now - timedelta(seconds=60),
                "expires_at": now - timedelta(seconds=1),
            }
        )
    lease_id = broker.state.lease_id
    first = await revoke_http_mcp_lease(
        credential_request, lease_id=lease_id, broker=broker, audit_sink=audit
    )
    second = await revoke_http_mcp_lease(
        credential_request, lease_id=lease_id, broker=broker, audit_sink=audit
    )
    assert first == second and first.revoked_at is not None
    assert broker.claims == 0
    assert all(event.event == "revocation_confirmed" for event in audit.events)


async def test_unconfirmed_revocation_cannot_manufacture_success(credential_request):
    broker, audit = MemoryBroker(credential_request), MemoryAudit()
    broker.revoke_effect = False
    with pytest.raises(HttpMcpAuthorizationError) as error:
        await revoke_http_mcp_lease(
            credential_request, lease_id=broker.state.lease_id, broker=broker, audit_sink=audit
        )
    assert error.value.code == "MCP_HTTP_REVOCATION_UNCONFIRMED"
    assert broker.state.revoked_at is None and broker.revocations == 1
    assert audit.events[-1].event == "revocation_unconfirmed"


@pytest.mark.parametrize("change", ["token_digest", "credential_revision", "revoked_at"])
async def test_revocation_cannot_rewrite_original_lease_metadata(credential_request, change):
    broker, audit = MemoryBroker(credential_request), MemoryAudit()
    first_revoked = datetime.now(UTC) - timedelta(milliseconds=100)
    if change == "revoked_at":
        broker.state = broker.state.model_copy(update={"revoked_at": first_revoked})

    async def conflicting_revoke(request, lease_id):
        broker.revocations += 1
        updates = {"revoked_at": datetime.now(UTC)}
        if change == "token_digest":
            updates["token_digest"] = "0" * 64
        elif change == "credential_revision":
            updates["credential_revision"] = 2
        broker.state = broker.state.model_copy(update=updates)

    broker.revoke = conflicting_revoke
    with pytest.raises(HttpMcpAuthorizationError, match="MCP_HTTP_REVOCATION_UNCONFIRMED"):
        await revoke_http_mcp_lease(
            credential_request,
            lease_id=broker.state.lease_id,
            broker=broker,
            audit_sink=audit,
        )
    assert broker.revocations == 1 and broker.claims == 0
    assert [event.event for event in audit.events] == ["revocation_unconfirmed"]


async def test_revocation_audit_failure_remains_unknown_without_second_revoke(credential_request):
    broker, audit = MemoryBroker(credential_request), MemoryAudit()

    def fail(_):
        raise OSError("isolated audit unavailable")

    audit.on_record = fail
    with pytest.raises(HttpMcpAuthorizationError, match="MCP_HTTP_AUDIT_UNAVAILABLE"):
        await revoke_http_mcp_lease(
            credential_request,
            lease_id=broker.state.lease_id,
            broker=broker,
            audit_sink=audit,
        )
    assert broker.state.revoked_at is not None and broker.revocations == 1


async def test_expired_request_cannot_claim_or_call(credential_request):
    expired = datetime.now(UTC) - timedelta(seconds=1)
    request = HttpMcpCredentialRequest(
        policy=credential_request.policy.model_copy(update={"valid_until": expired}),
        binding=credential_request.binding.model_copy(update={"expires_at": expired}),
    )
    broker, audit = MemoryBroker(request), MemoryAudit()
    with pytest.raises(HttpMcpAuthorizationError, match="MCP_HTTP_AUTHORIZATION_EXPIRED"):
        await call(request, broker, audit, forbidden_operation)
    assert broker.claims == 0


def test_binding_cannot_change_tenant_or_extend_approved_window(credential_request):
    for updates in (
        {"tenant_id": uuid4()},
        {"expires_at": credential_request.policy.valid_until + timedelta(seconds=1)},
    ):
        with pytest.raises(ValidationError):
            HttpMcpCredentialRequest(
                policy=credential_request.policy,
                binding=credential_request.binding.model_copy(update=updates),
            )


@pytest.mark.parametrize(
    "identifier",
    [
        "http://mcp.example.invalid",
        "https://mcp.example.invalid:443/tools",
        "https://user@mcp.example.invalid",
        "https://mcp.example.invalid/tools?q=1",
        "https://mcp.example.invalid/tools#fragment",
        "https://mcp.example.invalid/a/../tools",
        "https://mcp.example.invalid/%2e/tools",
    ],
)
def test_policy_cannot_silently_normalize_target(credential_request, identifier):
    value = credential_request.policy.model_dump()
    value["resource"] = identifier
    with pytest.raises(ValidationError):
        HttpMcpAuthorizationPolicy.model_validate(value)


@pytest.fixture(scope="module")
def rsa_key():
    # 每次测试会话生成的隔离密钥，不读取或输出任何部署凭据。
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_key(key):
    modulus = key.public_key().public_numbers().n
    return HttpMcpRsaVerificationKey(
        kid="isolated-key",
        n=base64.urlsafe_b64encode(modulus.to_bytes(256, "big")).rstrip(b"=").decode(),
    )


def verification_policy(request, key):
    return HttpMcpJwtVerificationPolicy(
        request_digest=request.request_digest,
        external_subject="external-test-subject",
        client_id="external-test-client",
        keys=(public_key(key),),
        valid_until=request.binding.expires_at,
    )


def jwt_claims(request):
    issued = int(datetime.now(UTC).timestamp()) - 1
    return {
        "iss": request.policy.issuer,
        "sub": "external-test-subject",
        "aud": request.policy.resource,
        "client_id": "external-test-client",
        "jti": "isolated-test-jti",
        "scope": "tools:read",
        "iat": issued,
        "exp": issued + 60,
    }


def signed_grant(request, token, issued, expires):
    grant = token_grant(request, token)
    return HttpMcpTokenGrant(
        state=grant.state.model_copy(update={"issued_at": issued, "expires_at": expires}),
        access_token=grant.access_token,
    )


def verify(request, grant, policy):
    verify_http_mcp_jwt(
        request,
        grant,
        policy=policy,
        platform_issuer=PLATFORM_ISSUER,
        platform_audience=PLATFORM_AUDIENCE,
    )


def test_real_rsa_signature_and_exact_external_subject_mapping(credential_request, rsa_key):
    request = credential_request
    claims = jwt_claims(request)
    token = jwt.encode(
        claims, rsa_key, algorithm="RS256", headers={"kid": "isolated-key", "typ": "at+jwt"}
    )
    grant = signed_grant(
        request,
        token,
        datetime.fromtimestamp(claims["iat"], UTC),
        datetime.fromtimestamp(claims["exp"], UTC),
    )
    verify(request, grant, verification_policy(request, rsa_key))
    assert token not in repr(grant)


@pytest.mark.parametrize(
    "field,value",
    [
        ("iss", "https://other.example.invalid"),
        ("aud", "https://other.example.invalid/tools"),
        ("aud", ["https://mcp.example.invalid/tools"]),
        ("sub", "other-subject"),
        ("client_id", "other-client"),
        ("scope", "tools:read tools:write"),
        ("scope", "tools:read tools:read"),
        ("scope", " tools:read"),
        ("scope", "tools:read "),
        ("iat", True),
        ("exp", "123456"),
        ("nbf", 0),
        ("jti", ""),
        ("unapproved_claim", "secret-claim-detail"),
    ],
)
def test_signed_but_wrong_claims_are_rejected(credential_request, rsa_key, field, value):
    request = credential_request
    claims = jwt_claims(request)
    issued, expires = (datetime.fromtimestamp(claims[name], UTC) for name in ("iat", "exp"))
    claims[field] = value
    token = jwt.encode(
        claims, rsa_key, algorithm="RS256", headers={"kid": "isolated-key", "typ": "at+jwt"}
    )
    with pytest.raises(HttpMcpAuthorizationError) as error:
        verify(
            request,
            signed_grant(request, token, issued, expires),
            verification_policy(request, rsa_key),
        )
    assert error.value.code == "MCP_HTTP_CREDENTIAL_REJECTED"
    assert token not in str(error.value) and "secret-claim-detail" not in str(error.value)
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "header",
    [
        {"kid": "unknown-key", "typ": "at+jwt"},
        {"kid": "isolated-key", "typ": "JWT"},
        {"kid": "isolated-key", "typ": "at+jwt", "jku": "https://untrusted.example.invalid/keys"},
        {"kid": "isolated-key", "typ": "at+jwt", "jwk": {"kty": "RSA"}},
        {"kid": "isolated-key", "typ": "at+jwt", "crit": ["custom"]},
    ],
)
def test_header_cannot_select_unapproved_key_or_remote_discovery(
    credential_request, rsa_key, header
):
    request = credential_request
    claims = jwt_claims(request)
    token = jwt.encode(claims, rsa_key, algorithm="RS256", headers=header)
    grant = signed_grant(
        request,
        token,
        datetime.fromtimestamp(claims["iat"], UTC),
        datetime.fromtimestamp(claims["exp"], UTC),
    )
    with pytest.raises(HttpMcpAuthorizationError, match="MCP_HTTP_CREDENTIAL_REJECTED"):
        verify(request, grant, verification_policy(request, rsa_key))


@pytest.mark.parametrize(
    "change",
    [
        "signature",
        "algorithm",
        "duplicate_claim",
        "noncanonical",
        "lease_time",
        "expired",
        "future",
        "wrong_request",
        "expired_policy",
    ],
)
def test_crypto_and_time_boundaries_reject_even_with_matching_token_digest(
    credential_request, rsa_key, change
):
    request = credential_request
    claims = jwt_claims(request)
    policy = verification_policy(request, rsa_key)
    issued, expires = (datetime.fromtimestamp(claims[name], UTC) for name in ("iat", "exp"))
    if change == "expired":
        claims.update(iat=claims["iat"] - 120, exp=claims["exp"] - 120)
        issued, expires = (datetime.fromtimestamp(claims[name], UTC) for name in ("iat", "exp"))
    elif change == "future":
        claims.update(iat=claims["iat"] + 20, exp=claims["exp"] + 20)
        issued, expires = (datetime.fromtimestamp(claims[name], UTC) for name in ("iat", "exp"))
    elif change == "lease_time":
        expires -= timedelta(seconds=1)
    elif change == "wrong_request":
        policy = policy.model_copy(update={"request_digest": "0" * 64})
    elif change == "expired_policy":
        policy = policy.model_copy(update={"valid_until": datetime.now(UTC) - timedelta(seconds=1)})
    token = jwt.encode(
        claims, rsa_key, algorithm="RS256", headers={"kid": "isolated-key", "typ": "at+jwt"}
    )
    if change == "signature":
        head, body, signature = token.split(".")
        raw = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
        signature = base64.urlsafe_b64encode(bytes([raw[0] ^ 1]) + raw[1:]).rstrip(b"=").decode()
        token = f"{head}.{body}.{signature}"
    elif change == "algorithm":
        token = jwt.encode(
            claims,
            "isolated-hmac-key-of-sufficient-length",
            algorithm="HS256",
            headers={"kid": "isolated-key", "typ": "at+jwt"},
        )
    elif change == "noncanonical":
        head, body, signature = token.split(".")
        token = f"{head}=.{body}.{signature}"
    elif change == "duplicate_claim":
        head = token.split(".")[0]
        raw = json.dumps(claims, separators=(",", ":"))[:-1] + ',"scope":"tools:read"}'
        body = base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()
        signature = rsa_key.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
        token = f"{head}.{body}." + base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
    with pytest.raises(HttpMcpAuthorizationError, match="MCP_HTTP_CREDENTIAL_REJECTED"):
        verify(request, signed_grant(request, token, issued, expires), policy)


@pytest.mark.parametrize("phase", ["before_claim", "during_claim", "after_claim"])
async def test_verifying_broker_rechecks_policy_without_reissuing(
    credential_request, rsa_key, phase
):
    request = credential_request
    policy = verification_policy(request, rsa_key)
    claims = jwt_claims(request)
    token = jwt.encode(
        claims, rsa_key, algorithm="RS256", headers={"kid": "isolated-key", "typ": "at+jwt"}
    )
    upstream = MemoryBroker(
        request,
        signed_grant(
            request,
            token,
            datetime.fromtimestamp(claims["iat"], UTC),
            datetime.fromtimestamp(claims["exp"], UTC),
        ),
    )
    changed = phase == "before_claim"

    async def current_policy(_):
        if changed or phase == "during_claim" and upstream.claims:
            return policy.model_copy(update={"client_id": "changed-client"})
        return policy

    broker = VerifiedJwtHttpMcpCredentialBroker(
        upstream=upstream,
        policy=policy,
        current_policy=current_policy,
        platform_issuer=PLATFORM_ISSUER,
        platform_audience=PLATFORM_AUDIENCE,
    )
    if phase == "after_claim":
        assert await broker.claim(request) == upstream.grant
        changed = True
        with pytest.raises(HttpMcpAuthorizationError, match="MCP_HTTP_AUTHORIZATION_CHANGED"):
            await broker.assert_current_permit(request)
    else:
        with pytest.raises(HttpMcpAuthorizationError, match="MCP_HTTP_CREDENTIAL_REJECTED"):
            await broker.claim(request)
    assert upstream.claims == (0 if phase == "before_claim" else 1)
    # 映射变化后，原lease仍可经上游独立撤销权读取/撤销，不重新领取令牌。
    await broker.revoke(request, upstream.state.lease_id)
    assert (await broker.current(request, upstream.state.lease_id)).revoked_at is not None
