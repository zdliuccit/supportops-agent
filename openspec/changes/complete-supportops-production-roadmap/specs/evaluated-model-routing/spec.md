## ADDED Requirements

### Requirement: 基于证据启动路由
动态路由 SHALL 在固定模型质量/成本/可用性瓶颈与评测批准后启用；策略固定到 Agent 版本，解析允许模型版本与数据边界。

#### Scenario: 缺少启动证据
- **WHEN** 团队仅因目标图包含 Model Router 而请求启用
- **THEN** 保留候选任务，继续固定模型，不自动扩大可用模型集合

### Requirement: 调用选型与历史兼容
每次模型选择 SHALL 满足工具/结构化/上下文能力、数据地域与累计预算并记录实际版本；旧会话固定模型语义 MUST 保留。

#### Scenario: 选择不兼容模型
- **WHEN** 低成本候选不支持当前工具或结构化策略
- **THEN** 排除候选并给出原因，不以降级校验换取成功

#### Scenario: 历史会话遇新策略
- **WHEN** 路由策略发布后恢复旧版本会话
- **THEN** 继续原固定模型，新策略只作用于明确采用它的新版本会话

### Requirement: 路由版本与候选解析契约
未来路由配置 SHALL 采用显式schema_version=3与固定/路由判别联合，引用同租户不可变model_routing_policy_v1及摘要。策略候选 SHALL 指定确切endpoint/model/version及请求、能力、价格和数据边界摘要；发布解析 SHALL 核对当前状态、最新匹配探测与当前凭据revision，保存完整resolved_model_routing_v1集合及新发布清单版本。前置设计 MUST NOT 放开现有v2空fallback或启用运行时路由。

#### Scenario: 指定候选验证已陈旧
- **WHEN** 最新探测未通过，或与原模型版本/当前凭据revision/请求摘要不匹配
- **THEN** 拒绝发布原策略，不挑选旧通过结果，不替换最新模型或静默删除候选

#### Scenario: 旧部署收到新配置
- **WHEN** API、Web或Worker不支持v3或新发布清单
- **THEN** 明确拒绝不兼容版本并阻止新路由会话，保留旧v2配置字节、固定模型与会话语义；新策略只随显式新AgentVersion生效

### Requirement: 受控失败切换
失败切换 SHALL 有限、可追踪且执行前保留授权/预算，不重放已完成写工具、不默默替换 revoked 凭据或选择最新未验证模型。

#### Scenario: 主模型故障
- **WHEN** 允许主模型在未完成回答时失败
- **THEN** 仅在匹配能力与策略的固定候选间切换并计量重试

#### Scenario: 工具已产生副作用
- **WHEN** 模型故障发生在工具执行后
- **THEN** 复用已有动作事实并对账，不重复提交动作
