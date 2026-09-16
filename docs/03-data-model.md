# 核心数据模型

## 聚合关系

```mermaid
erDiagram
    TENANT ||--o{ USER : contains
    TENANT ||--o{ AGENT : owns
    AGENT ||--|| AGENT_DRAFT : has
    AGENT ||--o{ AGENT_VERSION : publishes
    AGENT_VERSION }o--|| MODEL_ENDPOINT_VERSION : binds
    AGENT ||--o{ AGENT_ACCESS_GRANT : grants
    AGENT ||--o{ CONVERSATION : serves
    CONVERSATION ||--o{ MESSAGE : contains
    CONVERSATION ||--o{ AGENT_RUN : executes
    AGENT_RUN ||--o{ RUN_EVENT : emits
    AGENT_RUN ||--o{ AGENT_RUN_OBSERVATION : observes
    AGENT ||--|| AGENT_RUNTIME_HEALTH : summarizes
```

## 事实来源

- `Conversation` 与 `Message`：用户可见对话事实。
- `AgentRun` 与 `RunEvent`：一次执行的业务状态和事件流事实。
- `AgentRunObservation`：模型、工具和阶段观测，不覆盖原始 Run 结果。
- `AgentVersion` 与 `ModelEndpointVersion`：不可变运行配置边界。
- `AgentRuntimeHealth`：由运行事实和 Worker lease 派生的健康快照，不是请求事实。

所有业务查询必须从认证上下文取得 `tenant_id`，不能接受客户端传入租户扩权参数。
