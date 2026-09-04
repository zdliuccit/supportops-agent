from uuid import uuid4

import pytest
from supportops_core.enums import RunStatus
from supportops_core.models import AgentRun
from supportops_core.services import InvalidRunTransitionError, transition_run


def make_run(status: RunStatus) -> AgentRun:
    return AgentRun(
        tenant_id=uuid4(),
        conversation_id=uuid4(),
        input_message_id=uuid4(),
        status=status,
        correlation_id="test-correlation",
    )


def test_run_allows_declared_transition() -> None:
    run = make_run(RunStatus.QUEUED)
    transition_run(run, RunStatus.RUNNING)
    assert run.status == RunStatus.RUNNING
    assert run.started_at is not None


def test_run_rejects_illegal_transition() -> None:
    run = make_run(RunStatus.COMPLETED)
    with pytest.raises(InvalidRunTransitionError):
        transition_run(run, RunStatus.RUNNING)
