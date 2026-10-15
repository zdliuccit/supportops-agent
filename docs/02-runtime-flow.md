# Agent 请求运行链路

新会话选版：API先验证当前Agent使用授权并锁Agent，再读取固定范围灰度。服务器批准策略、基线/候选完整门禁和Worker巡检全部有效时，按tenant/Agent/rollout/user稳定哈希及受控万分比选择版本，并保存灰度ID/观察轮次/组别/桶；否则选择当前活动基线，正式环境继续检查其门禁。旧会话和发送后的Run固定原版本，不重新选版。控制面启动/恢复/扩大新增观察轮次，Worker只会复检或暂停；扩大必须人工提交当前修订，激活同事务关闭灰度。0044尚未执行，当前没有适用通过实测，不能启用灰度。

工具数据进入模型前先按封闭输出Schema/字段/完整JSON预算校验和脱敏，再封装model_tool_data_v1不可信数据。逐模型物理请求把实际ToolMessage与同租户/Run/attempt/call ID及账本结果摘要核对，将已使用的工具来源并集持久化到ContextSnapshot；用户同形JSON不产生来源，SDK结构化回答重试不充当业务工具数据。模型返回、正文增量写入与最终产品事务复查当前目录/实现/角色/配置、Mock启用/工单授权及目标版本；动态Mock观察按原观察时间最多五分钟，旧证明不能靠裁剪或重连接收延寿。来源变化停止本轮，已声明同请求公共标量冲突保留标记并要求复核，不推断根因；知识ACL/自然有效期仍单独复检。源码/Prompt范围变化使旧评测不再适用，API/Worker需同步升级；未执行新链路验收。

## 主流程

```mermaid
sequenceDiagram
    participant UI as Web 工作台
    participant API as FastAPI
    participant DB as PostgreSQL
    participant Q as Redis
    participant W as Worker
    participant A as AgentFactory
    participant L as 模型端点

    UI->>API: POST /conversations/{id}/messages + Idempotency-Key
    API->>DB: 同事务写入 Message/Run/Event/通知 Outbox
    API->>Q: 提交后尽力分发 Run 通知
    W->>DB: 分批领取 Outbox，有限重试/死信
    W->>Q: 至少一次发送相同 Run UUID
    API-->>UI: 返回 Run 标识
    W->>DB: 锁会话，只领取最早未结束 queued Run
    W->>A: 加载固定 Agent/模型/工具快照
    A->>DB: 读取 checkpoint
    A->>L: 执行模型调用与工具调用
    L-->>A: 结构化 SupportAnswer
    A->>DB: 写 checkpoint、观察数据和产品消息
    W->>DB: 写入终态 Run/Event
    UI->>API: GET /runs/{id}/events
    API-->>UI: 可恢复 SSE 事件流
```

## 关键不变量

- 消息提交必须带幂等键；重复请求不能创建第二个业务 Run。
- 会话建立时固定 Agent 版本，后续发布或回滚不改变历史会话语义。
- Worker 重新读取运行快照并再次检查租户、模型能力、工具绑定和凭据 revision。
- checkpoint 写入失败会产生稳定错误码，不能伪造成功回答。
- SSE 支持 `Last-Event-ID` 或 cursor 恢复，事件按 Run 内单调序号追加。

## 服务调度与停机边界

心跳、通知投递、恢复、健康刷新、checkpoint 清理与消费独立运行；维护操作有超时和周期重试。Worker 默认最大并发 1，满载时停止领取，真正领取后才统计活动 Run；Redis 消息深度与数据库 queued Run 数分开记录。

SIGTERM/SIGINT 先停止领取，在宽限期保持心跳；超时后协作取消在途 Run，隔离失败尝试并写取消终态。未确认取消会保留 degraded/过期租约并报错，不盲目移交。RunAttempt 租约/失效执行者隔离、只读有限崩溃恢复及输入序号已有实现；租户公平调度/完整累计预算仍待实施；测试按当前约定暂缓。

## 尝试、工具与知识证据

会话按 input_sequence 排队并持一个有效 attempt 许可，事件/最终提交校验 owner/generation/lease。attempt 使用独立 checkpoint thread，产品事务成功才确认指针；未知副作用阻止自动重跑。调用/提交查询当前用户/租户/角色/grant，本地工具统一经过 Schema/当前策略/撤销/额度/风险门禁。Mock 需审批动作由独立一次性审批链执行，真实副作用仍拒绝。

知识 Outbox 由独立任务构建关键词代次/分块并有限重试；检索先筛租户/当前版本/有效期/ACL，再读正文。evidence 绑定本 Run/attempt，最终验证与 Message/SupportResult 同事务保存；result.ready 只含消息标识，详情读取再次授权。向量暂缓，新迁移/新功能测试未执行。

工具本地并发先等待有界许可，数据库仍原子计数防跨进程绕过；租户额度用兼容外键校验的 NO KEY UPDATE 锁，保持互斥并避免子表追踪插入与额度申请互相等待。固定模型停用立即阻止后续领取/调用/提交。Mock 诊断默认关闭，生产拒绝，最终结果由服务端标注数据性质。

隔离 429 诊断通过相同工具网关执行固定十节点/最多五查询；日志/配额/路由/事件事实持久化绑定调用，唯一且跨源一致候选还需获准知识规则声明。未确认/失败生成接管包，不发起虚构模型调用。阶段保存 ContextSnapshot 并发 SSE 元数据，最终报告与消息同事务保存；只读恢复校验同 Run/参数/版本/当前策略及五分钟有效期，知识引用重新检索。

## Mock 审批与客服业务等待

回答/结果/checkpoint 和需审批提案的 waiting_approval 同事务提交，模型 attempt 结束并释放会话执行许可；同会话后续输入保留排队，不跳过业务等待。指定非本人批准后 queued 动作行承担事务 Outbox，Run 转 waiting_action；独立动作消费者领取并一次消费批准，出站复检冻结范围/资源版本/期限/原主体/审批者当前权限。业务等待不重跑模型，实际执行与等待时间分别表达。

客服显式 ticket_handle 授权后认领，MockSupportCase 冻结关联问题/来源会话的 Agent 写动作。动作提交与客服命令在共同会话/资源锁边界复查接管，读查询不被冻结；关闭/释放明确结束接管。命令、目标版本变化和审计同事务持久化。读取升级包另需 support_context_read，知识证据复检原主体和读取者当前 ACL；没有默认管理员业务越权。
