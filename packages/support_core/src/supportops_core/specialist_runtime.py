"""显式固定批次的PG准入/一次SDK/原结果采纳组合；默认依赖缺失，不注册Worker。"""

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.config import Settings
from supportops_core.outbound_http import OutboundHttpPolicy
from supportops_core.run_attempt_services import RunAttemptIdentity
from supportops_core.secrets import SecretProvider
from supportops_core.specialist_admission_contracts import SpecialistAdmissionBinding
from supportops_core.specialist_admission_services import PostgresSpecialistAdmission
from supportops_core.specialist_budget_services import SpecialistBudgetAuthority
from supportops_core.specialist_execution_cipher import SpecialistResponseCipher
from supportops_core.specialist_execution_store import SpecialistExecutionStorageAuthority
from supportops_core.specialist_scheduler import SpecialistCoordinator
from supportops_core.specialist_scheduler_contracts import SpecialistSchedule, SpecialistScheduleIssue, SpecialistScheduleReport
from supportops_core.specialist_sdk_executor import PostgresSpecialistSDKExecutor, SpecialistSDKDenied


class PostgresSpecialistBatch:
    """每实例只允许一次run；原图、call映射、父attempt及所有可信依赖创建时固定。

    仅内部获批调用者显式构造，不生成默认启动/存储许可、不写配置或替换父模型。
    原图要求同父授权范围与共享预算一致；不同范围不能以本组合绕过图校验。
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession], *, attempt: RunAttemptIdentity,
        schedule: SpecialistSchedule, bindings: tuple[SpecialistAdmissionBinding, ...], settings: Settings,
        authority: SpecialistBudgetAuthority | None = None,
        storage_authority: SpecialistExecutionStorageAuthority | None = None,
        secret_provider: SecretProvider | None = None, cipher: SpecialistResponseCipher | None = None,
        policies: Mapping[UUID, OutboundHttpPolicy] | None = None, redaction_secrets: tuple[str, ...] = (),
    ) -> None:
        schedule = SpecialistSchedule.model_validate_json(schedule.model_dump_json())
        bindings = tuple(SpecialistAdmissionBinding.model_validate_json(row.model_dump_json()) for row in bindings)
        # 构造只检查本地契约/依赖，不取密、预留、写库或发请求。
        executor = PostgresSpecialistSDKExecutor(factory, attempt=attempt, bindings=bindings, settings=settings,
            authority=authority, storage_authority=storage_authority, secret_provider=secret_provider,
            cipher=cipher, policies=policies, redaction_secrets=redaction_secrets)
        admission = PostgresSpecialistAdmission(factory, attempt=attempt, schedule=schedule, bindings=bindings,
            settings=settings, authority=authority, usage_reader=executor, result_reader=executor,
            redaction_secrets=redaction_secrets)
        self._schedule, self._executor = schedule, executor
        self._coordinator = SpecialistCoordinator(admission=admission, executor=executor)
        self._entered = False

    async def run(self) -> SpecialistScheduleReport:
        if self._entered:
            raise SpecialistSDKDenied("SPECIALIST_BATCH_ALREADY_ENTERED")
        self._entered = True  # 取消/失败/未知同样永久消费本实例，不能重跑图或换call。
        report = await self._coordinator.run(self._schedule)
        pending = set(self._executor.unconfirmed_task_ids) | set(report.unfinished_task_ids)
        if not pending:
            return report
        # executor已向协调器报告失败但费用collector仍在途时，批次不能伪称全部退出。
        return SpecialistScheduleReport.model_validate_json(report.model_copy(update={
            "status": "unconfirmed", "unfinished_task_ids": tuple(sorted(pending, key=str)),
            "next_action": "human_attention", "issues": (*report.issues,
                SpecialistScheduleIssue(code="CALLBACK_UNCONFIRMED")),
        }).model_dump_json())
