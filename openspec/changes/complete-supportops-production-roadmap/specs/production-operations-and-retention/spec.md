## ADDED Requirements

### Requirement: 凭据服务与执行隔离
production SHALL 通过批准 provider 获取受限凭据并隔离连接器执行，支持轮换/撤销与审计；模型、日志、评测与不可信执行不得接触秘密。

#### Scenario: 轮换保留历史引用
- **WHEN** 凭据创建新 revision 后恢复旧 Run
- **THEN** 保持审计引用；旧 revision 撤销时拒绝调用，不能默默改用新秘密

#### Scenario: 恶意内容请求秘密
- **WHEN** 工具或知识内容要求读取连接器环境
- **THEN** 权限/隔离阻止秘密访问，输出与日志不含秘密

### Requirement: 部署备份与运维
交付 SHALL 提供 API/Worker/Web、迁移、TLS/反向代理/流式配置、配置校验、备份恢复与回滚 Runbook；RTO/RPO/负载在试运行前确认，远程部署另需授权。

#### Scenario: 恢复隔离备份
- **WHEN** 在隔离环境恢复业务库与 checkpoint schema
- **THEN** 版本、消息、索引/动作恢复关系可验证，Redis 通知可从事实重建

#### Scenario: 升级失败
- **WHEN** 新版本迁移/服务启动未通过检查
- **THEN** 采用批准回滚策略，不自动破坏性删除已有业务数据

### Requirement: 保留与擦除事实
数据策略 SHALL 覆盖消息、checkpoint、事件、观测、知识、记忆、凭据 revision、外部回执与备份，区分软删除、法定保留与授权擦除；维护流程不弱化普通 ORM 保护。

#### Scenario: 授权擦除申请
- **WHEN** 合法主体申请擦除且无保留阻止
- **THEN** 受控流程清理相应副本/索引/缓存，保留最小合规证明

#### Scenario: 备份恢复被删除数据
- **WHEN** 恢复包含既往擦除内容的旧备份
- **THEN** 重新应用删除/隔离事实，内容不能重新对用户或模型可见

### Requirement: 条件规模化与可用性
HA、分区/聚合、队列背压与容量扩展 SHALL 基于负载和恢复瓶颈启用，定义故障域、配额与服务状态；容量措施不得削弱隔离、版本或审计。

#### Scenario: 容量达到阈值
- **WHEN** 批准负载下队列/查询/存储达到瓶颈
- **THEN** 按测量启用扩展并复测质量、授权和 SLO，不无依据提前拆微服务
