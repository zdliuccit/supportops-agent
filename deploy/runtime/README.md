# 应用构建与运行模板

本目录交付 W22.2/W22.3 的 API、Worker、Web 镜像、显式迁移和同源 SSE/TLS 入口模板，**没有运行镜像构建、容器、迁移或生产验收**。现有 `deploy/docker-compose.yml` 继续用于本地 PostgreSQL/Redis；此模板连接维护者批准的外部服务，不能当作 HA、备份恢复或已批准生产方案。生产环境、负载/SLO/RTO/RPO、企业秘密 provider 和隔离 smoke 仍待各自任务。

## 构建与配置边界

- 后端使用 Python 3.12，`uv sync --frozen --all-packages --no-dev --no-editable` 解析仓库 `uv.lock`，安装现有 API/Worker/core。正式运行直接使用虚拟环境入口，不在容器启动时联网安装。
- Web 使用 Node 22+、`packageManager` 固定的 pnpm@11.10.0、`pnpm install --frozen-lockfile` 与现有 `web:build`。构建仍包含项目既有 TypeScript 校验；这只是模板中的命令，本轮未运行。仓库当前 pnpm 启动器查询版本时报告锁文件读取权限问题并返回 12.4.2，不能当作已验证的 11.10.0；模板显式安装固定版本，实际构建待执行。
- 基础镜像必须由维护者填入已批准的不可变 digest；不提供假 digest 或自动选择 latest。Python 镜像需具备 Debian 的 groupadd/useradd，Node 镜像需提供 Corepack；nginx 镜像需具备 UID/GID 101、`/etc/nginx` 与 `nginx` 入口。运行镜像固定唯一 release tag，并记录实际 digest；平台/架构和基础镜像扫描由发布环境确认。
- 锁文件固定应用依赖；基础镜像、构建后端/下载源和架构也影响产物，未宣称完成逐字节可重复或供应链验收。修改依赖必须更新原锁文件，不在镜像内解锁升级。
- `.dockerignore` 排除本地 `.env`、私钥、工具缓存和产物；源码采用明确 COPY。Web 唯一 API 参数为空字符串，浏览器请求当前 HTTPS 来源的 `/v1`，不把服务端秘密写入 Vite 参数或静态文件。
- `config.env.example` 是 Compose 非秘密参数示例，`server.env.example` 是服务端配置结构。均复制到**仓库外**、替换占位符后使用；实际服务端文件只传入 API/Worker/迁移容器。不要 `docker compose config` 或打印 inspect/environment 到共享日志，以免展开秘密。
- 服务默认非 root、只读根文件系统、临时目录 tmpfs、丢弃 Linux capability。Web 只绑定宿主机 127.0.0.1:8080，API 无宿主机公开端口；Worker 无 HTTP 端口。本地密封 provider 不等于企业 Vault/KMS，staging 默认不得静默切到 production。

## 获授权环境的操作顺序

以下命令仅说明模板用法，**本轮未执行**。示例以维护者提供的配置文件为输入，不把秘密放到命令参数中。

```bash
# 先替换两个示例的占位符并保存到仓库外；命令从项目根目录执行。
docker compose --env-file /controlled/supportops/config.env -f deploy/runtime/compose.yml build

# 停止新请求，等待或隔离旧执行者，再由唯一维护者显式升级。
docker compose --env-file /controlled/supportops/config.env -f deploy/runtime/compose.yml stop web api worker
docker compose --env-file /controlled/supportops/config.env -f deploy/runtime/compose.yml --profile maintenance run --rm migrate

# 业务迁移/checkpoint 初始化成功后只读预检；不替代隔离 smoke 或生产验收。
docker compose --env-file /controlled/supportops/config.env -f deploy/runtime/compose.yml --profile maintenance run --rm preflight

# 只有迁移成功且批准开放后才启动；API/Web 必须匹配同一发布契约。
docker compose --env-file /controlled/supportops/config.env -f deploy/runtime/compose.yml up -d api worker web
```

