## ADDED Requirements

### Requirement: 统一工具执行入口
所有本地、业务 API 和 MCP 工具 SHALL 经同一授权、Schema、限额、风险、审批、输出过滤与审计边界；Registry SHALL 只提供发现与治理元数据。

#### Scenario: 不同协议相同风险
- **WHEN** 本地工具与远程 MCP 工具都尝试执行同类高风险动作
- **THEN** 均受相同门禁，不因适配协议不同绕过控制

#### Scenario: 新来源工具
- **WHEN** MCP Server 返回未知或变更的描述/Schema
- **THEN** 先做来源/版本/allowlist 审核，未经授权不暴露给模型

### Requirement: 连接与出站安全
连接器 MUST 校验目标地址、重定向、响应大小/Schema 与超时，并使用明确受众和最小范围凭据；不得把平台用户 JWT 任意转发或允许模型指定秘密。

#### Scenario: 重定向到私网
- **WHEN** 外部工具地址解析或重定向指向禁止目标
- **THEN** 拒绝连接并审计，无敏感请求出站

#### Scenario: 工具输出含秘密
- **WHEN** 上游返回令牌或敏感字段
- **THEN** 在进入模型、SSE 和日志前过滤，并保留安全错误摘要

### Requirement: 连接器健康与失效
平台 SHALL 记录连接状态、能力与版本，提供测试/停用/熔断/恢复路径和有限重试；写重试由动作对账策略决定。

#### Scenario: 停用外部连接
- **WHEN** 管理员撤销连接或其凭据
- **THEN** 排队及后续工具调用拒绝，既有审计保持可追踪
