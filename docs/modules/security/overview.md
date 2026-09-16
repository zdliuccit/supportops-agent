# 安全边界

- JWT 的 `sub` 是系统用户 UUID，用户停用后已有令牌失效。
- 所有资源查询强制租户过滤，管理接口不接受客户端传入租户或扩权角色。
- 模型密钥只写入 Secret Provider，API 和日志只返回 masked hint 与 revision。
- Agent 运行工具来自服务端注册目录，按版本绑定、租户和用户角色取交集。
- 日志、错误、Observation 和 provider 返回值执行字段白名单及脱敏。
- 高风险写操作和人工审批目前仍未实现，不能仅依赖 Prompt 承担权限控制。
