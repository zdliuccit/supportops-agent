## ADDED Requirements

### Requirement: 访问令牌验证
受保护 API SHALL 验证 JWT 的签名、签发者、受众、有效期、主体、租户和角色声明，并拒绝无效或缺失令牌。

#### Scenario: 有效身份访问
- **WHEN** 请求携带有效令牌且包含 `sub`、`tenant_id` 和允许角色
- **THEN** API 建立可信身份上下文供后续资源授权使用

#### Scenario: 客户端伪造租户
- **WHEN** 请求体或查询参数中的租户与令牌声明不一致
- **THEN** API 忽略不可信租户或拒绝请求，不扩大令牌授予的范围

### Requirement: 资源范围隔离
所有会话、消息、Run 和事件查询 SHALL 以可信 `tenant_id` 和主体资源范围过滤；不存在与无权访问 SHALL 对外返回不可区分的资源未找到响应。

#### Scenario: 跨租户读取会话
- **WHEN** 有效用户请求另一个租户的会话 ID
- **THEN** API 返回资源未找到且不泄露该资源是否存在
