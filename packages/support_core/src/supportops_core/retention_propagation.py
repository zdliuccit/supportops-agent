"""独立维护集成的删除传播只读核对；不注册默认目标、不调用删除执行器。"""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import uuid4

from supportops_core.retention_batch_contracts import batch_digest
from supportops_core.retention_propagation_contracts import (
    PropagationAudit, PropagationObservation, PropagationReceipt, PropagationRequest,
    PropagationTarget, PropagationTargetResult,
)


class PropagationAuthority(Protocol):
    """每次核对原账本真实性/代次/成员、当前读取许可、批准及完整副本盘点。

    必须确认清单未遗漏任何已知副本，inventory_digest与当前拓扑一致；未知或变化拒绝。
    仅有摘要匹配不能替代该检查，不得将此只读许可用于目标删除。
    """

    authority_id: str

    async def assert_current(self, request: PropagationRequest) -> None: ...


class PropagationVerifier(Protocol):
    """固定目标的独立只读观察；不能由源删除回执、缓存miss或不完整列表推断完成。"""

    verifier_id: str

    async def observe(self, request: PropagationRequest, target: PropagationTarget,
    ) -> PropagationObservation: ...


class PropagationAuditSink(Protocol):
    async def record(self, event: PropagationAudit) -> None:
        """独立持久追加并提交后返回，不得使用临时内存或覆盖旧事件；未知不重放。"""
        ...


class PropagationUnavailable(RuntimeError):
    code = "RETENTION_PROPAGATION_DEPENDENCIES_UNAVAILABLE"


class PropagationUnconfirmed(RuntimeError):
    code = "RETENTION_PROPAGATION_INSPECTION_UNCONFIRMED"


def _live(request: PropagationRequest) -> None:
    if not request.created_at <= datetime.now(UTC) < request.expires_at:
        raise ValueError()


async def inspect_deletion_propagation(request: PropagationRequest, *,
    authority: PropagationAuthority | None = None, audit_sink: PropagationAuditSink | None = None,
    verifiers: Mapping[str, PropagationVerifier] | None = None,
) -> PropagationReceipt:
    """最多16个显式目标、20秒工作等待；只返回当前列明副本的核对结果。"""
    if authority is None or audit_sink is None or verifiers is None:
        raise PropagationUnavailable()
    try:
        if not isinstance(request, PropagationRequest) or authority.authority_id != request.authority_id:
            raise ValueError()
        _live(request)
        request_digest = batch_digest(request)
        # 固定本次注册映射；只按目标别名分派，不接受URL、命令或模型提供的实现。
        if len(verifiers) > 16:
            raise ValueError()
        registry = dict(verifiers)
        if not set(registry) <= {target.target_id for target in request.targets}:
            raise ValueError()

        async def current() -> None:
            _live(request)
            if authority.authority_id != request.authority_id:
                raise ValueError()
            async with asyncio.timeout(0.5):
                await authority.assert_current(request)
            _live(request)

        async def record(event: Literal["inspection_started", "target_observed", "inspection_finished"],
            result: PropagationTargetResult | None = None,
        ) -> None:
            message = PropagationAudit(event_id=uuid4(), request_id=request.request_id,
                request_digest=request_digest, fact_digest=request.fact.record_digest,
                inventory_digest=request.inventory_digest, event=event, result=result,
                observed_at=datetime.now(UTC))
            _live(request)
            async with asyncio.timeout(0.5):
                await audit_sink.record(message)
            _live(request)

        async with asyncio.timeout(min(20, (request.expires_at - datetime.now(UTC)).total_seconds())):
            await current()
            await record("inspection_started")
            results: list[PropagationTargetResult] = []
            for target in request.targets:
                await current()
                verifier = registry.get(target.target_id)
                observation: PropagationObservation | None = None
                started_at = datetime.now(UTC)
                if verifier is not None:
                    if verifier.verifier_id != target.verifier_id:
                        raise ValueError()
                    try:
                        async with asyncio.timeout(1):
                            observation = await verifier.observe(request, target)
                        if (not isinstance(observation, PropagationObservation)
                            or observation.request_digest != request_digest
                            or observation.target_id != target.target_id
                            or observation.target_scope_digest != target.target_scope_digest
                            or observation.verifier_id != target.verifier_id
                            or observation.resource_key_digest != request.fact.resource_key_digest
                            or observation.fact_digest != request.fact.record_digest
                            or observation.ledger_generation != request.ledger_generation
                            or not started_at <= observation.observed_at <= datetime.now(UTC)):
                            raise ValueError()
                    except Exception:
                        # 单项故障仅产生未知，不输出目标异常，不重试或伪造目标观察。
                        observation = None
                await current()
                state = observation.state if observation is not None else "unknown"
                reason: Literal["confirmed", "target_present", "action_mismatch", "verifier_unavailable", "unconfirmed"]
                if state == "unknown":
                    reason = "verifier_unavailable" if verifier is None else "unconfirmed"
                elif state == "present":
                    reason = "target_present"
                else:
                    reason = "confirmed" if state == request.fact.action else "action_mismatch"
                result = PropagationTargetResult(target_id=target.target_id, kind=target.kind,
                    state=state, reason=reason,
                    observation_digest=batch_digest(observation) if observation is not None else None,
                    evidence_digest=observation.evidence_digest if observation is not None else None,
                    observed_at=observation.observed_at if observation is not None else datetime.now(UTC))
                await record("target_observed", result)
                results.append(result)
            await current()
            await record("inspection_finished")
            await current()
            return PropagationReceipt(request_id=request.request_id, request_digest=request_digest,
                fact_digest=request.fact.record_digest, inventory_digest=request.inventory_digest,
                results=tuple(results), status="listed_targets_confirmed"
                    if all(result.reason == "confirmed" for result in results) else "needs_attention",
                finished_at=datetime.now(UTC))
    except Exception:
        # 账本/拓扑变化或审计失败不返回半份成功；外部取消继续传播。
        raise PropagationUnconfirmed() from None
