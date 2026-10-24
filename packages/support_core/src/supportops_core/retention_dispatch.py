"""原批次派发提交后的一次进程内交接；历史DTO不能恢复目标写许可。"""

from threading import Lock
from typing import NoReturn

from supportops_core.retention_batch_contracts import RetentionBatchLease, RetentionBatchPlan
from supportops_core.retention_batch_events import RetentionBatchEvent, require_live_event

_ISSUER = object()


class RetentionBatchDispatch:
    """只由真实Store本次提交路径签发；不是持久令牌或进程崩溃后的恢复依据。"""

    __slots__ = ("_source", "_event", "_used", "_lock")

    def __init__(self, issuer: object, source: object, event: RetentionBatchEvent) -> None:
        if issuer is not _ISSUER or event.event_type != "dispatched":
            raise ValueError("RETENTION_DISPATCH_HANDOFF_INVALID")
        self._source = source
        self._event = RetentionBatchEvent.model_validate_json(event.model_dump_json())
        self._used, self._lock = False, Lock()

    def __repr__(self) -> str:
        return "RetentionBatchDispatch(<private>)"

    def __reduce__(self) -> NoReturn:
        raise TypeError("RETENTION_DISPATCH_HANDOFF_NOT_SERIALIZABLE")

    def consume(self, source: object, plan: RetentionBatchPlan, lease: RetentionBatchLease) -> RetentionBatchEvent:
        with self._lock:
            if self._used or source is not self._source or self._event.plan != plan or self._event.lease != lease:
                raise ValueError("RETENTION_DISPATCH_HANDOFF_INVALID")
            self._used = True  # 期限检查失败、取消或提交未知均不恢复交接许可。
            require_live_event(self._event, lease)
            return self._event


def _issue_retention_dispatch(source: object, event: RetentionBatchEvent) -> RetentionBatchDispatch:
    """内部Store仅在原派发事务明确退出后调用；任何历史读取入口都不得调用。"""
    require_live_event(event, event.lease)
    return RetentionBatchDispatch(_ISSUER, source, event)
