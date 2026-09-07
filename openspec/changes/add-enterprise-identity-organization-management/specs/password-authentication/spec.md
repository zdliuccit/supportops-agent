## ADDED Requirements

### Requirement: 邮箱密码登录
系统 SHALL 接受规范化邮箱和密码，在用户存在、状态有效且密码哈希匹配时签发包含可信租户和角色声明的短期 JWT；失败响应 MUST 不暴露邮箱是否存在。

#### Scenario: 有效用户登录
- **WHEN** active 用户提交正确邮箱与密码
- **THEN** 系统更新最近登录时间并返回 Bearer Token 和安全用户资料

#### Scenario: 无效凭据登录
- **WHEN** 用户提交未知邮箱或错误密码
- **THEN** 系统返回相同的 401 错误且不签发令牌

#### Scenario: 停用用户登录
- **WHEN** disabled 用户提交正确凭据
- **THEN** 系统拒绝登录且不暴露停用之外的敏感资料

### Requirement: 密码安全存储
系统 MUST 使用带独立随机盐的现代单向哈希保存密码，MUST NOT 在数据库、API、日志或审计事件中保存或返回明文密码。

#### Scenario: 管理员创建用户
- **WHEN** 管理员为新用户设置初始密码
- **THEN** 数据库仅保存版本化密码哈希且 API 响应不包含哈希

### Requirement: 初始管理员引导
系统 SHALL 在显式启用且数据库不存在任何用户时，依据服务端环境变量幂等创建默认公司与未分配部门的平台管理员；生产环境 MUST 拒绝已知示例密码。

#### Scenario: 首次本地启动
- **WHEN** 本地数据库无用户且管理员引导已启用
- **THEN** 系统创建配置邮箱对应的 active 平台管理员且可以登录

#### Scenario: 再次启动
- **WHEN** 引导管理员或其他用户已经存在
- **THEN** 系统不覆盖密码、角色、公司或组织资料

### Requirement: 已停用身份即时失效
受保护 API SHALL 在解析 JWT 后加载数据库用户并校验状态，MUST 拒绝已停用用户的尚未过期令牌。

#### Scenario: 管理员停用已登录用户
- **WHEN** 被停用用户使用先前签发的有效 JWT 请求 API
- **THEN** 系统返回 403 且不执行操作

### Requirement: 系统用户作为唯一 JWT 主体
系统 MUST 使用系统 User UUID 作为 JWT `sub`，MUST NOT 根据外部 subject 自动创建、映射或兼容用户。

#### Scenario: JWT 引用不存在用户
- **WHEN** 有效签名 JWT 的 `sub` 不对应当前租户中的系统用户
- **THEN** 系统返回 403 且不创建租户或用户
