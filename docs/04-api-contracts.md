# API 契约约定

## 分层

| 前缀 | 用途 | 典型权限 |
| --- | --- | --- |
| `/v1/auth` | 登录和当前身份 | 公开登录 / 已登录 |
| `/v1/admin/*` | 控制面、知识和 Dashboard | `platform_admin` |
| `/v1/agents` | 员工可用 Agent 目录 | 显式授权 |
| `/v1/conversations` | 会话、消息和历史 | 会话主体授权 |
| `/v1/runs` | Run 状态、取消和事件流 | 会话主体授权 |

## 通用规则

- 使用 JWT 建立主体、租户和角色上下文。
- 跨租户或无权限资源统一返回不可枚举的 404 语义。
- 消息写入必须携带 `Idempotency-Key`。
- 分页接口使用稳定排序，返回明确的 cursor 或 page 信息。
- 错误响应包含稳定错误码和 correlation ID，不返回密钥、Prompt、内部域名或完整敏感正文。
- SSE 支持 `Last-Event-ID` 和 `cursor`，终态后关闭流。
