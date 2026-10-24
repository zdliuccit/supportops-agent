"""会话/凭据隔离的独立批次复核；只读目标，不执行隔离或生成删除事实。"""

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncEngine

from supportops_core.retention_access_propagation import (
    AccessPropagationBinding, AccessPropagationDatabase, CredentialPropagationBinding,
    SessionPropagationBinding, _AccessMetadataReader,
)
from supportops_core.retention_batch_contracts import (
    RetentionBatchLease, RetentionBatchPlan, RetentionBatchVerification, RetentionResourceVerification,
    batch_digest, require_live_recovery_permit,
)
from supportops_core.retention_batch_events import RetentionBatchEvent, require_live_event
from supportops_core.retention_batch_store import PostgresRetentionBatchStore
from supportops_core.retention_tombstone_snapshot import _canonical

VERIFIER_ID = "access_quarantine_batch_v1"


class AccessRetentionAuthority(Protocol):
    async def assert_current_scope(self, event: RetentionBatchEvent, database: AccessPropagationDatabase,
        bindings: tuple[AccessPropagationBinding, ...],
    ) -> None:
        """独立核验原政策/批准/法律保留/完整依赖与原资源全集、实际目标和读取路径。

        必须将原maintenance身份、本人/共享凭据范围、当前普通或恢复复核许可与真实
        已派发批次绑定；个人关联、元数据摘要和管理员角色均不构成维护批准。
        这里只批准读取和本次隔离状态复核，不批准撤销/软删除/物理擦除或目标重放。
        """
        ...


class AccessRetentionUnconfirmed(RuntimeError):
    code = "RETENTION_ACCESS_BATCH_VERIFICATION_UNCONFIRMED"


def _prepare_access_scope(plan: RetentionBatchPlan, database: AccessPropagationDatabase,
    bindings: tuple[AccessPropagationBinding, ...],
) -> tuple[RetentionBatchPlan, AccessPropagationDatabase, tuple[AccessPropagationBinding, ...], tuple[_AccessMetadataReader, ...]]:
    """写适配与独立复核共用完整固定集合校验，不授予目标操作或读取许可。"""
    if (not isinstance(database, AccessPropagationDatabase) or not isinstance(plan, RetentionBatchPlan)
        or plan.category not in {"auth_sessions", "credentials"} or plan.operation != "quarantined"
        or plan.verifier_id != VERIFIER_ID or not 1 <= len(bindings) <= 100):
        raise AccessRetentionUnconfirmed()
    _plan = RetentionBatchPlan.model_validate_json(plan.model_dump_json())
    _database = AccessPropagationDatabase.model_validate_json(database.model_dump_json())
    expected = SessionPropagationBinding if plan.category == "auth_sessions" else CredentialPropagationBinding
    if any(type(binding) is not expected for binding in bindings):
        raise AccessRetentionUnconfirmed()
    copied = tuple(type(binding).model_validate_json(binding.model_dump_json()) for binding in bindings)
    indexed: dict[str, _AccessMetadataReader] = {}
    ids: set[UUID] = set()
    for binding in copied:
        locator = binding.locator
        digest = hashlib.sha256(_canonical(locator.model_dump(mode="json"))).hexdigest()
        if (digest in indexed or locator.resource_id in ids or locator.tenant_id != plan.tenant_id
            or locator.subject_user_id != plan.subject_user_id or binding.record_created_at > plan.cutoff_at):
            raise AccessRetentionUnconfirmed()
        indexed[digest] = _AccessMetadataReader(_database, binding)
        ids.add(locator.resource_id)
    if tuple(sorted(indexed)) != plan.resource_key_digests:
        raise AccessRetentionUnconfirmed()
    _bindings = tuple(sorted(copied, key=lambda binding:
        hashlib.sha256(_canonical(binding.locator.model_dump(mode="json"))).hexdigest()))
    _readers = tuple(indexed[digest] for digest in plan.resource_key_digests)
    return _plan, _database, _bindings, _readers


