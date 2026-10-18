## ADDED Requirements

### Requirement: 独立心跳与有界调度
Runtime SHALL 将服务心跳、任务消费、恢复与维护分离，并配置有限并发、背压和优雅停机；活动数量来自真实执行事实。

#### Scenario: 长任务期间租约有效
- **WHEN** 模型调用持续超过服务租约周期
- **THEN** 心跳仍按周期更新，活动 Run 数准确且不误判服务离线

#### Scenario: 停机停止领取
- **WHEN** Worker 收到停机信号
- **THEN** 停止新领取并在限定时间内完成、移交或取消在途任务，租约状态可解释

#### Scenario: 多租户持续排队
- **WHEN** 多个租户共享队列，某租户持续提交大量请求
- **THEN** 默认 tenant_round_robin 按数据库持久化游标轮转，所有 Worker 共用同一队列名称/策略；保留租户内稳定顺序、会话许可和租户并发额度，重启不清空游标

#### Scenario: 重复或迟到通知对应死信
- **WHEN** Redis 旧通知被消费，但数据库通知事实已进入 dead_letter
- **THEN** 不领取执行；公平调度仅使用成功通知事实，管理员显式恢复后再进入轮转

### Requirement: 原子通知与持久化任务
平台 SHALL 将 Message/Run/通知 Outbox 原子提交，采用至少一次分发与幂等领取；模型探测任务 SHALL 有持久化状态、有限重试和故障查询入口。

#### Scenario: Redis 暂不可用
- **WHEN** 数据库已提交而 Redis 入队失败
- **THEN** Run 与 Outbox 保留，恢复后可分发且不重复生成业务 Run

#### Scenario: 探测进程重启
- **WHEN** 连接测试开始后 API 进程退出
- **THEN** 新的执行者恢复探测或收敛为明确终态，不永久显示 running

#### Scenario: 批次队首的Run或通知已被其他事务锁定
- **WHEN** API或Worker对一个有限且稳定排序的到期候选批次进行分发
- **THEN** 候选仅含ID/Run/tenant且不持有整批锁，逐条独立事务先Run再Outbox并跳过已锁行；锁内复检固定绑定、当前状态/到期及原会话/额度/重试门禁。空领取不证明Run已删除，不能标废弃或消费次数；跳过后继续本批下一项，不因同一队首等待而反复消耗本批预算

#### Scenario: 候选发现后状态已变化或批次中断
- **WHEN** 候选已由其他分发者处理/推迟/进入死信，或当前批次发生异常/取消
- **THEN** 锁内重查阻止陈旧候选投递；每条独立提交，之前已提交记录不因后续失败回滚。通知始终使用原Run UUID，Redis成功而数据库提交未确认时仍允许重复通知，不产生新业务输入；下一次重查数据库，不把候选列表当持久化领取或执行许可

#### Scenario: 分发批次的资源和语义边界
- **WHEN** 调用方指定分发limit或在候选读取后又有其他记录到期
- **THEN** 本次候选数量不超过配置批次（默认20、最大100），limit只能降低；新到期记录由下一次发现，API即时分发仅限原Run且最多一条。SKIP LOCKED不承诺全队无饥饿、表级锁等待或整体IO硬截止，不替代已有Run领取公平性和重复分发验收

#### Scenario: 探测取消或旧执行者迟到
- **WHEN** 管理员取消持久化探测，或旧 owner 在新 generation 生效后返回
- **THEN** 当前 owner/generation/lease、主体权限及固定引用保护阶段、产品终态与模型验证状态；取消不被迟到成功覆盖，重试累计次数保留，探测不使用聊天 checkpoint

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
