## ADDED Requirements

### Requirement: 登录与会话安全
生产登录 SHALL 无固定账号/密码预填；浏览器令牌存储、过期、注销和撤销 SHALL 采用经评估的明确契约，不能把 localStorage helper 名称当作已有刷新能力。

#### Scenario: 生产首次打开
- **WHEN** 未登录用户打开登录页
- **THEN** 敏感输入为空且无预置凭据进入客户端产物

#### Scenario: 会话撤销或过期
- **WHEN** 访问令牌失效或用户停用
- **THEN** API 拒绝，UI 清理认证并保留安全恢复入口，不重复自动建身份

#### Scenario: 基础短期 Bearer 会话
- **WHEN** 本地用户密码登录成功
- **THEN** 保存固定租户/用户/期限/许可代次的 AuthSession 并签发独立 jti，客户端只使用 sessionStorage；无刷新或静默延长，有效 Bearer 的重复使用不冒称已防止

#### Scenario: 安全变更后的旧会话
- **WHEN** 重置密码、修改角色或用户状态后再次提交旧 JWT
- **THEN** 原子递增代次与永久撤销事实使旧 JWT 拒绝，重新启用用户也不能复活；本人会话列表及撤销范围隐藏他人会话

#### Scenario: 已连接流与迟到响应
- **WHEN** SSE 期间会话失效，或新登录后收到旧请求的 401
- **THEN** SSE 发无业务游标的 auth.expired 并终止，客户端仅清对应会话；不重放业务写请求或修改 Run，旧 401 不清新登录，网络失败提供认证重试

### Requirement: 会话刷新模式条件实施
采用 refresh token 或服务端会话前 SHALL 明确 HTTPOnly/SameSite/CSRF、轮换/重放检测、注销和租户绑定；不采用时 SHALL 明确短期令牌与重登体验。

#### Scenario: 重放旧刷新凭证
- **WHEN** 采用刷新模式后旧轮换凭证再次使用
- **THEN** 拒绝并使相应会话按策略失效，不签发可扩权令牌

### Requirement: 企业身份与委托范围
SSO/委托 OAuth SHALL 在 IdP 和映射契约批准后启用，校验 issuer/audience/state/nonce/回调及租户映射，外部声明不得自动扩权或静默建号。

#### Scenario: 外部声明指定管理员
- **WHEN** IdP 用户声明未经批准的租户或管理员角色
- **THEN** 拒绝映射或按显式预配范围收窄，保留本地管理员应急能力

#### Scenario: 下游委托撤销
- **WHEN** 外部用户撤销 SaaS 授权
- **THEN** 后续连接器调用拒绝并记录撤销，不继续使用缓存宽权限凭据
