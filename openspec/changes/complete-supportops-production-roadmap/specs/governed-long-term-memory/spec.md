## ADDED Requirements

### Requirement: 记忆启用与来源确认
长期记忆 SHALL 仅在业务决策与质量/隐私评测通过后启用；保存租户、主体、类型、来源、确认状态、有效期和 policy 版本，模型推断不得自动成为确认事实。

#### Scenario: 模型推断偏好
- **WHEN** Agent 从对话推测用户偏好
- **THEN** 按策略请求确认或标为候选，不覆盖正式知识/业务事实

### Requirement: 记忆政策与不可变修订设计
MemoryPolicy/MemoryRecord SHALL 固定租户、本人、Agent与原Agent版本、政策版本/摘要、独立记录修订/前版、本人消息来源、正文摘要和有效期；确认与撤销 SHALL 精确绑定修订范围摘要。前置设计仅提供严格不可变memory_policy_design_v1/memory_record_design_v1，MUST NOT 产生实际启用、存储、召回或擦除许可。

#### Scenario: 候选政策没有批准期限或范围
- **WHEN** 类型/Agent允许范围为空，或对应期限/政策到期未配置
- **THEN** 静态政策配对拒绝，不把空值解释为所有主体/类型或永久有效；政策继续draft、批准/执行/召回均为false

#### Scenario: 旧确认用于新内容或延长期限
- **WHEN** 内容、来源、主体/Agent、政策或有效期生成新修订
- **THEN** 新scope摘要必须重新确认，原确认不能迁移；保留原前版/替代关联，不原地覆盖或延长旧修订

#### Scenario: 擦除申请与删除投影
- **WHEN** 本人提出擦除申请，或未来独立维护产生可核对的tombstone
- **THEN** 分别表达withdrawn与tombstoned，后者只呈现无正文投影；申请不表示物理删除，设计契约不生成实际删除事实或绕过W21法律保留

### Requirement: 权限召回与纠错删除
记忆召回 MUST 校验当前权限与有效期，用户 SHALL 能查看、纠错、撤回并请求删除；索引/缓存与备份恢复流程 SHALL 遵循删除事实。

#### Scenario: 另一用户相似偏好
- **WHEN** 不同主体的记忆与当前问题相似
- **THEN** 默认不召回未经授权主体的内容

#### Scenario: 纠错或删除记忆
- **WHEN** 用户撤回已确认记忆
- **THEN** 后续召回及缓存失效，相关留存/擦除流程可审计

### Requirement: 记忆收益与失败退路
启用记忆 SHALL 比较关闭时的质量、成本与敏感泄露结果，并支持按租户/版本禁用而保留业务事实。

#### Scenario: 评测无收益
- **WHEN** 记忆版本未改善任务结果或有安全退化
- **THEN** 保持功能关闭并保留候选待办，不强制上线
