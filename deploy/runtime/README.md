# 应用构建与运行模板

本目录交付 W22.2/W22.3 的 API、Worker、Web 镜像、显式迁移和同源 SSE/TLS 入口模板，**没有运行镜像构建、容器、迁移或生产验收**。现有 `deploy/docker-compose.yml` 继续用于本地 PostgreSQL/Redis；此模板连接维护者批准的外部服务，不能当作 HA、备份恢复或已批准生产方案。生产环境、负载/SLO/RTO/RPO、企业秘密 provider 和隔离 smoke 仍待各自任务。

## 显式逻辑备份（W22.6）

`backup.compose.yml`与应用编排独立，使用批准的Debian PostgreSQL客户端镜像，默认只显示帮助。`backup.sh --create`才导出专用SupportOps数据库；本轮没有运行脚本、Compose、数据库备份或恢复。采用[pg_dump自定义归档](https://www.postgresql.org/docs/17/app-pgdump.html)，一次导出同库全部非系统schema，避免把业务库和checkpoint分成两个不同时间的备份。不支持与其他应用共享的数据库，不自动连本地默认数据库。

维护者在仓库外准备以下配置后才能使用：

- `backup.env.example`：镜像digest、非root UID/GID、目标别名、受控目录和期限/大小预算。原`server.env`和API/Worker镜像不参与，不向备份容器注入JWT或密封主密钥。
- `backup-service.conf.example`及独立pgpass：服务固定名`supportops_backup`，显式host/database/备份只读身份；入口的固定连接参数强制TLS verify-full/批准CA、5秒连接和锁等待，不从命令行传密码。两文件与恢复引用清单必须属于运行UID，0400/0600且不是符号链接，根证书独立只读挂载。备份身份须由维护者在数据库预先授予读取完整专用库的权限，脚本不自动GRANT、提权或开启RLS绕过。
- `backup-recovery-references.example.json`：独立受控保存应用镜像/source版本、数据库角色/扩展基线、密封密钥revision/provider引用、外部文件备份关联、保留和恢复政策引用。只填引用，不填秘密值；文件≤128KiB，开始和归档后都核对批准SHA-256。脚本不解析内容语义，不复制它或主密钥进归档，不把摘要匹配当作provider可恢复/法律保留批准；清单必须与备份独立妥善保留。
- 输出目录必须已存在、属于该UID且权限0700；配置`STORAGE_APPROVED=true`和`DATABASE_SCOPE=dedicated_supportops_database`是维护者对目标与受控加密存储的声明，**脚本不提供或验证磁盘加密**。自定义格式压缩也不是加密。占位符和默认未批准值不能直接作为实际配置，存储配额/位置、保留周期及法律保留仍须批准。

以下命令仅说明用法，本轮未执行；真实数据导出仍须明确目标与授权：

```bash
docker compose --env-file /absolute/controlled/backup.env \
  -f deploy/runtime/backup.compose.yml run --rm backup --help
docker compose --env-file /absolute/controlled/backup.env \
  -f deploy/runtime/backup.compose.yml run --rm backup --create
```

每次在输出卷新建随机0700目录，不覆盖已有备份，单次pg_dump默认900秒、最大一天，无自动重试。默认归档预算1GiB、最大64GiB，shell文件上限按1024字节保守折算，512字节块的shell可能更早拒绝，完成后再检查实际字节；这是单文件上限，不代替卷配额。归档目录读取另外限30秒/2MiB；哈希、flush和驱动/容器清理没有硬实时截止。stderr经FIFO只计字节、不落原文；非零退出、任意警告或中断均失败。`pg_restore --list`只读取本次归档目录、不连接恢复目标，确认`public.alembic_version`及配置schema的四张checkpoint表数据条目，源/客户端主版本须一致；不验证行内容、全部业务表或实际可恢复性。

成功产物为`database.dump`、`manifest.json`和`COMPLETED`，manifest记录目标别名/时间/实际大小/SHA-256/PG主版本/checkpoint/恢复引用摘要，固定`restore_verified=false`。归档和manifest先flush，再写并flush完成标记。只有三者齐全且**没有INCOMPLETE或FAILED**才可视为本次归档已创建；SHA-256只能核对字节，不是签名或恢复验收。失败保留原随机目录、部分归档和INCOMPLETE/FAILED（包括可能已写出的完成标记），不得当可用恢复点；强制终止或输出管道故障可能缺报告，须按上述组合核对。报告只输出版本/固定码/备份ID/大小/摘要，不输出连接串、目标主机、用户名、对象目录、凭据或原诊断。

覆盖范围以manifest为准：知识版本正文、分块/ACL、动作/停止事实等数据库记录随同库导出；外部原始文件/批准文件不自动复制，须由独立文件备份按引用清单关联。当前长期记忆未启用，不虚构记忆文件。Redis通知不备份，恢复后按Run/通知Outbox事实重建；不能把Redis缓存当业务事实。角色/表空间/扩展二进制、秘密provider/key本体也不在单库归档中，恢复前按独立版本化配置准备。此入口没有WAL/PITR、定时调度、自动保留删除或恢复操作；RTO/RPO、企业provider/外部文件策略及W22.7真实恢复验收仍待，W22.6保持部分实现。

### 备份产物的离线核对

`backup_inspect.py`提供W22.6的只读交付功能，本轮没有运行。只用Python3.12标准库，不导入Settings、不读.env/数据库连接文件，不调用pg_restore或执行归档SQL。单独的`backup-inspect.compose.yml`禁用网络，根文件系统及所选单个产物目录均只读；使用产物所属的非root维护UID/GID，不复用应用环境。另填`SUPPORTOPS_BACKUP_INSPECTOR_IMAGE`的批准Python镜像digest及`SUPPORTOPS_BACKUP_ARCHIVE_DIR`，命令默认仍只帮助。

维护者必须从原备份记录及独立配置提供预期备份ID/目标别名/checkpoint schema/恢复引用摘要，不能直接把待核验manifest中的值复制为“可信预期”。示例仅说明用法，未执行：

```bash
docker compose --env-file /absolute/controlled/backup.env \
  -f deploy/runtime/backup-inspect.compose.yml run --rm inspect \
  --directory /backup \
  --expected-backup-id 'supportops-backup.<12-character-id>' \
  --expected-target-alias '<approved-database-alias>' \
  --expected-checkpoint-schema supportops_checkpoints \
  --expected-references-sha256 '<approved-reference-manifest-sha256>'
```

入口逐级拒绝路径符号链接，目录须本人0700；三份产物须本人0400/0600普通文件且无硬链接。目录只允许database.dump/manifest.json/COMPLETED三项，INCOMPLETE/FAILED/其他内容一律不能匹配。manifest限16KiB、完成标记65字节、归档默认最多64GiB（可用--max-bytes降低），精确字段/版本/布尔及整数类型、预期范围、时间先后、实际长度和完成标记摘要均核对，重复JSON字段/非有限常量拒绝。归档每次读取最多1MiB并计算SHA-256；前后复查句柄/路径stat及目录条目，发现变化不输出半份通过报告。默认300秒、可用--timeout-seconds设1–3600秒，逐段检查截止时间；内核IO、关闭句柄不承诺硬截止，核验也不锁定宿主机文件或保证之后不再变化。

成功报告supportops_backup_inspection_v1只说明artifact_matched（字节与维护者预期一致），报告归档及manifest摘要，不输出路径、目标原文或坏清单/异常。明确restore_verified/provenance_verified/archive_readability_checked/reference_contents_validated/storage_encryption_verified/external_files_checked均为false，不是签名校验、归档解压、行完整性、外部文件或密钥检查，更不能替代W22.7恢复演练。参数错误退出2，其他未确认退出1，中断130；帮助/无效参数不读产物，任何路径不改写完成/失败标记或自动修复、恢复。W22.6仍需实际备份策略批准，验证继续暂缓。

### 恢复引用配置的缺项检查

同一`backup_inspect.py --mode references`只读检查恢复引用清单的结构和缺项，可在创建备份前使用。独立`backup-references.compose.yml`只挂载引用目录，禁网络且只读；不需要产物目录、数据库连接或应用环境。`SUPPORTOPS_BACKUP_RECOVERY_REFERENCES_DIR`须属于运行UID、权限0700，里面只能有本人0400/0600的`recovery-references.json`普通单链接文件；`SUPPORTOPS_BACKUP_RECOVERY_REFERENCES_FILE`须指向这一份文件。维护者独立确认其SHA-256，修改清单后须重新确认摘要，检查不会补写配置或生成批准。

清单使用示例中的精确字段；null保留为待配置，不默认通过。引用字段只接受`ref:<登记号>`，登记号首字符为英文字母或数字，后续最多191个英文字母、数字或`._/-`，由维护者在独立受控登记中解析；这里不填路径、URL或秘密值。后端/Web镜像字段填`sha256:`加64位小写十六进制摘要，源码revision为40或64位小写十六进制；本工具只核对格式，不访问镜像仓库或Git。PG主版本为10–99的整数，RPO/RTO为1–2147483647的整数秒（该范围只是字段容量，不是批准的恢复目标），布尔值不当整数接受。

- `external_files.coverage`为`not_configured`时报告缺项；只有维护者确认没有外部文件才可填`none`并保持两个关联引用为null。`independent_backup`必须同时有外部文件备份和知识导入批准引用。
- `long_term_memory.enabled=false`时backup_reference须为null；true时须有独立备份引用。这只是清单声明，不开启产品记忆或新增备份能力。
- `retention_and_storage.legal_hold_status`必须显式为`not_configured`、`none`或`active`；未配置报告缺项，active须有legal_hold_reference，其余状态该引用须为null。`none`是维护者声明，不是系统已验证不存在法律保留。

以下仅为用法，本轮没有执行：

```bash
docker compose --env-file /absolute/controlled/backup.env \
  -f deploy/runtime/backup-references.compose.yml run --rm references \
  --mode references --directory /references \
  --expected-references-sha256 '<independently-confirmed-reference-manifest-sha256>'
```

沿用受控目录/文件打开及前后变化检查，清单最多128KiB，拒绝重复键、未知字段、非有限常量、错误类型及范围矛盾；默认300秒协作截止，不承诺内核IO硬截止。成功读取后仅输出`supportops_backup_reference_inspection_v1`、摘要、固定字段名缺项列表和`configuration_complete`或`configuration_incomplete`，不回显引用值或原文。完整配置退出0，缺项退出1；无效配置/读取失败沿用`supportops_backup_inspection_v1`固定未确认错误报告并退出1，参数错误2、中断130。引用模式拒绝产物专用参数，原产物模式默认行为保持不变。

`configuration_complete`只说明字段齐备且格式一致，approval_verified/reference_targets_checked/secret_recoverability_verified/external_files_checked/storage_encryption_verified/restore_verified/backup_authorized始终为false；不证明批准、密钥可恢复、存储加密或恢复目标可达。备份脚本仍只绑定原始清单摘要，产物manifest的reference_contents_validated仍为false，独立配置报告不升级历史产物或授予实际备份/恢复许可。实际政策输入和W22.7仍待，当前示例保持未配置。

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
4. 业务迁移 0018–0053 按依赖链顺序，0040 后旧 JWT 重新登录，0041 旧版本发布清单保持空、不回填当前事实。0042追加评测表、0043撤回字段不回填历史。0044新增灰度/追加事件与会话选版字段，不回填历史。0045新增保留草稿/本人申请/未来删除事实结构，不清理或回填实际删除事实。0046增加独立补偿提案表，无实际补偿执行。0047增加导入任务/对象/物化/页回执四表，不回填来源或启用读取；有历史拒绝降级。0048新增本人创建停止事实，与Job在READ COMMITTED下以主体锁双向防止迟到创建，停止事实不可改删、有历史拒绝降级；该迁移与API/Web须同步发布，未执行或验收。0053新增HTTP MCP一次派发表，不启用真实连接。checkpoint表不计入69张业务表，与独立 schema 一起备份。业务迁移已提交但 checkpoint 初始化失败时本次命令失败，修复后再次运行收敛，不执行自动 downgrade。当前运行时仍在打开 saver 时执行 setup，应明确其数据库 DDL 权限；严格 DDL 分权需要另行修改，不冒称已完成。
5. API/Worker/Web 使用同一发布版本；队列名、调度策略、固定凭据 provider 和 checkpoint Schema 必须一致。模型允许列表/外部 URL 是维护者批准输入，模板不赋予任何业务资源范围。已有库关闭 bootstrap；首次管理员配置独立审核。
6. 显式 `preflight` 检查配置和预期环境一致、关闭 bootstrap/Mock、显式秘密无占位符、provider 可构造；PostgreSQL READ COMMITTED只读事务先取得同迁移key的共享事务锁，忙时立即拒绝，结束自动释放。核对代码/Alembic head、全部ORM业务表/字段类型/空值与PK/UNIQUE/FK结构和执行状态；checkpoint仍只核对表存在，Redis ping。SQL语句最多5秒/锁等待1秒，整体60秒。启用端点非删除固定历史版本同样按允许列表与DNS/私网策略复检；只读地址元数据、最多1000个去重地址，固定DNS子进程使用-I/-B、空环境、关闭额外FD、cwd=/、单次3秒且可终止。报告只含固定码/计数/版本和明确未核对项，不输出原异常或目标URL，不解封每条凭据、不请求模型。
7. 预检不验证CHECK表达式、部分/普通索引整体、其他业务触发器体、服务端默认值/生成策略、其他迁移独有保护、额外结构或数据库角色权限，也不验证业务内容、企业秘密provider、凭据可解封、模型能力、外层TLS或业务闭环。脚本尚未执行，隔离smoke尚未实施；W22.4保持部分完成，API ready仍不保证schema head，不得跳过显式迁移/预检步骤。

两个维护脚本都只以无参数调用执行实际操作，`-h`或`--help`仅显示固定帮助；未知、重复或混合参数在加载配置/连接依赖前拒绝，退出码2，且不回显参数值。`preflight.py --dns-policy`是父预检的固定内部子进程协议，不接受额外参数，不是独立上线核准。人工中断返回130及固定`PREFLIGHT_INTERRUPTED`或`MIGRATION_INTERRUPTED`；迁移中断不证明尚未提交，维护者须独立核对业务head和checkpoint状态，不自动downgrade或重放。工作等待超时/SQL超时不承诺进程与驱动清理的硬截止。实际帮助/错误/中断路径亦未运行验收。

0049追加历史凭据revision及知识证据/分块的行UPDATE/DELETE与语句TRUNCATE保护，ALWAYS启用，三表任意有历史拒绝downgrade；不新增表或改写旧内容。新代码仍允许正常轮换追加revision、首次完整撤销和单向软删除。保留保护及原数据后再评估应用回滚，不为恢复/清理关闭触发器；独立维护身份和实际擦除/恢复tombstone仍待。preflight现另行核对0049的具名定义，但head或目录匹配均不证明实际保护故障已验收；本轮未执行迁移或任何数据操作。

## 只读结构核对的范围

`supportops_core.schema_structure`由预检显式传入连接、当前Base.metadata和既有Alembic类型比较器；模块不自行加载Settings、连接数据库、读业务行或运行DDL。仅核对ORM要求的public结构子集，未声明的额外对象不会被删除或伪称已核对。最多256个声明表、10000个声明字段和20000条相关PK/UNIQUE/FK/CHECK系统目录记录，反射按明确表名批量读取，查询参数绑定；超限或反射异常固定拒绝。

- 实际连接必须只读，显式有效search_path只能是public，或pg_catalog、public；包含隐式schema的实际搜索顺序还必须恰为pg_catalog、public，已有pg_temp也拒绝。不通过自动改路径让预检通过。迁移入口共用该路径约束，并限定内置锁函数为pg_catalog。允许默认的不存在用户schema占位，已有用户schema或其他有效路径会拒绝。
- 字段使用Alembic PostgreSQL类型比较器处理FLOAT/DOUBLE PRECISION等别名，并另行严格核对明确的字符串长度、原生枚举标签和nullable；不靠表/列同名放行类型漂移。
- 主键和普通唯一约束按列顺序核对，唯一约束还核对NULL唯一性。外键核对本地列/目标schema、表、列及删除/更新/MATCH规则；约束名称仅用于对应系统目录状态，不作为语义证明。
- PK/UNIQUE/FK必须已验证且时机匹配；PostgreSQL18及以后另查conenforced，旧版本不引用不存在的列。主键/唯一约束的支撑索引必须unique、valid、ready、live且立即性匹配；当前会话session_replication_role必须origin，外键内部执行触发器须存在并在该模式启用。这里只核对这些内部触发器的启用状态，不验证所有触发器体。
- ORM表级及列内声明的CHECK必须具名，并在同public表下找到同名CHECK；缺失、类型不符或声明无名固定拒绝。复用validated、PG18+enforced与时机核对，旧版不查询新列。仅核对存在与状态，不读取/执行或比较表达式，不用同名证明逻辑相同；额外及迁移独有CHECK仍不在声明范围内。
- 显式声明的唯一Index另核对同public表/名称、直接键列顺序、NULL唯一性及是否为部分索引，要求unique/valid/ready/live/immediate；只支持当前直接列键、无声明include/ops及默认或btree访问方法的声明，未知表达式、自定义选项或重名声明拒绝。最多1000个声明名称/1000条目录记录，只取键列和状态，不读取谓词或索引表达式原文；不核对WHERE语义、实际访问方法、排序/操作符类、collation或include定义，不能据此认定完整唯一性策略正确。PG15前不引用indnullsnotdistinct，索引目录结构要求PG11+；当前部署仍遵循项目既有版本约定。
- 预检共享事务锁与模板迁移会话独占锁互斥，key数值保持原值并从同一常量导入。采用READ COMMITTED在成功取锁后读取新的迁移快照，避免依赖取锁前的旧快照；共享锁不阻止绕开模板的直接DDL，维护者仍须遵守停机和统一迁移入口。

预检与迁移共用`require_public_schema_context`，在原路径检查外均要求实际`session_replication_role=origin`；迁移在取得独占锁/执行DDL前即检查，失败固定`SCHEMA_ENFORCEMENT_CONTEXT_UNSAFE`，不自动SET角色或启用触发器。PostgreSQL中replica会影响触发器和外键检查，而local与origin内部行为相同；这里只允许origin是模板的固定配置约束，见[官方会话复制角色说明](https://www.postgresql.org/docs/17/runtime-config-client.html#GUC-SESSION-REPLICATION-ROLE)。预检仍额外要求只读事务，迁移不套用只读要求；没有执行连接、迁移或角色验收。

成功响应中的`checks.schema.structure`升级为`business_schema_structure_v3`，保留v2的具名CHECK计数/状态和既有计数，新增`named_unique_indexes`及`named_unique_index_states_verified=true`；`unique_index_definitions_verified=false`明确完整定义未核对。`foreign_key_trigger_enablement_checked=true`只指内部外键触发器；CHECK表达式、完整部分索引及其他触发器体、默认值、权限、checkpoint细节仍为false。旧报告不补造新检查结果，消费方须识别v3；局部`passed`不是完整迁移约束、数据正确性或上线批准。

| 固定码 | 维护处理 |
| --- | --- |
| `SCHEMA_MAINTENANCE_BUSY` | 当前模板迁移持独占锁，待该执行者结束后再独立预检，不绕过锁 |
| `SCHEMA_TRANSACTION_NOT_READ_ONLY` / `SCHEMA_SEARCH_PATH_UNSAFE` / `SCHEMA_ENFORCEMENT_CONTEXT_UNSAFE` | 核对实际连接与会话配置，不通过预检自动切换配置掩盖差异 |
| `SCHEMA_TABLE_MISSING` / `SCHEMA_COLUMN_MISSING` / `SCHEMA_TYPE_MISMATCH` / `SCHEMA_NULLABILITY_MISMATCH` | 核对匹配代码的显式迁移，不自动create_all/改字段 |
| `SCHEMA_PRIMARY_KEY_MISMATCH` / `SCHEMA_UNIQUE_CONSTRAINT_MISMATCH` / `SCHEMA_FOREIGN_KEY_MISMATCH` | 核对列组合、目标、规则、NULL语义及迁移记录 |
| `SCHEMA_CHECK_CONSTRAINT_MISMATCH` | 核对ORM具名CHECK与同表迁移定义；不存在、类型不符或声明无名均拒绝，不自动改名、补建或替换 |
| `SCHEMA_UNIQUE_INDEX_DECLARATION_UNSUPPORTED` | 当前核对器不支持该声明，或同public名称重复；独立审阅代码/迁移及核对能力，不静默跳过或简化声明 |
| `SCHEMA_UNIQUE_INDEX_MISMATCH` | 核对同表具名索引是否存在，以及键列/表达式键/NULL语义/部分标志；不自动重建 |
| `SCHEMA_CONSTRAINT_UNVALIDATED` / `SCHEMA_CONSTRAINT_NOT_ENFORCED` / `SCHEMA_CONSTRAINT_TIMING_MISMATCH` | 独立审阅约束当前状态，不能把NOT VALID/NOT ENFORCED当作完整约束 |
| `SCHEMA_KEY_INDEX_INVALID` / `SCHEMA_FOREIGN_KEY_TRIGGER_DISABLED` | 核对约束支撑/声明唯一索引的执行状态与内部触发器；预检不会自动重建/启用 |
| `SCHEMA_METADATA_LIMIT` / `SCHEMA_REFLECTION_UNAVAILABLE` / `SCHEMA_BACKEND_UNSUPPORTED` | 未完成结构核对，保留失败；不输出原SQL或秘密，不据此判定数据库为空 |

实现参考PostgreSQL官方的[约束目录](https://www.postgresql.org/docs/18/catalog-pg-constraint.html)、[索引状态](https://www.postgresql.org/docs/17/catalog-pg-index.html)、[实际schema搜索路径](https://www.postgresql.org/docs/17/functions-info.html#FUNCTIONS-INFO-SESSION)和[会话/事务advisory锁规则](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS)。本轮只交付代码与用法，没有连接数据库、执行预检、迁移、测试、构建或并发验收。

0050接续0049为知识版本、发布快照、索引代次增加ALWAYS行UPDATE/DELETE和TRUNCATE保护，不新增表/改写旧迁移或内容。版本原文与定位、快照固定列及代次快照/算法/摘要/编号不可改写；审核、首次完整发布、已发布版本退役缩短有效期、版本/快照单向软删除及索引构建状态保留，已有发布事实不可覆盖。降级先排他锁定新三表再判空，有历史拒绝；应用回滚保留保护，新内容/重建分别新增版本/代次。此迁移尚未执行，不新增擦除或恢复许可。

0051接续0050新增工具发现追加修订，当前共66张业务ORM表。启用新的工具发现治理API前须完成此迁移；来源策略默认disabled，不含地址或凭据，不自动连接MCP。PG ALWAYS触发器在READ COMMITTED及租户锁下核对前驱/状态并拒绝改写、删除、截断，有历史拒绝降级；应用回滚须保留已记录事实。当前仅交付迁移源文件，尚未执行迁移或验收，原预检没有新增对0051触发器体的核对承诺。

### 历史保护的专项目录核对

预检在同一只读/共享维护锁事务中另调用`supportops_core.reference_history_schema.verify_reference_history_guards`，核对0049三表`model_credential_revisions/knowledge_evidence/knowledge_chunks`及0050三表`knowledge_versions/knowledge_snapshots/knowledge_index_generations`上的`reference_history_guard`和`reference_history_truncate_guard`，以及三个public保护函数。行保护分别绑定对应迁移函数，TRUNCATE共用0049拒绝函数。它只检查系统目录，不调用函数、读取业务行、解封凭据、执行DDL或自动修复。本轮没有运行预检或连接数据库。

函数结果最多4条（正常3），每个prosrc最多16KiB，触发器结果最多13条（正常12）。具名触发器须精确符合BEFORE/行UPDATE+DELETE或语句TRUNCATE、ALWAYS、正确函数OID，无WHEN/列过滤/参数/延迟/克隆/transition table。函数须固定public/plpgsql/trigger返回/零参数/invoker及pg_catalog搜索路径等声明，函数体按0049/0050原始UTF-8文本SHA-256匹配，空白变化也拒绝。三个预期摘要已从迁移源码生成并固定于代码，运行时不读取迁移文件或用待核对数据库生成“预期”；今后保护定义变更须经新迁移及相应版本化核对契约更新。

核对结果位于`checks.schema.reference_history`，版本`reference_history_schema_v2`、范围`named_0049_0050_guards`，只返回6表/12触发器/3函数计数及定义匹配/ALWAYS标记；不输出函数体、数据库对象内容或原异常。`other_triggers_verified/roles_verified/history_contents_verified/catalog_snapshot_is_atomic/future_ddl_prevented`均为false，原`structure.triggers_verified=false`继续表示没有完整业务触发器验收。共享维护锁不阻止绕过统一入口的所有者DDL，多个READ COMMITTED查询也不构成不可变目录快照；检查后仍需保持原维护窗口及身份分权。

| 固定码 | 维护处理 |
| --- | --- |
| `SCHEMA_REFERENCE_HISTORY_GUARD_MISMATCH` | 指定保护缺失、停用、挂错函数、形状/配置/源码摘要漂移；停止开放入口，独立核对匹配的迁移与发布版本，不自动启用/重建 |
| `SCHEMA_REFERENCE_HISTORY_GUARD_UNAVAILABLE` | 目录不兼容、读取或解析不可确认；保持失败，按批准数据库版本与维护身份排查，不回显目录原文或SQL诊断 |

目录字段依据[pg_trigger](https://www.postgresql.org/docs/17/catalog-pg-trigger.html)、[pg_proc](https://www.postgresql.org/docs/17/catalog-pg-proc.html)，事件位依据[官方定义](https://github.com/postgres/postgres/blob/REL_17_STABLE/src/include/catalog/pg_trigger.h)。沿原语句5秒/锁1秒/整体60秒工作等待预算；没有新增依赖/表/迁移或执行恢复。运行权限、其他触发器与策略、历史数据真实性、实际撤销/篡改故障和隔离smoke仍待，W22.4保持部分实现。

## 健康、TLS 与 SSE

| 信号 | 当前边界 |
| --- | --- |
| API `/health/live` | 进程存活，不查模型/数据库/Redis；外层编排 liveness 使用该信号 |
| API `/health/ready` | PostgreSQL 与 Redis，供模板 healthcheck/入口启用；不等于 Schema、模型、知识索引或业务连接器验收 |
| Worker 存活 | 容器进程运行只是进程事实；消费/维护健康读取当前独立 RuntimeServiceLease、过期和 degraded 原因，没有虚构 Worker HTTP ready |
| Web 可访问 | 静态入口与反向代理，不等于 Agent 执行成功 |

人工本地Markdown导入默认`SUPPORTOPS_KNOWLEDGE_INGESTION_ENABLED=false`，当前只允许隔离local/dev/test；此处staging/production模板不能启用。实际来源获准后，维护者须在仓库外受保护文件中配置精确清单/当前本人范围/ACL映射/期限，并显式指定`SUPPORTOPS_KNOWLEDGE_INGESTION_APPROVAL_FILE`，不能经Web上传。提供者逐边界读取，不缓存批准；同OS用户的读取子进程不是完整OS沙箱，正式环境需先完成隔离与验收。没有默认来源、自动调度或自动发布，详见[知识配置与人工入口](../../docs/modules/knowledge/overview.md)。本轮未配置文件、启用来源、部署或迁移。

`web` 的 nginx 仅监听内部 8080，由批准的 TLS 入口终止 HTTPS。`tls-site.conf.example` 给出固定域名跳转、证书路径、TLS 1.2/1.3 和同源代理结构；替换域名/证书并检查入口网络隔离后才启用，不自动签发证书或修改宿主机。若外层入口位于其他主机，需要受控私有网络或认证隧道连接，不能直接公开模板 HTTP 端口。

两层 `/v1` 代理都关闭 buffering、缓存及 gzip，保留 Authorization 和 correlation ID，HTTP/1.1、不强制关闭上游连接。默认 heartbeat 10 秒、上游空闲/发送超时 75 秒；外层负载均衡/CDN 也必须禁用 SSE 缓冲并满足心跳超时。连接断开后客户端沿原 Run cursor 重连；auth.expired 清理会话且不写业务终态。代理 access log 只含方法/路径/状态/耗时，不记录 query、原令牌、IP 或正文。

应用回滚须保留扩展 Schema、动作/审批/撤销/回执事实；不能启动绕过 AuthSession/工具撤销/attempt fencing 的旧 API/Worker。0040 存在会话/代次历史时拒绝降级。远程部署、证书安装、迁移执行及真实数据操作均需目标与授权，本次仅交付可审阅源文件。

## W8.5 隔离MCP协议入口（实现已写入，未运行）

独立的`mcp-isolated.Dockerfile`与`mcp-isolated.compose.yml`只运行随包Mock，不加入应用Compose或生产工具目录。现有项目没有MCP SDK；新增`supportops-core[mcp-isolated]`可选依赖，锁定官方`mcp==1.30.0`，`uv add --package supportops-core --optional mcp-isolated 'mcp==1.30.0' --no-sync`已生成锁文件，未升级原有包、未在本机同步安装SDK。API/Worker默认安装不带此extra，不加载或启动MCP进程。

本实现显式使用2025-11-25的initialize/stdio/tools契约，拒绝协商为其他版本，不宣称支持2.x/2026协议。接口依据固定版本的[官方ClientSession](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/session.py)、[stdio客户端](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/client/stdio/__init__.py)、[低层Server](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/server/lowlevel/server.py)与[协议版本定义](https://github.com/modelcontextprotocol/python-sdk/blob/v1.30.0/src/mcp/types.py)核对；这是源码接口核对，尚非运行兼容性验收。

恢复这些可选模块的类型检查或兼容性验收时，需要在开发/隔离环境显式安装mcp-isolated extra，单纯默认生产安装不包含SDK或其类型信息。本轮未运行该安装或检查；当前依赖解析结果不能代替导入、构建及协议运行结果。

构建变量文件放在仓库外，仅提供已批准的`SUPPORTOPS_PYTHON_IMAGE`、`SUPPORTOPS_UV_IMAGE`不可变digest和`SUPPORTOPS_MCP_ISOLATED_IMAGE`。不传入应用server.env、生产凭据或数据卷。恢复隔离验收并准备好镜像引用后，可使用以下入口；本轮没有执行这些命令：

```bash
docker compose --env-file /path/to/approved-build.env -f deploy/runtime/mcp-isolated.compose.yml --profile mcp-isolated build mcp-isolated
docker compose --env-file /path/to/approved-build.env -f deploy/runtime/mcp-isolated.compose.yml --profile mcp-isolated run --rm --no-deps mcp-isolated catalog
docker compose --env-file /path/to/approved-build.env -f deploy/runtime/mcp-isolated.compose.yml --profile mcp-isolated run --rm --no-deps mcp-isolated read-status
```

容器使用独立10002 UID/GID、network none、只读根、cap_drop ALL/no-new-privileges、16进程/256MiB内存/0.5CPU、有限FD/CPU时间和8MiB tmpfs；没有宿主机/应用卷、端口或env_file。env-i只保留四个固定非秘密环境字段，SDK子进程接收同一允许列表。入口仅允许Linux/该UID与GID、精确环境、仅lo网卡和NoNewPrivs/空有效权限；普通宿主机/API/Worker直接调用会被拒绝。运行前置检查不证明完整挂载、cgroup、宿主机或网络隔离，实际容器隔离尚未验收。

`catalog`进行握手并读取固定单页目录，不执行工具；`read-status`同样先核对来源源码/服务身份/协议/完整描述及封闭输入输出Schema，再以空参数调用唯一`get_isolated_service_status`。返回固定isolated_mock状态、real_service_checked=false，结构化结果和文本镜像必须一致；没有任意工具名/参数/程序/URL/令牌入口，不提供roots/sampling/elicitation回调或业务执行许可。初始化之外的通知（包括listChanged）令本连接观察失效，失败不重试、不回退目录。

成功输出`isolated_mcp_report_v1`，包含来源指纹、描述与Schema摘要、可供人工审核的`tool_discovery_catalog_v1`和可选模拟观察，business_execution_enabled始终false；0只表示本次协议操作返回，不表示目标业务、生产连接或隔离验收通过。失败仅固定码，参数错误2、运行/协议失败3、输出失败4、中断130；输出失败不重做调用，中断不证明此前未调用。SDK单请求等待3秒，协议工作与清理等待预算10秒，SDK/内核清理不承诺硬截止。

来源指纹仅绑定固定实现/协议/SDK版本和包内源码，不是镜像批准或服务身份签名。此入口不连接治理库、不自动创建来源批准/提交目录、无ToolInvocation/Agent网关派发、认证IPC或当前调用许可；不能作为真实连接器使用。8.6仍要求管理员独立批准与修订围栏，8.10的connection_status保持not_connected；SDK通用stdio在解析前的输入帧限制并未完成，当前只启动随包固定Mock，不能替换为不可信第三方程序。W8.5运行兼容性与发现调用验收、W8.7 HTTP受众/scope/撤销、W20隔离与许可接线仍待，按当前要求未运行测试、类型检查、构建或容器。

## W8.10 隔离连接控制（实现已写入，未运行）

`mcp-probes.compose.yml`是显式合并到应用Compose的隔离local/dev/test覆盖文件，提供持久化任务的固定Unix socket探测。普通应用编排与staging/production不启用它；默认`SUPPORTOPS_TOOL_CONNECTION_MOCK_ENABLED=false`，开关设true时Settings拒绝非local/dev/test。API/Worker/Web须同步升级，启动Worker前先按已授权维护流程达到0052：即使开关关闭，Worker也会收敛旧任务，不能在旧Schema上运行新代码。0052新增任务/追加事件两表及ALWAYS保护，有历史拒绝降级；0052阶段为68张业务表，当前0053增至69张；均未执行迁移。原preflight不会核对0052触发器体，不得把结构匹配当作保护故障验收。

隔离环境另行准备非生产数据库/Redis、批准的后端/Web/隔离Mock镜像及基础镜像digest，不能复用真实业务数据或把staging配置改名后当隔离环境。沿用仓库外config.env，另外提供`SUPPORTOPS_MCP_ISOLATED_IMAGE`；服务端文件须明确local/dev/test并显式开启上述开关。以下仅说明启用模板的命令，本轮未执行构建、启动或探测：

```bash
docker compose --env-file /controlled/supportops/isolated-config.env \
  -f deploy/runtime/compose.yml -f deploy/runtime/mcp-probes.compose.yml \
  --profile mcp-probes up -d mcp-probe-runtime api worker web
```

启用覆盖文件时必须同时启用mcp-probes profile。三个应用服务的环境配置不进入隔离进程。共享专用命名卷`mcp-probe-ipc`只含`/run/supportops-mcp/control.sock`；初始化服务以root及CHOWN/FOWNER能力一次性设置目录10002:10001/0770，不加载应用配置、不读业务数据。常驻服务仍10002:10002，显式附加10001组，网络none、只读根/有限tmpfs、无应用数据卷/凭据/公开端口，API/Worker只读挂载该socket卷。双向SO_PEERCRED要求调用者10001、响应者10002，部署如使用user namespace须保持可核对身份，否则拒绝，不能回退到仅路径鉴权。常驻父进程取消累计CPU秒数退出限制，仍保留0.5 CPU、内存/PID/FD及权限限制；它每次只运行一个固定探测，不排队、不接受任意工具参数或业务动作。

管理员先通过W8.6人工审核当前源码指纹与唯一`get_isolated_service_status`完整描述/Schema，批准并提交匹配目录；没有当前批准或有效目录不能排队。源码指纹现包含IPC/CLI，旧指纹和批准不能沿用或自动升级。管理页来源详情单独展示隔离探测与熔断，GET的可探测标记只表示配置/批准允许排队，不证明执行器可达。写入确认绑定来源record_id与连接revision；只提交一次任务，必须刷新原任务区分queued/running/passed/failed/cancelled，未知不自动重试。

三次连续失败熔断，人工熔断可立即阻止固定定义解析并围栏在途结果；恢复需30秒冷却后显式半开探测，仅新结果通过解除。观察有效最多60秒且不超批准期限，过期stale/来源变更unknown；原connection_status=not_connected及business_execution_enabled=false不变。开关关闭只收敛原任务，不复活或重发；停止执行器后剩余任务按截止收敛，不因进程退出补写通过。移除覆盖文件前按授权停机流程处理依赖，保留任务/事件与审计，不删除卷或数据库事实来伪造恢复。

该通道是固定无参数Mock的只读诊断，不是业务执行broker、通用第三方stdio沙箱或HTTP授权实现。未连接真实MCP、没有业务凭据/ToolInvocation/Agent网关接线；SDK通用解析前帧限制与完整隔离、业务调用许可仍待W8.5/W20。连接控制按功能实现计入W8.10，运行兼容/发现调用/浏览器/隔离验收继续暂缓，不能据此宣称生产连接健康。

## W8.7 HTTP派发broker与认证IPC库（未部署）

0053新增`http_mcp_dispatches`，当前69张业务表；先按授权维护流程应用迁移才能接入新broker/保留预览，迁移尚未执行。每invocation唯一派发、取密前先提交claimed、一次nonce及非秘密token状态/lease不可替换；取消/超时/回传未知不重取或复活。PG ALWAYS保护历史，有记录拒绝降级；原preflight没有核对0053具名触发器体，不将通用结构匹配当作运行验收。该库没有公共HTTP接口或自动启动入口。

`serve_http_mcp_broker`须由未来获准独立运行入口注入真实上游provider及DB工厂；broker运行UID10003并附加10004组，HTTP执行器UID10004。维护者须在专用卷预建`/run/supportops-mcp-broker`为10003:10004/0770，broker创建`control.sock`后设置10003:10004/0660；双向SO_PEERCRED核对身份，无Linux支持、路径/身份不符或已存在socket则拒绝，不自动删除旧socket。下节已提供HTTP单次执行镜像/Compose/CLI源码，当前仍没有broker初始化服务或真实provider配置，不能直接复用原10002无网络Mock服务。

执行器使用`call_http_mcp_tool_via_ipc`，经固定socket发送nonce/operation/dispatch/摘要并取得短期SecretStr或最小元数据，DB及审计写入器只位于broker侧；不把应用env_file、数据库卷/网络或模型凭据交给执行器。每连接单命令、最多32KiB/4并行，无自动重连/重发；socket权限和UID验证只覆盖该通道，不证明OS秘密/网络隔离。完整部署仍须获批provider/服务身份、精确HTTP出口、CPU/内存/进程限制及独立环境，当前均未运行。缺少真实provider/签名或内省、获批目标或固定工具注册时调用仍拒绝，未执行测试、构建、迁移、容器或实际取密/网络验收。

### 独立HTTP单次执行入口与模板（未运行）

`mcp-http-executor.Dockerfile`和`mcp-http-executor.compose.yml`只供未来已批准的单次HTTP执行，不合并默认应用编排。镜像安装既有`mcp-isolated` extra，不新增依赖；运行入口固定`python -I -B -m supportops_core.mcp_http_executor_cli call`，UID/GID10004，env-i只保留PATH/HOME/LANG和固定模式。启动核对Linux/身份/环境、NoNewPrivs/零有效能力/seccomp过滤模式、无额外继承FD、管道stdin/stdout及有限core/CPU/句柄/进程/地址空间；失败退出2且不读取输入或策略。它没有验证全部挂载、过滤器内容或出口防火墙，不可视作完整OS隔离认证。

模板要求维护者在仓库外完成以下批准/准备，空值直接拒绝，**本轮没有生成实际策略或启动容器**：

| 引用 | 必需范围 |
| --- | --- |
| `SUPPORTOPS_MCP_HTTP_EXECUTOR_IMAGE`及构建镜像参数 | 已批准的固定镜像引用；Python3.12+与uv版本沿现有锁文件，不临时选最新版 |
| `SUPPORTOPS_MCP_HTTP_POLICY_FILE` | 已批准非秘密策略的绝对文件路径；普通单链接文件root:任意组/0444，只读挂到`/etc/supportops-mcp/http-policy.json`，容器目录root0755 |
| `SUPPORTOPS_MCP_BROKER_SOCKET_VOLUME` | 已存在、仅含broker控制socket的专用卷；目录10003:10004/0770和socket10003:10004/0660保持，不能挂应用数据卷 |
| `SUPPORTOPS_MCP_HTTP_EGRESS_NETWORK` | 已存在且由运维配置精确目标IP/端口和必要DNS规则的独立网络；禁止应用/模型/业务库/Redis入口及其他出口，不把网络名称当作规则证明 |

策略使用封闭`http_mcp_executor_policy_v1`，字段为`authorization_policy`（完整原`HttpMcpAuthorizationPolicy`）、`platform_issuer`、`platform_audience`、1–32项规范精确`allowed_ips`及可空`private_ips`子集。私网只接受逐个批准的RFC1918/ULA，回环/link-local/保留/过渡地址拒绝；完整SHA通过该DTO的`policy_digest`取得，摘要不是签名或执行授权。策略最多32KiB，固定路径O_NOFOLLOW读取，核对root权限/期限和前后文件属性；不允许按stdin/argv选择文件或扩展目标。单次进程冻结这一份策略，换文件只影响新进程，实时撤销仍须broker当前许可检查。

stdin只接受`http_mcp_executor_request_v1`的一行紧凑JSON：`nonce`、`deployment_policy_digest`、`request_digest`、`request`、`descriptor`、`arguments`；不得携访问令牌/平台JWT、命令、凭据路径或任意网络策略。原调用必须已经由可信网关保存且可由broker当前核对，原授权策略/部署SHA/调用SHA/描述SHA/参数SHA均匹配才进入现有IPC/HTTP适配器。最多192KiB、16层4096节点，3秒输入等待；首帧换行后立即处理、不等待EOF或接收第二次调用。不要在应用进程中取得Docker权限或将未经网关批准的用户JSON直接交给此入口。

模板为只读根、有限tmpfs、cap-drop/no-new-privileges、无应用env_file/业务卷/公开端口/TTY/自动重启，logging=none；CPU0.5、内存256MiB、PID16、FD64、地址空间512MiB、core0和CPU20/22秒。只有专用socket卷、非秘密策略文件和外部批准网络，没有真实凭据文件或broker数据库工厂。CLI工作等待20秒，输出最多128KiB/2秒非阻塞写入，另有25秒SIGALRM退出保护；同步计算、内核不可中断I/O与容器清理不能据此声称硬实时保证。

stdout一次返回`http_mcp_executor_report_v1`，仅关联nonce/dispatch/原摘要及脱敏不可信观察或固定失败码；`result_confirmed`只确认观察可返回，`business_status_confirmed`/`execution_permit`恒false，`invocation_attempted`不证明字节已出站。原始stderr不输出，令牌只在认证socket/内存，不进入命令行/策略/请求/结果/日志。退出0为观察返回，3为结构化未确认，4为输出或循环未知，130为尽力中断，124为25秒退出保护；任何未知/进程消失均按原dispatch只读核对，不重取/重发/新建许可或自造已撤销事实。已有claimed/bound不清除。

当前仅提供源码及默认关闭模板，没有实际provider/签名或内省、broker启动配置、精确OS网络规则或产品工具注册；69表/0053保持，8.7/20.5未完成。按用户要求未写/运行测试、类型检查、构建、Compose检查、容器、迁移、取密或实际HTTP请求。

### 应用到执行器的单次派发（未启用）

当前Compose默认`serve-once`，而`call`仍保留stdin运维入口。新增必填`SUPPORTOPS_MCP_EXECUTOR_DISPATCH_VOLUME`，为10001应用到10004执行器的独立外部socket卷；维护者须预建目录10004:10001/0770，仅包含`dispatch.sock`，端点由10004进程创建10004:10001/0660。镜像新增10001附加组，Compose显式group_add，固定UID/GID10004不变。应用只挂派发卷，不能挂broker凭据卷、传令牌或取得Docker权限；当前没有自动修改API/Worker编排、创建卷/权限或启动监督器。

执行器首个认证10001连接后关闭监听，最多一项请求、20秒处理/2秒输出且受23秒服务等待/25秒进程退出保护；处理复用原策略、broker取密、HTTP与输出过滤。连接/输出丢失、坏帧、取消或未知都不接受第二项或重发；仅自身创建且inode未变的socket可清理，未知旧端点拒绝覆盖。broker凭据socket仍为独立只读卷，派发卷为执行器可写；两个通道都需SO_PEERCRED和固定目录权限。

应用库`dispatch_authorized_http_mcp_tool(factory, request=..., broker=...)`要求原网关已预留ToolInvocation，复用同factory的Durable broker保存和复检许可，再通过`dispatch_http_executor`一次发送。回执匹配原nonce/dispatch/摘要/来源/描述及时间，固定输出Schema/64KiB预算、当前DB许可/绑定lease与原call_confirmed审计、上游当前lease/授权均复检后才返回不可信观察。输出校验需要原已有jsonschema能力，应用如未安装可选组会拒绝，须获批部署方案而不能跳过校验。没有新生产依赖/表/迁移；远程工具产品注册、真实provider/映射/公钥、启动监督和精确出口规则仍待。未运行测试、构建、迁移、容器或网络请求。

### 可选固定公钥JWT验证链（未配置真实提供者）

`mcp_http_jwt`提供RS256/at+jwt的严格访问令牌profile，复用锁文件PyJWT2.13.0及cryptography；接口按[官方固定tag源码](https://github.com/jpadilla/pyjwt/blob/2.13.0/jwt/api_jwt.py)核对，未运行验签。维护者须另行批准公开JWK和外部主体/client映射，不能从令牌header/未验签claims或平台UUID自动生成批准。`HttpMcpJwtVerificationPolicy`包含原`request_digest`、`external_subject`、`client_id`、`keys`和带时区`valid_until`，期限不超原派发截止；keys最多8个，每项仅kty=RSA/alg=RS256/use=sig/key_ops=[verify]/kid/n/e=AQAB，2048–4096位且kid/模数不重复，n为规范base64url。这里只保存公钥，没有默认key、私钥路径或远程JWKS地址。

可信broker运行入口可显式调用`create_jwt_verified_http_mcp_broker(factory, upstream=..., policy=..., current_policy=..., platform_issuer=..., platform_audience=...)`，再将返回的Durable broker交给原`serve_http_mcp_broker`。该工厂不读取配置/取密/连库，固定构造Durable→VerifiedJwt→真实上游，外层一次领取提交后内层才取密验签；缺少真实upstream/当前可信策略解析器不能启用。解析器须从独立可信批准源读取该原请求的当前公钥/主体映射策略，完整变化或过期拒绝后续正常调用；不能用永远返回调用方输入的空实现代替当前批准校验。

令牌只接受RS256/at+jwt/匹配kid及封闭claims：iss/sub/aud/client_id/jti/scope/iat/exp/可选nbf。强制验签、单一字符串audience、显式外部subject/client、scope恰为批准集合且不重复、严格整数时间和零宽限，issued/expires与原lease一致且不超批准截止；未知扩展claims/其他算法/多aud拒绝。原lease读取与上游独立撤销不要求旧公钥仍有效，回查真实撤销时间，不从验签失败自造revoked。验签不提供consent/签发/撤销服务或全局jti重放检测，真实provider接线、OS出口和产品注册仍待。无新依赖/表/迁移，未运行测试/类型检查/构建/实际验签或网络联调。

## 可选单次知识解析器（仅隔离local/dev/test，未运行）

双配置离线预检：原`supportops_core.knowledge_ingestion_preflight`增加`--reader-policy-file`，仅新隔离读取模式必填；与`--approval-file`及独立`--expected-scope-digest`一同使用。该入口复用两侧运行时配置校验，比较scope/精确清单/物理根/当前源码摘要，重读后变化拒绝；两份期限取较早值核对600秒窗口。读取器文件须逐级root保护目录和root0444单链接普通文件，不能通过临时改宽权限绕过。报告整体升级为`knowledge_ingestion_preflight_v2`，原v1消费脚本须同步升级；只含固定码/范围/计数及匹配策略摘要/期限，错配隐藏实际范围。不连接DB/socket、检查挂载、读取/source、创建批准或启动容器。新读取器源码因共用配置解析发生变化，旧隔离adapter摘要须独立重新批准；原reader_v2/plaintext_v3字节摘要不变。本轮未运行预检或读取实际配置。

独立来源读取补充：`knowledge-reader.Dockerfile`及`knowledge-reader.compose.yml`提供10006单次读取器，应与应用和上述解析器覆盖文件显式合并。维护者提供`SUPPORTOPS_KNOWLEDGE_READER_IMAGE`的批准镜像引用、`SUPPORTOPS_KNOWLEDGE_READER_SOURCE_DIRECTORY`的单一来源目录和`SUPPORTOPS_KNOWLEDGE_READER_POLICY_FILE`的受保护策略文件；所有bind均`create_host_path=false`、来源rprivate/只读，只挂给读取器，API只挂专用Unix卷。这里没有默认值或实际配置，不能指定业务库/秘密目录/宿主机根/Docker socket。模板不更改宿主机来源权限，10006须已获明确只读/目录搜索许可。

读取器固定/source挂载并拒绝其下额外挂载，只接受固定root:0444的`/etc/supportops-knowledge/reader-policy.json`；字段与权限见[独立读取契约](../../docs/modules/knowledge/overview.md#独立身份的可选来源读取)。应用保护文件声明root_directory=/source、source_root_device/source_root_inode及完整write_policy；两个严格整数物理根字段与清单进入新的isolated_selection_v1摘要，再绑定原scope。读取器保护文件另绑定同一根身份、scope摘要、清单、当前读取/解析源码摘要与期限，打开目录描述符必须匹配；不能替代应用当前人员/Job/ACL授权。两文件须由维护者独立批准并保持一致，不从HTTP或模型输入自动生成或由客户端自行探测选择。

新读取adapter只能配当前隔离解析，两端当前源码变化须新scope/Job，不静默沿用旧隔离批准；原v2/v3字节摘要和选择保持。分别准备10006读取与10005解析单次进程后再明确执行原Job，API无需来源挂载、无Docker启动权限。两进程均network none/只读根、空环境/-I/-S/-B、CPU软2硬3/32FD/16进程/256MiB/core0；共用一次认证帧运输和运行前置检查，首连接60秒/处理8秒/收尾10秒/进程75秒的有界等待不替代客户端原deadline，也不承诺内核IO硬截止。读取socket另为10006:10001/0770目录与0660端点，SO_PEERCRED双向认证；旧端点不自动删除，未知不重启/重发，不能通过换卷或重建Job掩盖未知结果。

本补充没有启动服务、配置来源、读取策略/知识或变更数据库；未运行测试、类型检查、构建、Compose/挂载/UID检查、迁移或容器验收。实际来源批准、完整OS隔离及故障验收仍待，默认导入关闭和staging/production禁止保持，W27.5不因此勾选。以下为前轮解析器运行模板说明，单独使用旧reader_v2时仍由应用身份读取；选择新读取adapter时按本补充隔离来源。

`knowledge-parser.Dockerfile`只复制包入口、knowledge_content和knowledge_parser_isolated三份源码，不安装uv、应用、DB/模型SDK或第三方解析栈。由维护者提供`SUPPORTOPS_PYTHON_IMAGE`的已批准Python 3.12 digest及`SUPPORTOPS_KNOWLEDGE_PARSER_IMAGE`镜像引用；`knowledge-parser.compose.yml`是显式覆盖，不继承API的env_file、网络或来源挂载。代码/模板存在不代表镜像已构建或隔离已认证。

模板以10005:10005及附加10001组运行，network none、只读根、cap_drop ALL/no-new-privileges，CPU软2/硬3秒、nofile32、nproc16、core0、地址空间/容器内存256MiB。固定入口清空环境、Python -I/-S/-B，不接受额外参数。专用命名卷只含Unix socket：初始化服务仅设置10005:10001/0770目录，解析器RW、API只读挂载，无公开端口、应用Docker权限或自动重启。应用与解析器的实际UID映射须一致；不能把此卷换成业务目录或挂载来源、批准文件、秘密和Docker socket。

隔离源scope须独立批准`LOCAL_MARKDOWN_ISOLATED_PARSER_MANIFEST_DIGEST`，不能仅配置镜像或挂卷就视为来源授权。API仍需原默认关闭的人工导入开关、local/dev/test配置、PostgreSQL及受保护批准文件；这里的staging/production配置不能启用。原v3批准继续走原受限子进程，isolated_v1失败不回退。本覆盖只向实际人工执行者API挂卷，Worker没有自动导入调度；来源文件仍由原读取子进程在应用身份下读取，完整读取沙箱另待实现。

维护者应在确认原Job当前许可后、显式执行前单独启动一次解析器；启动不执行Job或读取来源，首个认证连接前最多等60秒，收到一项后关闭监听，处理等待8秒/收尾10秒、75秒进程退出保护。原来源读取/IPC共享的deadline仍是执行上限，不因解析器可等待更久续期。未知/取消不重发、不自动启动第二个解析器；后续新的获准执行需维护者重新准备独立单次进程。旧socket存在直接拒绝，只清理自身同inode端点；强制退出可能残留，须先核对旧进程已停止再由维护者处理，模板不自动删除卷/端点或重置Job。

两端SO_PEERCRED核对10001/10005，长度帧限制请求6,001,024字节、结果8MiB及1024字节信封开销，nonce/完整请求SHA/原scope解析摘要均匹配；只传有界base64正文，不传来源路径/人员/DB或凭据。回执不代表知识已物化，提交仍由原API事务当前授权与摘要复检；正文不写stdout/日志，不执行Markdown或外链。Linux前置检查仅核对部分运行事实，完整挂载/资源/进程/故障边界仍须隔离验收。未构建/启动镜像、执行Compose检查、实际解析/来源读取、测试/类型检查/迁移，27.5保持未完成。
