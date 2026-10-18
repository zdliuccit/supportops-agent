## ADDED Requirements

### Requirement: 版本化产品回答
平台 SHALL 持久化 answer、summary、合法 citations、suggested_actions 和升级状态，并关联证据与 Run；建议动作不得表示已执行。

#### Scenario: 完成后刷新
- **WHEN** 用户刷新已完成回答
- **THEN** 完整结构化结果可重建，引用与升级信息不丢失

#### Scenario: 旧正文消息
- **WHEN** 会话只有历史 answer 正文
- **THEN** 按兼容模式显示，不伪造历史引用或外部执行结果

### Requirement: 流式恢复与授权
新结果字段 SHALL 与现有 text.delta/text.replace 及游标恢复兼容；证据读取、动作结果和升级信息 MUST 做当前权限检查。

#### Scenario: 正文与结果分阶段到达
- **WHEN** 正文流完成后结构化结果通过校验
- **THEN** 保存结果并通知客户端，前端刷新获得同一已提交事实

#### Scenario: 正文生成被取消
- **WHEN** 用户取消 Run
- **THEN** 保留合法已生成正文，不写迟到完整回答或将建议标成成功动作

#### Scenario: 结果刷新后旧引用抽屉仍打开
- **WHEN** 已打开证据抽屉的聊天结果对象被刷新或替换，包括引用改为不可用
- **THEN** 当次渲染即隐藏旧抽屉正文，清理选择/详情/错误和加载状态并作废旧请求；关闭动画也不保留旧正文，迟到响应不能重新打开。引用详情请求显式no-store，每次展开重新读取，不宣称新增权限变化推送或后台轮询
