## ADDED Requirements

### Requirement: 可复现单仓库
项目 SHALL 在同一仓库管理 API、Agent Worker、Web Chat 和共享核心包，并使用锁文件固定 Python 与 Node.js 依赖。

#### Scenario: 全新开发环境安装
- **WHEN** 开发者按 README 使用受支持的 Python、uv、Node.js、pnpm 和 Docker 安装项目
- **THEN** 后端测试、前端类型检查和前端构建能够使用仓库声明的命令运行

### Requirement: 本地基础设施
项目 SHALL 提供 PostgreSQL/pgvector 和 Redis 的 Docker Compose 配置、健康检查及非敏感默认配置示例。

#### Scenario: 启动本地依赖
- **WHEN** 开发者启动 Compose 基础设施
- **THEN** PostgreSQL 和 Redis 在健康检查通过后可供 API 与 Worker 使用

### Requirement: 数据库迁移
数据库结构 SHALL 通过版本化迁移管理，不得依赖应用启动时自动建表作为生产方案。

#### Scenario: 初始化空数据库
- **WHEN** 开发者对空 PostgreSQL 数据库执行升级命令
- **THEN** 所有基础表、索引和约束按迁移版本创建
