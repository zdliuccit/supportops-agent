## ADDED Requirements

### Requirement: 健康与就绪检查
API SHALL 提供不依赖外部系统的存活检查和验证 PostgreSQL、Redis 可用性的就绪检查。

#### Scenario: Redis 不可用
- **WHEN** API 进程存活但无法连接 Redis
- **THEN** 存活检查成功、就绪检查失败并返回不包含凭据的依赖状态

### Requirement: 关联 ID
每个 API 请求 SHALL 接受或生成 `correlation_id`，在响应头、结构化日志、消息、Run 和事件中保持关联。

#### Scenario: 请求未提供关联 ID
- **WHEN** 客户端发送请求时没有有效关联 ID
- **THEN** API 生成一个新 ID、写入响应头并用于该请求产生的记录

### Requirement: 安全结构化日志
API 与 Worker SHALL 输出结构化日志，包含时间、级别、服务、事件、关联 ID 和适用的资源 ID，但不得记录访问令牌、密钥、完整消息正文或数据库连接凭据。

#### Scenario: 身份验证失败
- **WHEN** 无效令牌被拒绝
- **THEN** 日志记录失败类别和关联 ID，不记录令牌内容
