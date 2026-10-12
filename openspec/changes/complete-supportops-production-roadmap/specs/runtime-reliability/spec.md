## ADDED Requirements

### Requirement: 独立心跳与有界调度
Runtime SHALL 将服务心跳、任务消费、恢复与维护分离，并配置有限并发、背压和优雅停机；活动数量来自真实执行事实。

#### Scenario: 长任务期间租约有效
- **WHEN** 模型调用持续超过服务租约周期
- **THEN** 心跳仍按周期更新，活动 Run 数准确且不误判服务离线

#### Scenario: 停机停止领取
- **WHEN** Worker 收到停机信号
- **THEN** 停止新领取并在限定时间内完成、移交或取消在途任务，租约状态可解释

### Requirement: 原子通知与持久化任务
平台 SHALL 将 Message/Run/通知 Outbox 原子提交，采用至少一次分发与幂等领取；模型探测任务 SHALL 有持久化状态、有限重试和故障查询入口。

#### Scenario: Redis 暂不可用
- **WHEN** 数据库已提交而 Redis 入队失败
- **THEN** Run 与 Outbox 保留，恢复后可分发且不重复生成业务 Run

#### Scenario: 探测进程重启
- **WHEN** 连接测试开始后 API 进程退出
- **THEN** 新的执行者恢复探测或收敛为明确终态，不永久显示 running

### Requirement: Run 尝试与失效执行者保护
Runtime SHALL 保存执行 attempt、owner、租约和 fencing generation；恢复 SHALL 覆盖过期 running/cancelling，并保护产品与 checkpoint 写入，未知副作用先交由对账。

#### Scenario: 领取后崩溃
- **WHEN** 执行者领取 Run 后崩溃
- **THEN** 恢复器依照租约和副作用事实恢复或终止，原固定版本引用不改变

#### Scenario: 旧执行者迟到
- **WHEN** 新 attempt 生效后旧执行者提交结果或 checkpoint
- **THEN** 旧写入被拒绝或隔离，不污染新结果、当前提交指针或外部动作

#### Scenario: 跨层提交失败
- **WHEN** checkpoint 已写但产品消息事务失败
- **THEN** 失败 attempt 状态隔离，后续从有效业务事实恢复，不伪造完成

### Requirement: 同会话串行与取消
平台 SHALL 对同租户会话串行调度，固定上下文至本轮输入位置；取消 SHALL 在排队、执行、审批和对账状态具备明确终止或人工处置语义。

#### Scenario: 并发发送
- **WHEN** 同一会话提交不同幂等键的两条消息
- **THEN** 第二条显式排队，第一轮不读取第二条输入，不并发写相同有效 checkpoint

#### Scenario: 执行取消
- **WHEN** 用户在模型或工具调用期间取消
- **THEN** 终止后续非必要调用和正文写入；已发生副作用保持回执并完成必要对账
