# 会话内短期记忆

## 已实现语义

短期记忆指同一 `Conversation` 内跨轮次保留的上下文，不等同于用户跨会话的长期记忆。

- 产品 `messages` 是用户可见历史事实源。
- LangGraph PostgreSQL checkpoint 保存图状态和工具轨迹。
- `thread_id` 由 `tenant_id` 与 `conversation_id` 派生，防止跨租户混用。
- 没有 checkpoint 时，从产品消息重建用户/助手消息；有 checkpoint 时只追加本轮输入。
- checkpoint 具有独立保留和清理周期。

## 一致性边界

产品消息与 checkpoint 双写发生异常时，Run 进入稳定失败状态并记录一致性错误；不能把内部 checkpoint 当成用户可见历史直接返回。

## 相关代码

`packages/support_core/src/supportops_core/agent_runtime.py`、`worker_runtime.py`。
