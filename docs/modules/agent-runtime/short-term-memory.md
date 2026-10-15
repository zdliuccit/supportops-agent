# 会话内短期记忆

## 已实现语义

短期记忆指同一 `Conversation` 内跨轮次保留的上下文，不等同于用户跨会话的长期记忆。

- 产品 `messages` 是用户可见历史事实源。
- LangGraph PostgreSQL checkpoint 保存图状态和工具轨迹。
- 正式执行 `thread_id` 由 tenant/conversation/run/attempt 派生，尝试隔离；旧直接调用保留会话 thread 兼容。
- 没有 checkpoint 时，从产品消息重建用户/助手消息；有 checkpoint 时只追加本轮输入。
- checkpoint 具有独立保留和清理周期。

## 有界历史与来源摘录

Worker SQL 只读取本轮 input_sequence 之前的有限旧轮次和首轮最多两条消息；当前输入缺失时拒绝执行，不能误答旧问题。消息 ID 保留到图输入，ContextSnapshot 保存来源 message/role/sequence/digest；历史答案先投影当前知识 ACL/有效快照、诊断与动作授权，失效或损坏结果不能退回旧正文。

extractive_history_v1 保存最多 12 条脱敏摘录，用户最多 400 字符、答案优先使用已有 summary（最多 1000 字符），没有 summary 则截取答案片段。摘录附原消息/输入序号/digest、旧 evidence ID、截断和冲突/升级未完成标记；不是完整语义总结或确认事实，也不是长期记忆。额外模型总结未启用，不增加未经计量的模型调用。

每次模型调用先按完整系统/历史/工具 Schema/结果/结构化输出做保守预算，只裁完整旧用户轮次。预算允许时将此次裁掉的首个问题和最近来源摘要作为 HumanMessage 的不可信 JSON 数据加入，以 400/160/64 字符逐步缩短；摘要装不下就省略，保留当前问题和当前工具轨迹。旧 evidence ID 仅作历史定位，引用必须重新检索获得本轮证据，摘要不能授予工具/审批权限或声称问题已解决。

调用前后、正文事件和最终产品提交复查历史来源与当前权限，历史/本轮证据按统一文档顺序锁定；失效以 HISTORY_CONTEXT_AUTHORIZATION_CHANGED 结束，不继续消费旧上下文。恢复重新构建产品历史及当前授权，不盲目复用失败 checkpoint。旧 ContextSnapshot 的新增字段默认空以兼容，历史 proof 不回填虚构来源；本次未执行测试/构建/真实 PostgreSQL或性能验收。

物理调用预留事务保存实际进入模型的历史来源 ID；后续调用保留已用来源并集，不能裁掉引用后继续复用已经受其影响的答案来绕过撤权。正文/最终检查仅复检实际已用并集，避免每个正文批次读取已被裁掉且从未使用的全部历史；旧无已用记录的快照保守复检全部证明。来源 ID/序号必须与本轮固定 Run 边界一致，未来输入、重复来源或未知来源许可拒绝。预算紧张时按条目数和摘录长度逐步收缩，摘要仍装不下就省略；新 API/Worker 必须同步理解新增字段，不在严格旧解析器上盲目回退。

## 一致性边界

模型工具来源使用独立的used_tool_sources元数据并集，以同租户/Run/attempt/call ID及账本摘要核对真实ToolMessage，用户输入同形JSON不会成为来源；SDK回答Schema重试与业务工具分开。来源证明持久化先于模型物理调用，重试或裁剪不能移除已经消费的来源；模型返回、正文增量和最终提交重查当前目录/角色/实现/配置及工单权限/目标版本。动态Mock观察按原观察时间最多五分钟，接收/重连不延寿，失效结束本轮；知识ACL/自然有效期继续单独检查。已声明同请求公共标量冲突保留标记并要求复核，不声称通用语义冲突识别。来源快照仅保留ID/摘要/版本/失效时间和必要定位，不存完整工具正文或原始参数；来源查询有开销，性能/恢复验收未执行。

产品消息与 checkpoint 双写发生异常时，Run 进入稳定失败状态并记录一致性错误；不能把内部 checkpoint 当成用户可见历史直接返回。

## 相关代码

`packages/support_core/src/supportops_core/agent_runtime.py`、`worker_runtime.py`、`context_services.py`、`history_context_services.py`、`model_budget_services.py`。
