## ADDED Requirements

### Requirement: 结构化事件持久化
Run 事件 SHALL 以每个 Run 单调递增的序号持久化，至少支持 `run.queued`、`run.started`、`stage.started`、`text.delta`、`stage.completed`、`run.completed`、`run.failed` 和 `run.cancelled`。

#### Scenario: Worker 完成占位执行
- **WHEN** Worker 成功处理一个 Run
- **THEN** 客户端能够按序读取开始、阶段、文本增量和完成事件

### Requirement: 可恢复 SSE
事件接口 SHALL 使用 `text/event-stream`，为每个事件提供 ID、类型和 JSON 数据，并支持通过 `Last-Event-ID` 或游标参数从断点之后继续读取。

#### Scenario: 客户端断线重连
- **WHEN** 客户端携带已消费事件序号重新连接
- **THEN** API 只发送该序号之后的事件，保持顺序且不丢失已持久化事件

### Requirement: 流终止与心跳
SSE SHALL 在 Run 进入终态且历史事件发送完毕后结束，并在等待新事件期间发送不包含业务数据的心跳注释。

#### Scenario: 等待 Worker 事件
- **WHEN** Run 尚未结束且暂时没有新事件
- **THEN** 连接保持活动并周期性发送心跳，不生成虚假业务事件
