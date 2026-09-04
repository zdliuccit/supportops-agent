## ADDED Requirements

### Requirement: 会话与消息交互
Web Chat SHALL 允许开发用户配置本地访问令牌、创建会话、输入非空消息并展示服务端返回的 Run 状态。

#### Scenario: 发送新消息
- **WHEN** 用户在已有会话中提交非空消息
- **THEN** 客户端生成幂等键、发送消息并开始订阅对应 Run 事件

### Requirement: 事件增量呈现
Web Chat SHALL 按 SSE 事件序号处理文本增量和状态事件，重连时不得重复拼接已经处理的文本。

#### Scenario: 流式事件完成
- **WHEN** 客户端收到 `text.delta` 和 `run.completed`
- **THEN** 页面增量显示助手内容并将 Run 标记为完成

### Requirement: 关键界面状态
Web Chat SHALL 明确呈现初始化、加载、空会话、发送中、运行中、失败、取消和连接中断状态，并提供可访问的表单标签和状态提示。

#### Scenario: SSE 连接失败
- **WHEN** 事件流建立或读取失败
- **THEN** 页面保留已收到内容、显示错误并允许用户重试订阅
