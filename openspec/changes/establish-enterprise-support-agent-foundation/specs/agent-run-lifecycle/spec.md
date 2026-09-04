## ADDED Requirements

### Requirement: Run 创建与排队
每条成功接受的用户消息 SHALL 在同一事务中创建一个初始状态为 `queued` 的 Agent Run；事务提交后 SHALL 将 Run 标识发布到 Redis 队列。

#### Scenario: 消息成功接受
- **WHEN** 新用户消息和 Run 已持久化
- **THEN** API 返回消息 ID、Run ID、`queued` 状态和事件订阅地址

### Requirement: 确定性状态转换
Run SHALL 只允许 `queued → running → completed|failed|cancelled`、`queued → cancelled` 或 `running → cancelling → cancelled` 的状态转换。Worker SHALL 以原子条件更新声明 Run，防止重复消费产生并行执行。

#### Scenario: 重复队列投递
- **WHEN** 同一 Run ID 被 Worker 重复消费
- **THEN** 只有一个消费者能够从 `queued` 声明为 `running`，其他消费者安全退出

### Requirement: Run 查询与取消
已授权用户 SHALL 能查询 Run 状态并取消尚未进入终态的 Run。取消 SHALL 是幂等操作。

#### Scenario: 取消已排队 Run
- **WHEN** 用户取消属于自己的 `queued` Run
- **THEN** Run 转为 `cancelled` 并产生 `run.cancelled` 事件

#### Scenario: 重复取消终态 Run
- **WHEN** 用户重复取消已完成、失败或取消的 Run
- **THEN** API 返回当前终态且不产生非法状态转换