1. 先核对业务库已达到 **20261010_0017** 或其已知后续版本并准备批准的一致性备份。本迁移入口拒绝空库、未知基线或更早版本：历史 `20260904_0005` 含清空业务及 checkpoint 的操作，不能自动跨越。首次空环境建库/旧系统迁移必须单独审核，不用删数据/伪造 Alembic revision 绕过保护。
2. 旧 API/Worker 必须停止新输入/领取，并按 [Runbook](../../docs/modules/observability/operations-runbook.md) 收敛在途请求。默认 Worker 30 秒宽限 + 15 秒取消，Compose 留 60 秒；增大服务端时限时必须同步增大 stop_grace_period。shutdown_incomplete 不能视为已停止或允许副作用重放。
3. 迁移入口用同一 PostgreSQL 会话持有 `pg_try_advisory_lock`，Alembic 复用这条连接，完整升级到代码 head 后建立配置中的 checkpoint Schema 并调用官方 saver.setup。第二执行者立即失败，进程/连接终止后锁释放；原始异常不输出连接串。Alembic 配置保存 URL 时转义 ConfigParser 的 `%`，避免 URL 编码密码被错误插值；不改变真实密码。**所有维护者都必须使用这个入口**，旧直接 Alembic 命令不自动受此锁约束；在应用运行时不能并发直接升级。
4. 业务迁移 0018–0046 按依赖链顺序，0040 后旧 JWT 重新登录，0041 旧版本发布清单保持空、不回填当前事实。0042追加评测表、0043撤回字段不回填历史。0044新增灰度/追加事件与会话选版字段，不回填历史。0045新增保留草稿/本人申请/未来删除事实结构，不清理或回填实际删除事实。0046增加独立补偿提案表，无实际补偿执行。checkpoint表不计入60张业务表，与独立 schema 一起备份。业务迁移已提交但 checkpoint 初始化失败时本次命令失败，修复后再次运行收敛，不执行自动 downgrade。当前运行时仍在打开 saver 时执行 setup，应明确其数据库 DDL 权限；严格 DDL 分权需要另行修改，不冒称已完成。
5. API/Worker/Web 使用同一发布版本；队列名、调度策略、固定凭据 provider 和 checkpoint Schema 必须一致。模型允许列表/外部 URL 是维护者批准输入，模板不赋予任何业务资源范围。已有库关闭 bootstrap；首次管理员配置独立审核。
6. 显式 `preflight` 检查配置和预期环境一致、关闭 bootstrap/Mock、显式秘密无占位符、provider 可构造；PostgreSQL 只读事务核对代码/Alembic head、全部业务表/列和 checkpoint 表，Redis ping。启用端点的非删除固定历史版本同样按允许列表与 DNS/私网策略复检；只读地址元数据、最多 1000 个去重地址、单个 DNS 子进程 3 秒且可终止、整体 60 秒。报告只含固定错误码/计数/版本，不输出原异常或目标 URL，不解封每条凭据、不请求模型。
7. 预检不验证字段类型/约束/内容、企业秘密 provider、每条凭据可解封、模型能力、外层 TLS 或业务闭环。脚本尚未执行，隔离 smoke 尚未实施；W22.4 保持部分完成，API ready 仍不保证 schema head，不得跳过显式迁移/预检步骤。

## 健康、TLS 与 SSE

| 信号 | 当前边界 |
| --- | --- |
| API `/health/live` | 进程存活，不查模型/数据库/Redis；外层编排 liveness 使用该信号 |
| API `/health/ready` | PostgreSQL 与 Redis，供模板 healthcheck/入口启用；不等于 Schema、模型、知识索引或业务连接器验收 |
| Worker 存活 | 容器进程运行只是进程事实；消费/维护健康读取当前独立 RuntimeServiceLease、过期和 degraded 原因，没有虚构 Worker HTTP ready |
| Web 可访问 | 静态入口与反向代理，不等于 Agent 执行成功 |

`web` 的 nginx 仅监听内部 8080，由批准的 TLS 入口终止 HTTPS。`tls-site.conf.example` 给出固定域名跳转、证书路径、TLS 1.2/1.3 和同源代理结构；替换域名/证书并检查入口网络隔离后才启用，不自动签发证书或修改宿主机。若外层入口位于其他主机，需要受控私有网络或认证隧道连接，不能直接公开模板 HTTP 端口。

两层 `/v1` 代理都关闭 buffering、缓存及 gzip，保留 Authorization 和 correlation ID，HTTP/1.1、不强制关闭上游连接。默认 heartbeat 10 秒、上游空闲/发送超时 75 秒；外层负载均衡/CDN 也必须禁用 SSE 缓冲并满足心跳超时。连接断开后客户端沿原 Run cursor 重连；auth.expired 清理会话且不写业务终态。代理 access log 只含方法/路径/状态/耗时，不记录 query、原令牌、IP 或正文。

应用回滚须保留扩展 Schema、动作/审批/撤销/回执事实；不能启动绕过 AuthSession/工具撤销/attempt fencing 的旧 API/Worker。0040 存在会话/代次历史时拒绝降级。远程部署、证书安装、迁移执行及真实数据操作均需目标与授权，本次仅交付可审阅源文件。