class PostgresAccessRetentionVerifier:
    """显式固定一个原quarantined批次；缺真实Store或独立scope批准即拒绝。"""

    verifier_id = VERIFIER_ID

    def __init__(self, engine: AsyncEngine, database: AccessPropagationDatabase, *,
        plan: RetentionBatchPlan, bindings: tuple[AccessPropagationBinding, ...],
        store: PostgresRetentionBatchStore | None = None, authority: AccessRetentionAuthority | None = None,
    ) -> None:
        if (not isinstance(engine, AsyncEngine) or engine.dialect.name != "postgresql"
            or not isinstance(database, AccessPropagationDatabase) or not isinstance(plan, RetentionBatchPlan)
            or not isinstance(store, PostgresRetentionBatchStore) or authority is None
            or plan.category not in {"auth_sessions", "credentials"} or plan.operation != "quarantined"
            or plan.verifier_id != self.verifier_id or not 1 <= len(bindings) <= 100):
            raise AccessRetentionUnconfirmed()
        self._plan, self._database, self._bindings, self._readers = _prepare_access_scope(plan, database, bindings)
        self._engine, self._store, self._authority = engine, store, authority
        self._entered: set[UUID] = set()

    @staticmethod
    def _window(event: RetentionBatchEvent) -> None:
        now = datetime.now(UTC)
        if not event.plan.created_at <= now < event.plan.expires_at:
            raise ValueError()
        if event.recovery_permit is None:
            require_live_event(event, event.lease)
        else:
            require_live_recovery_permit(event.recovery_permit)

    async def _current(self, lease: RetentionBatchLease, verification_id: UUID) -> RetentionBatchEvent:
        async with asyncio.timeout(0.8):
            event = await self._store.read_current_verification(self._plan, lease, verification_id=verification_id)
            self._window(event)
            await self._authority.assert_current_scope(event, self._database, self._bindings)
            self._window(event)
        return event

    async def observe(self, plan: RetentionBatchPlan, lease: RetentionBatchLease, *,
        verification_id: UUID,
    ) -> RetentionBatchVerification:
        try:
            if (not isinstance(plan, RetentionBatchPlan) or plan != self._plan
                or not isinstance(lease, RetentionBatchLease) or not isinstance(verification_id, UUID)
                or verification_id in self._entered or len(self._entered) >= plan.max_verification_attempts):
                raise ValueError()
            self._entered.add(verification_id)  # 失败/取消也不在本实例重读同一验证。
            deadline = asyncio.get_running_loop().time() + 1.9
            async with asyncio.timeout(1.9):
                event = await self._current(lease, verification_id)
                async with self._engine.connect() as connection, connection.begin():
                    # 同类别全集只检查一次元数据权限/原历史保护；不会读取账本或其他业务表。
                    await self._readers[0]._database(connection)
                    async with asyncio.timeout(0.5):
                        await self._authority.assert_current_scope(event, self._database, self._bindings)
                    self._window(event)
                    query = text("""
                        SELECT id,tenant_id,user_id,created_at,revoked_at,revoked_by,revocation_code
                        FROM ONLY public.auth_sessions WHERE id IN :ids ORDER BY id LIMIT :limit
                    """) if plan.category == "auth_sessions" else text("""
                        SELECT id,tenant_id,credential_id,revision,provider,created_at,deleted_at
                        FROM ONLY public.model_credential_revisions WHERE id IN :ids ORDER BY id LIMIT :limit
                    """)
                    rows = (await connection.execute(query.bindparams(bindparam("ids", expanding=True)), {
                        "ids": [binding.locator.resource_id for binding in self._bindings],
                        "limit": len(self._bindings) + 1,
                    })).mappings().all()
                    observed_at = datetime.now(UTC)
                    expected_ids = {binding.locator.resource_id for binding in self._bindings}
                    by_id = {row["id"]: row for row in rows}
                    if (len(rows) > len(self._bindings) or len(by_id) != len(rows)
                        or not set(by_id) <= expected_ids):
                        raise ValueError()
                    resources: list[RetentionResourceVerification] = []
                    for binding, reader, digest in zip(self._bindings, self._readers, plan.resource_key_digests, strict=True):
                        state, marker = reader._state(by_id.get(binding.locator.resource_id), observed_at)
                        # 缺行或未隔离只给unknown，不能据此证明从未派发/无副作用。
                        proof = {"schema_version": "access_retention_verification_evidence_v1",
                            "plan_digest": batch_digest(plan), "lease_digest": batch_digest(lease),
                            "verification_id": str(verification_id), "source_event_digest": batch_digest(event),
                            "binding_digest": batch_digest(binding), "database_digest": batch_digest(self._database),
                            "resource_key_digest": digest, "quarantine_marker_at": marker.isoformat() if marker else None,
                            "observed_at": observed_at.isoformat(), "erasure_verified": False}
                        resources.append(RetentionResourceVerification(resource_key_digest=digest,
                            status="verified" if state == "quarantined" else "unknown",
                            evidence_digest=hashlib.sha256(_canonical(proof)).hexdigest() if state == "quarantined" else None))
                # 源入口已变化、被收尾或被新验证取代时，丢弃本次观察，不挑旧成功。
                if await self._current(lease, verification_id) != event:
                    raise ValueError()
                result = RetentionBatchVerification(plan_digest=batch_digest(plan), lease_id=lease.lease_id,
                    generation=lease.generation, verification_id=verification_id, verifier_id=self.verifier_id,
                    observed_at=observed_at, resources=tuple(resources))
            if asyncio.get_running_loop().time() > deadline:
                raise ValueError()
            self._window(event)
            return result
        except Exception:
            raise AccessRetentionUnconfirmed() from None
