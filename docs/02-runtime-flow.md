# Agent 请求运行链路

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
    API->>DB: 校验租户、授权、固定版本并写入 Message/Run
    API->>Q: 提交 Run 通知
    API-->>UI: 返回 Run 标识
    W->>DB: 抢占 queued Run
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
