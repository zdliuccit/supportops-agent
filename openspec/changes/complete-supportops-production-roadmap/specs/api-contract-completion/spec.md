## ADDED Requirements

### Requirement: 剩余普通列表分页
普通工具目录、知识来源和知识版本列表 SHALL 按统一 page/page_size 与 items/total/page/page_size/pages 契约迁移，查询在数据库分页并有稳定唯一排序；明确完整集合接口除外。

#### Scenario: 后续页与越界页
- **WHEN** 用户请求第二页或超过总页数
- **THEN** 返回准确过滤总数和请求页码，越界 items 为空，租户过滤先于分页

#### Scenario: 旧数组消费者
- **WHEN** 知识版本数组响应改为分页对象
- **THEN** Web 与 API 同步迁移，升级窗口和兼容策略明确，不在客户端取全量再切片

### Requirement: 新状态与错误契约同步
新增审批、对账、结构化结果与 job 状态 SHALL 同步 OpenAPI、Pydantic、前端类型和恢复事件；错误 SHALL 提供稳定码而不泄露资源或秘密。

#### Scenario: 刷新等待审批
- **WHEN** 客户端恢复有新等待状态的 Run
- **THEN** 正确显示等待与合法操作，无未知状态空白或强制当作失败

#### Scenario: 跨租户新资源
- **WHEN** 主体请求其他租户的审批或动作 ID
- **THEN** 遵循资源隐藏策略拒绝，分页和详情均不泄露
