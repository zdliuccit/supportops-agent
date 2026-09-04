## 1. 单仓库与开发环境

- [x] 1.1 创建 uv Python workspace、共享核心包、API 应用和 Agent Worker 应用
- [x] 1.2 创建 pnpm workspace 和 React/TypeScript/Vite Web Chat 应用
- [x] 1.3 添加环境变量示例、统一忽略规则和本地开发命令文档
- [x] 1.4 添加 PostgreSQL/pgvector 与 Redis Docker Compose 配置及健康检查

## 2. 配置、数据与迁移

- [x] 2.1 实现类型化配置、异步数据库会话和 Redis 连接管理
- [x] 2.2 实现租户、用户、会话、消息、Agent Run 和 Run Event 数据模型与约束
- [x] 2.3 配置 Alembic 并创建首个 PostgreSQL 数据库迁移
- [x] 2.4 实现仅限非生产环境的本地身份引导和 JWT 生成命令

## 3. API 身份与横切能力

- [x] 3.1 实现 JWT 验证、Principal 模型和租户/用户资源过滤
- [x] 3.2 实现关联 ID 中间件和安全结构化日志
- [x] 3.3 实现存活、就绪检查以及数据库与 Redis 依赖状态
- [x] 3.4 配置版本化 API、统一错误响应和允许来源配置

## 4. 会话与 Run API

- [x] 4.1 实现创建及读取会话 API，并返回按时间排序的消息
- [x] 4.2 实现带 Idempotency-Key 的消息提交事务和冲突检测
- [x] 4.3 实现 Run 查询、状态转换服务和幂等取消 API
- [x] 4.4 在事务提交后安全发布 Run 到 Redis 队列

## 5. Worker 与事件流

- [x] 5.1 实现 Redis 队列生产/消费协议和重复投递保护
- [x] 5.2 实现确定性占位执行器及 queued/running/terminal 状态流转
- [x] 5.3 实现按 Run 单调递增且追加写的结构化事件存储
- [x] 5.4 实现支持 Last-Event-ID、心跳和终态结束的 SSE API
- [x] 5.5 实现 queued Run 恢复扫描或重新入队机制

## 6. Web Chat

- [x] 6.1 实现运行时配置、开发令牌输入和 API 客户端
- [x] 6.2 实现创建会话、消息列表、消息发送和幂等键
- [x] 6.3 实现带 Authorization 的 fetch SSE 解析、断点恢复和文本增量展示
- [x] 6.4 实现加载、空状态、运行中、取消、失败和重连界面及基础可访问性

## 7. 验证与交付

- [x] 7.1 添加身份、租户隔离、消息幂等和非法状态转换单元测试
- [x] 7.2 添加会话、消息、Run、取消和 SSE API 集成测试
- [x] 7.3 添加 Worker 重复消费、事件顺序和 queued Run 恢复测试
- [x] 7.4 运行 Alembic PostgreSQL 验证、后端格式/类型/测试和前端类型/构建检查
- [x] 7.5 更新 README 启动指南、架构说明、已知限制和后续 change 边界
