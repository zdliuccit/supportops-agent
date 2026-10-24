"""精确版本范围内的Worker依赖组合；仅内存构造，不提供默认批准或启用。"""

from collections.abc import Mapping
from types import MappingProxyType
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.model_routing_checkpoint import RoutingCheckpointStorageAuthority
from supportops_core.model_routing_circuit_store import RoutingCircuitAuthority
from supportops_core.model_routing_contracts import Digest, RoutingFilterModel
from supportops_core.model_routing_coordinator import PostgresRoutingCoordinator, RoutingExecutionHistoryReader
from supportops_core.model_routing_eligibility import RoutingDataBoundaryReader
from supportops_core.model_routing_failover_contracts import RoutingFailoverControls
from supportops_core.model_routing_product_runtime import PostgresRoutingProductExecutor
from supportops_core.model_routing_product_services import RoutingProductAuthority
from supportops_core.model_routing_request_contracts import RoutingWorkerStart
from supportops_core.model_routing_request_services import RoutingPreparedApprovalReader
from supportops_core.model_routing_reservations import RoutingReservationAuthority
from supportops_core.model_routing_result_services import RoutingResultDenied
from supportops_core.outbound_http import OutboundHttpPolicy
from supportops_core.run_attempt_services import RunAttemptIdentity
from supportops_core.secrets import SecretProvider


class RoutingWorkerScope(RoutingFilterModel):
    """仅选择配置范围；不充当当前授权、发布门禁或独立批准记录。"""

    tenant_id: UUID
    agent_version_id: UUID
    config_digest: Digest


class PostgresRoutingWorkerFactory:
    """由可信集成显式创建，服务一个确切v3版本；不缓存或重用执行器。

    build接收原Worker入口明确提交后的记录及确切领取身份。原Worker仍负责一次
    登记与entry绑定；记录本身不是执行许可，全部authority在实际边界读取当前批准。
    依赖提供者可以共享，协调器、输入作用域、checkpoint及产品执行状态逐轮新建。
    配置变化须显式创建新工厂，不自动换租户/版本、扩大候选或加载环境中的模块。
    """

    def __init__(self, *, scope: RoutingWorkerScope, controls: RoutingFailoverControls,
        reservation_authority: RoutingReservationAuthority,
        approval_reader: RoutingPreparedApprovalReader, boundary_reader: RoutingDataBoundaryReader,
        circuit_authority: RoutingCircuitAuthority, product_authority: RoutingProductAuthority,
        checkpoint_authority: RoutingCheckpointStorageAuthority, secret_provider: SecretProvider,
        candidate_policies: Mapping[UUID, OutboundHttpPolicy],
        history_reader: RoutingExecutionHistoryReader | None = None,
        streaming: bool = True, redaction_secrets: tuple[str, ...] = (),
    ) -> None:
        if (not isinstance(scope, RoutingWorkerScope) or not isinstance(controls, RoutingFailoverControls)
            or any(value is None for value in (reservation_authority, approval_reader, boundary_reader,
                circuit_authority, product_authority, checkpoint_authority, secret_provider))
            or not isinstance(candidate_policies, Mapping) or not 1 <= len(candidate_policies) <= 8
            or type(streaming) is not bool or not isinstance(redaction_secrets, tuple)
            or any(not isinstance(value, str) for value in redaction_secrets)):
            raise RoutingResultDenied("ROUTING_WORKER_BINDING_INVALID")
        # 复制受控配置，避免调用方随后修改原dict/set或未经校验的model_copy材料。
        self._scope = RoutingWorkerScope.model_validate_json(scope.model_dump_json())
        self._controls = RoutingFailoverControls.model_validate_json(controls.model_dump_json())
        policies: dict[UUID, OutboundHttpPolicy] = {}
        for candidate_id, policy in candidate_policies.items():
            if not isinstance(candidate_id, UUID) or not isinstance(policy, OutboundHttpPolicy):
                raise RoutingResultDenied("ROUTING_WORKER_BINDING_INVALID")
            policies[candidate_id] = OutboundHttpPolicy(allowed_hosts=frozenset(policy.allowed_hosts),
                allowed_paths=frozenset(policy.allowed_paths), allowed_ports=frozenset(policy.allowed_ports),
                allowed_ips=frozenset(policy.allowed_ips), private_ips=frozenset(policy.private_ips))
        self._policies = MappingProxyType(policies)
        self._reservation, self._approval, self._boundary = reservation_authority, approval_reader, boundary_reader
        self._circuit, self._product, self._checkpoint = circuit_authority, product_authority, checkpoint_authority
        self._provider, self._history = secret_provider, history_reader
        self._streaming, self._redaction = streaming, redaction_secrets

    def build(self, factory: async_sessionmaker[AsyncSession], *, settings: Settings,
        attempt: RunAttemptIdentity, start: RoutingWorkerStart,
    ) -> PostgresRoutingProductExecutor:
        """同步组合本轮新实例；不读库、取密、执行批准reader或启动任何任务。"""
        if not isinstance(start, RoutingWorkerStart):
            raise RoutingResultDenied("ROUTING_WORKER_BINDING_INVALID")
        start = RoutingWorkerStart.model_validate_json(start.model_dump_json())
        if ((start.tenant_id, start.agent_version_id, start.config_digest)
                != (self._scope.tenant_id, self._scope.agent_version_id, self._scope.config_digest)
            or (start.tenant_id, start.conversation_id, start.run_id, start.attempt_id, start.generation)
                != (attempt.tenant_id, attempt.conversation_id, attempt.run_id, attempt.attempt_id, attempt.generation)):
            raise RoutingResultDenied("ROUTING_WORKER_BINDING_INVALID")
        coordinator = PostgresRoutingCoordinator(factory, settings=settings,
            authority=self._reservation, approval_reader=self._approval, boundary_reader=self._boundary,
            circuit_authority=self._circuit, history_reader=self._history, secret_provider=self._provider,
            candidate_policies=self._policies, redaction_secrets=self._redaction)
        return PostgresRoutingProductExecutor(factory, settings=settings, attempt=attempt, controls=self._controls,
            coordinator=coordinator, reservation_authority=self._reservation, boundary_reader=self._boundary,
            product_authority=self._product, checkpoint_authority=self._checkpoint,
            streaming=self._streaming, redaction_secrets=self._redaction)
