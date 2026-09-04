## ADDED Requirements

### Requirement: 创建会话
已认证用户 SHALL 能在当前租户内创建会话，服务端 SHALL 生成会话 ID、创建时间和活动状态。

#### Scenario: 成功创建会话
- **WHEN** 已认证用户提交可选标题
- **THEN** API 返回属于当前用户和租户的新会话

### Requirement: 读取会话
用户 SHALL 能读取自己有权访问的会话及按时间排序的消息。

#### Scenario: 读取已有会话
- **WHEN** 会话属于当前用户和租户
- **THEN** API 返回会话元数据和消息列表，不包含其他租户数据

### Requirement: 消息持久化与幂等
发送消息 SHALL 要求非空内容和 `Idempotency-Key`，并在同一租户、主体和会话范围内保证重复请求返回同一消息与 Run。

#### Scenario: 重复提交消息
- **WHEN** 客户端使用相同幂等键重复提交同一消息请求
- **THEN** API 返回第一次创建的消息和 Run，不产生重复记录或队列任务

#### Scenario: 幂等键复用不同内容
- **WHEN** 客户端使用已有幂等键提交不同消息内容
- **THEN** API 返回冲突错误且不创建新消息或 Run
