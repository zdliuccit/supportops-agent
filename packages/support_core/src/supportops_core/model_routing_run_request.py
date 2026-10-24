"""从原Run产品事实创建本次完整逻辑请求；不接收客户端正文，不取密或调用模型。"""

import asyncio
from uuid import UUID

from langchain.agents.middleware import ModelRequest
from langchain.agents.structured_output import ProviderStrategy
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatResult
from langgraph.runtime import Runtime
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from supportops_core.agent_config import SupportAnswer, SupportContext
from supportops_core.agent_runtime import _PLATFORM_DATA_POLICY
from supportops_core.config import Settings
from supportops_core.context_services import ContextSnapshot
from supportops_core.enums import MessageRole
from supportops_core.history_context_services import assert_history_context_authorized
from supportops_core.model_routing_request_services import (
    RoutingRequestDenied, assert_routing_worker_entry, routing_business_activity_exists, routing_outcome_event_exists,
)
from supportops_core.model_routing_runtime_input import _version
from supportops_core.models import Message, ModelCallLedger, RunEvent
from supportops_core.release_manifest_services import manifest_digest
from supportops_core.run_attempt_services import RunAttemptIdentity, current_run_identity, owned_run
from supportops_core.run_input_context import load_run_input_context
from supportops_core.services import content_digest


class _RoutingRequestModel(BaseChatModel):
    """逻辑路由ModelRequest的模型边界；物理调用只允许走独立派发器，不能直接生成。"""

    @property
    def _llm_type(self) -> str:
        return "supportops_routing_request_only_v1"

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None, **kwargs: object,
    ) -> ChatResult:
        raise RoutingRequestDenied("ROUTING_REQUEST_DIRECT_GENERATION_FORBIDDEN")

    async def _agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: object,
    ) -> ChatResult:
        raise RoutingRequestDenied("ROUTING_REQUEST_DIRECT_GENERATION_FORBIDDEN")


async def build_owned_routing_request(factory: async_sessionmaker[AsyncSession], *,
    attempt: RunAttemptIdentity, settings: Settings, worker_entry_id: UUID | None = None,
) -> ModelRequest[SupportContext]:
    """从数据库正文创建新请求，随后由原gateway裁剪、冻结和登记一次入口。

    这里不重建任何已派发调用的缺失正文/回执，不解析checkpoint或构造供应商客户端；
    逻辑模型没有凭据、URL或备用handler。事务未明确提交则不返回请求，不允许发送。
    """
    try:
        async with asyncio.timeout(5):
            async with factory() as session, session.begin():
                if session.get_bind().dialect.name != "postgresql":
                    raise RoutingRequestDenied("ROUTING_RUN_REQUEST_POSTGRES_REQUIRED")
                await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                await session.execute(text("SET LOCAL statement_timeout = '2s'"))
                owned = await owned_run(session, attempt)
                await assert_routing_worker_entry(session, attempt=attempt, worker_entry_id=worker_entry_id)
                run, conversation = owned.run, owned.conversation
                identity = await current_run_identity(session, run=run, conversation=conversation)
                config, _ = await _version(session, owned=owned)
                prior = await session.scalar(select(RunEvent.id).where(RunEvent.tenant_id == attempt.tenant_id,
                    RunEvent.run_id == attempt.run_id,
                    RunEvent.event_type == "routing.execution_started").limit(1))
                ledger = await session.scalar(select(ModelCallLedger.id).where(ModelCallLedger.tenant_id == attempt.tenant_id,
                    ModelCallLedger.run_id == attempt.run_id).limit(1))
                business_activity = await routing_business_activity_exists(session,
                    tenant_id=attempt.tenant_id, run_id=attempt.run_id)
                product = await session.scalar(select(Message.id).where(Message.tenant_id == attempt.tenant_id,
                    Message.conversation_id == attempt.conversation_id, Message.turn_sequence == run.input_sequence,
                    Message.role == MessageRole.ASSISTANT).limit(1))
                if (prior is not None or ledger is not None or business_activity or product is not None
                    or await routing_outcome_event_exists(session, tenant_id=attempt.tenant_id, run_id=attempt.run_id)
                    or owned.attempt.side_effect_state != "none" or owned.attempt.checkpoint_id is not None):
                    raise RoutingRequestDenied("ROUTING_RUN_REQUEST_ALREADY_USED")
                message = await session.scalar(select(Message).where(Message.id == run.input_message_id,
                    Message.tenant_id == attempt.tenant_id, Message.conversation_id == attempt.conversation_id,
                    Message.user_id == identity.principal.user_id, Message.role == MessageRole.USER,
                    Message.turn_sequence == run.input_sequence))
                if (message is None or run.input_sequence is None or not message.content.strip()
                    or message.content_hash != content_digest(message.content)):
                    raise RoutingRequestDenied("ROUTING_RUN_REQUEST_INPUT_INVALID")
                loaded = await load_run_input_context(session, run=run, conversation=conversation,
                    input_message=message, identity=identity, settings=settings)
                snapshot = loaded.snapshot
                if owned.attempt.context_snapshot is not None and ContextSnapshot.model_validate(
                    owned.attempt.context_snapshot) != snapshot:
                    raise RoutingRequestDenied("ROUTING_RUN_REQUEST_CONTEXT_CHANGED")
                await assert_history_context_authorized(session, snapshot=snapshot, identity=identity,
                    conversation_id=conversation.id, run=run)
                context = SupportContext(tenant_id=run.tenant_id, user_id=identity.principal.user_id,
                    roles=identity.principal.roles, conversation_id=conversation.id, run_id=run.id,
                    correlation_id=run.correlation_id, attempt_id=attempt.attempt_id,
                    execution_generation=attempt.generation, owner_id=attempt.owner_id)
                messages: list[AnyMessage] = [HumanMessage(content=content, id=str(message_id)) if role == "user"
                    else AIMessage(content=content, id=str(message_id))
                    for (role, content), message_id in zip(loaded.product_messages, loaded.product_message_ids, strict=True)]
                if (not messages or messages[-1].id != str(message.id) or messages[-1].type != "human"
                    or messages[-1].content != message.content):
                    raise RoutingRequestDenied("ROUTING_RUN_REQUEST_INPUT_ORDER_INVALID")
                request = ModelRequest[SupportContext](model=_RoutingRequestModel(cache=False, callbacks=[], verbose=False),
                    messages=messages, system_message=SystemMessage(content=_PLATFORM_DATA_POLICY + config.prompt.system_prompt),
                    response_format=ProviderStrategy(SupportAnswer, strict=True), tools=[], tool_choice=None,
                    model_settings={}, state={"messages": messages}, runtime=Runtime(context=context))
                owned.attempt.context_snapshot = snapshot.model_dump(mode="json")
                # 首尾重新核对原版本和当前主体；不把先前读取当成调用批准。
                final_identity = await current_run_identity(session, run=run, conversation=conversation)
                final_config, _ = await _version(session, owned=owned)
                if (final_identity.principal != identity.principal or final_config != config
                    or manifest_digest(owned.attempt.context_snapshot) != manifest_digest(snapshot.model_dump(mode="json"))):
                    raise RoutingRequestDenied("ROUTING_RUN_REQUEST_SCOPE_CHANGED")
            return request
    except asyncio.CancelledError:
        raise
    except RoutingRequestDenied:
        raise
    except Exception:
        raise RoutingRequestDenied("ROUTING_RUN_REQUEST_UNCONFIRMED") from None
