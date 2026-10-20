# 全项目待处理任务

所有任务基于 2026-10-12 的 AGENTS 快照，现已开始分项实施。W00–W28 是工作流，数字 X.Y 是可追踪任务 ID。详情与历史任务唯一归属见 [规划导航与覆盖矩阵](README.md)，行为契约见本目录 specs，执行边界见 [design](design.md)。

当前按用户要求优先实现，测试相关任务暂缓；实现完成即更新对应任务，混合实现/验收任务仅记录已实现部分并保持未勾选，不把未经执行的测试记为通过。

工程优先级与产品阶段独立；候选工作流的决策完成不等于功能完成。组间依赖表示交付依赖，组内前置设计/契约/测试准备可先开展。P0 Mock 低风险建单不依赖 R4 审批，真实外部接入另外依赖 W20；评测和灰度按发布子集运行，P2 不阻塞 P0。每项交付须有代码/接口/迁移或决策/验收证据，不能仅写“已完成”。

## 0. W00 基线核对、范围与历史任务归属

工程优先级：P0；产品阶段：全阶段规划；模块：M01–M12。

依赖：无。规格：[support-quality-evaluation](specs/support-quality-evaluation/spec.md)。覆盖：G01、G42。

完成标准：需求、阶段和历史任务都有唯一归属；已实现与待验收分别记录。

- [x] 0.1 逐项核对 AGENTS M01–M12 和差距表，固定当前工作区快照、缺口 ID 与代码证据；不改旧勾选状态。
  - 核对证据：本change README新增2026-10-15逐模块源码入口/当前实现/剩余边界/G缺口ID回链，当前3应用+1核心/12模块/56表，迁移至0044未执行。原始42项覆盖矩阵明确保留初始差距、不充当当前状态；根AGENTS修正关键词尚未接入、排队Run无实时授权、缺诊断图/动作/审批等待等过时描述，补会话/审批/工单/告警/发布灰度入口及真实系统/评测边界。旧change的20项验收状态未改，源码核对不等于运行验收，测试仍暂缓。
- [x] 0.2 核对 add-knowledge-lifecycle 的 5.2/5.3/5.5/5.6，记录已有用例、缺失验收和后续任务归属。
  - 核对证据：本change README“历史知识任务核对”逐项记录核心内容/服务、API、路由桩与PG注释/迁移/checkpoint既有用例的实际范围，以及ACL组合/管理员正文/拒绝脱敏、多连接并发/事件消费/恢复、真实浏览器/可访问性和完整检查缺口。明确版本数组→分页、submit→独立review→publish及AuthSession/sessionStorage的旧fixture待适配；未执行用例，不将静态差异报告为运行失败。旧四项勾选保持不变，唯一验收归11.1/11.2/11.6–11.8/12.10及28.3/28.8。
- [x] 0.3 核对 add-system-agent-dashboard 的 16 项未勾选记录，与归档完成规范/当前代码比对，区分待实现、待验证及重复承诺。
  - 核对证据：本change README“历史Dashboard任务核对”逐项对照旧16项、2026-09-14归档完成声明、当前健康/系统Dashboard/数据契约与源码。2.3/2.5/2.6和4.2/4.4/4.5/4.6已有实现，保留原验收；2.4/3.7/4.11/5.1–5.5尚需真实/矩阵/浏览器验收，5.6缺批准聚合启用门槛。记录三个Dashboard API用例、Worker单lease健康用例、Analytics桩E2E的实际覆盖限制，以及旧四排行键/localStorage/缺字段桩待适配；不把归档声明或API桩当当前通过证据，不伪造per-Agent进程租约。16项验收所有权保持原change，与18.8–18.23唯一回链；未运行/新增测试或更改旧状态。
- [x] 0.4 为每个工作流确定工程优先级、产品 P0/P1/P2、责任角色、交付证据与阶段入口；候选能力保持默认关闭。
  - 交付证据：README工作流表保留W00–W28的工程优先级/产品阶段/依赖，新增29项职责/交付证据/阶段入口逐项映射。角色为维护/业务/安全/数据/质量/运维职责分工，不虚构具体负责人已任命或企业决策已批准；标明本地实现授权、测试暂缓、实际迁移/部署/擦除另需授权，以及IdP/API/MCP/工单/provider/容量/来源缺前提。W24/25/26及自动更新保持启动门禁、固定单Agent/固定模型/无长期记忆/无向量，不以“决定暂缓”关闭功能任务。
- [x] 0.5 确认首批 429 场景、批准参考事实和 Mock 工具契约；真实企业数据/系统接入保持独立批准门禁。
  - 契约证据：README“首批429场景与Mock参考契约”按已归档2026-09-14内部评审S01–S06和当前mock_429_v1/mock_429_workflow_v1逐项固定request_id、RPM/TPM/并发/套餐/路由/未知参考事实与允许结论。S04只覆盖套餐硬配额分支，不冒称账务系统验证；明确五项只读能力、本人派生项目、用户提供ID、含时区≤15分钟、字段白名单/完整JSON预算、正常/空/超时/拒绝/冲突模式、10节点/5查询上限，以及唯一候选仍需当前获准规则evidence。默认关闭/production拒绝，诊断推荐不代表建单，真实企业/连接器仍须14.10/17.8/W20批准。没有新增企业批准或执行测试，14.11和W19保持待验收。
- [x] 0.6 建立待定决策清单：IdP、工单/日志系统、embedding、审批角色、Vault/KMS、负载与 RTO/RPO/留存；各决策关联阻塞任务。
  - 决策清单/责任角色/关联任务见设计末尾；外部选型与 P2 启动本身仍未完成。
- [x] 0.7 确定契约升级策略：分页响应、Run enum、结构化结果与 Agent 配置版本，定义旧会话/客户端兼容边界。
  - 决策证据：设计中的前后端同步分页升级、原六态保留、新状态启用门禁、旧正文与配置版本兼容边界。
- [x] 0.8 为总规划后续拆分 change 制定任务 ID 回链与单一验收所有权规则，避免把候选决策或 artifact 完备当成实现完成。
  - 所有权规则见设计末尾；当前直接实施沿用 X.Y，历史验收原归属不变。

## 1. W01 API 分页、错误与跨层契约补齐

工程优先级：P0；产品阶段：产品 P0 基础；模块：M01/M02/M06/M07。

依赖：W00。规格：[api-contract-completion](specs/api-contract-completion/spec.md)。覆盖：G05、G06、G07。

完成标准：三个剩余普通列表有服务端分页，API/Web 同步且资源隐藏一致。

- [x] 1.1 清点整集接口与调用方，确认工具目录、知识来源和知识版本迁移为分页；部门树和明确整体替换接口保留完整语义说明。
  - 调用方：工具管理列表、知识文档来源筛选/显示、知识详情版本历史；来源显示由文档响应提供，筛选按需搜索第一页。部门树、ACL/grant 整体替换和 SSE 保留完整集合语义，见设计中的契约升级说明。
- [x] 1.2 为工具目录实现数据库 count/offset/limit 与稳定排序，不再全量查询后内存分页。
  - 实现证据：`tool_services.py` 的 count/list 查询、`admin_tools.py` 的统一分页依赖、`ToolCatalogListResponse`；内置元数据补齐仅查询注册工具标识。测试按当前约定暂缓。
- [x] 1.3 为知识来源实现 tenant/权限过滤、确定排序与分页响应，保留负责人名称映射。
  - 实现证据：`knowledge_services.py` 来源查询/count、`admin_knowledge.py` 管理员权限依赖和当前页名称映射、`KnowledgeSourceListResponse`；增加来源名称搜索。测试暂缓。
- [x] 1.4 为知识版本列表增加稳定版本/唯一键排序、标准分页 Schema，并同步详情页请求。
  - 实现证据：版本 list/count 查询、`KnowledgeVersionListResponse`、`getKnowledgeVersions` 与知识详情 `AppTable` 服务端分页；编辑/回滚 revision 独立取最新版本，翻页不覆盖草稿。测试暂缓。
- [ ] 1.5 为三个列表添加首页、后续页、空结果、越界页、非法页码及跨租户测试。
- [ ] 1.6 同步 api.ts/types.ts/OpenAPI 与筛选重置页码、加载/空态/错误交互，验证窄屏和键盘操作。
  - 已实现：三个列表 API 返回类型与 OpenAPI Schema、文档来源摘要、来源搜索与筛选页码重置、工具/版本历史服务端分页及加载/空态/错误处理；过期请求不覆盖新列表。窄屏和键盘验收暂缓，整项仍未完成。
- [x] 1.7 统一新 job、action、approval、retrieval 错误码与资源隐藏策略，定义新 Run 状态和恢复事件的兼容处理。
  - 实现证据：共享 ErrorResponse OpenAPI（健康 503 单独模型）、job 许可/授权处理器、领域与 HTTP 的资源隐藏固定 NOT_FOUND 文案，认证固定码/no-store/Bearer challenge；docs/04 按 job/通知/尝试/动作/审批/检索/配额列稳定码和 HTTP/持久化失败区别。API 枚举/Web 八 Run 状态及恢复/等待事件已同步；runContract 检查快照/发送/详情/取消与状态 SSE，非法/未知契约阻止新提交而不伪造终态，写请求不重放。修复取消回执仍 cancelling/等待却提前显示 cancelled 的客户端问题，保留 Run/订阅等待事实确认；业务动作 unknown/对账仍独立。代码/契约核对完成，测试/类型检查/构建未执行。
- [x] 1.8 明确 live/ready 与模型/连接器依赖健康的职责；避免每次 readiness 触发昂贵模型请求。
  - 实现证据：`health.py` 接口说明；live 仅存活、ready 仅数据库/Redis，模型能力探测仍走管理端显式操作，未加入 readiness。
- [x] 1.9 在实际修改涉及的企业/Dashboard 聚合路径提取可复用查询服务，保持结果契约；不进行无关全量重构。
  - 实现证据：`dashboard_query_services.py` 提供共用 Run/Agent 范围与健康 SQL 表达式，`dashboard_filters.py` 统一查询输入/时区/区间校验。已有 summary/timeseries/错误聚合/排行和普通列表复用，保留原字段/路径并追加桶终点和全范围 health_* 计数；租户及关联租户约束、条件先于 SQL count/分页。未重构无关企业路径；测试暂缓。

## 2. W02 登录、会话安全与企业身份接入

工程优先级：P0/P2；产品阶段：基础 P0；企业身份按 P1 需求启动；模块：M01/M03/M12。

依赖：W00。规格：[enterprise-authentication-hardening](specs/enterprise-authentication-hardening/spec.md)。覆盖：G02、G03、G04。

完成标准：生产无固定登录预填；会话模式有明确验证；SSO 未批准时不启用。

- [x] 2.1 移除登录固定账号/密码预填，检查源码与构建产物不携带预置敏感值，保留本地配置 bootstrap。
  - 实现证据：登录页两个输入初始为空；源码与重新生成的 dist 旧预填值匹配均为 0，服务端 bootstrap 未变。为清除旧产物执行现有构建，TypeScript 与 Vite 构建通过；未运行业务测试。
- [x] 2.2 评估当前 localStorage/访问令牌与失效边界，记录短期令牌重登或服务端会话/刷新模式的决策及影响。
  - 决策证据：design 第 11 节记录短期 Bearer + 可撤销 AuthSession、默认 60 分钟/5–480 分钟范围、sessionStorage 与非秘密跨标签信号；无刷新/Cookie/SSO，明确同源脚本读取和有效 Bearer 重放风险。旧 JWT 强制重登、API/Web 同步及禁止绕过撤销语义回滚；测试暂缓。
- [x] 2.3 实现所选基础会话过期、注销、撤销与 401 恢复契约，不以 helper 名称声称已有刷新。
  - 实现证据：auth_session_services、/auth/sessions 本人 SQL 分页、单次/全部撤销、密码/角色/状态变化永久失效及最小审计；API 当前身份和 SSE 每轮复检，auth.expired 控制帧不改业务事实。Web 会话页面/用户菜单、过期/跨标签清理、守卫聚焦与每分钟复检、网络失败重试、迟到 401 保护及无写请求重放；helper 新名称明确无刷新。注销不取消已提交 Run；测试/类型检查/构建未运行。
  - 后续最小修复：此前 AUTH_SESSION/RUNTIME_ALERT 误登记为 ResponseStrategy 成员，已移至 AuditResourceType；用独立枚举导入验证正确成员存在且 ResponseStrategy 仅 tool/provider，通过。该检查仅定位缺失审计枚举，不是登录/告警业务测试。
- [x] 2.4 若选用刷新/服务端会话，设计存储、轮换、重放检测、租户绑定和过期 Schema/迁移。
  - 实现证据：0040 增加 AuthSession 和 User.auth_generation，JWT jti 固定租户/用户/期限/代次，登录用户锁与 SQL 代次增量协调并发安全修改。每次登录生成新 jti，撤销/过期/旧代次重放拒绝；未采用刷新凭证，刷新轮换/重用检测不适用，有效 Bearer 重放仍可能。无旧会话补造、无令牌/IP保存，历史存在时降级拒绝；迁移未执行，旧签发测试 fixture 待适配。
- [ ] 2.5 若采用 Cookie 会话，实施 HTTPOnly/SameSite/Secure 与 CSRF/跨域校验，验证多标签页与注销。
  - 当前未采用 Cookie，本项保持候选而不虚构 Cookie/CSRF 验收；Bearer 跨标签收口由 2.3 实现，浏览器验收仍待 2.6。
- [ ] 2.6 增加停用、角色变更、过期、重放和登录错误的 API/浏览器测试，不记录凭据。
- [ ] 2.7 确认企业 IdP、显式主体映射、角色范围及本地管理员应急方案；未批准保持候选任务。
- [ ] 2.8 在批准的测试 IdP 上实现 SSO 回调与 issuer/audience/state/nonce 校验，不自动建号或扩权。
- [ ] 2.9 实现下游委托 OAuth 的 consent、最小 scope、受众、撤销和审计，接入受控凭据提供者。
- [ ] 2.10 验收跨租户映射拒绝、IdP 故障应急、回调伪造与授权撤销，并记录启用/回滚步骤。

## 3. W03 Worker 独立心跳、调度与停机

工程优先级：P0；产品阶段：产品 P0 基础；模块：M10/M11。

依赖：W00。规格：[runtime-reliability](specs/runtime-reliability/spec.md)、[agent-health-computation](specs/agent-health-computation/spec.md)。覆盖：G09。

完成标准：长任务不阻断心跳/维护，活动计数准确并可优雅停止。

- [x] 3.1 将服务心跳从 await execute_agent_run 的消费循环分离，按配置周期更新服务租约。
  - 实现证据：Worker 独立 heartbeat 任务、配置心跳周期/服务租约与两倍周期约束；长 Run 不阻塞续租。测试暂缓。
- [x] 3.2 将恢复扫描、健康刷新和 checkpoint 清理拆为独立有限周期任务，定义任务失败不互相拖垮的行为。
  - 实现证据：三个独立 periodic 任务、操作超时、失败计数与下一周期重试；非关键维护失败仅降级，不取消其他任务。测试暂缓。
- [x] 3.3 按实际执行集合更新 active_run_count，区分队列消息深度与持久化 queued Run 数。
  - 实现证据：`execute_agent_run.on_claim` 在领取事务提交后登记活动 Run，结束时移除；租约 metadata 单独记录 queued Run 数据库计数及队列深度可用性。测试暂缓。
- [x] 3.4 增加可配置 Worker 最大并发、容量背压和公平调度，保留单会话执行约束接口。
  - 实现证据：默认并发 1、tenant_round_robin/fifo 可配置；共享 RunSchedulerState 行锁持久化租户游标，跨 Worker/重启轮转，租户内按创建时间/唯一 ID 领取。只选择成功通知、无会话许可、最早输入和租户剩余额度的 queued Run，复用原子 attempt 领取；死信不被旧通知绕过。停机期间领取回滚整事务，容量满不领取，重启不重置游标；0027 未执行，多 Worker 验收暂缓。
- [x] 3.5 实现任务监督、异常退出与日志关联；避免后台协程静默终止后仍报告健康。
  - 实现证据：后台任务 done callback 监督、连续心跳失败停止领取并非零退出；维护故障显示 degraded，日志绑定启动实例与 Run 标识。测试暂缓。
- [x] 3.6 实现 SIGTERM 停止领取、限定等待、在途取消/移交和 stopped 租约更新。
  - 实现证据：停止消费者、在宽限期维持心跳、超时后协作取消/收敛 Run 与 checkpoint、最后写 stopped；未确认取消保留 degraded 并报错，不盲目移交。强制进程崩溃恢复仍由 W05 承接。测试暂缓。
- [ ] 3.7 添加长模型调用超过租约、心跳失败恢复与时钟容差测试。
- [ ] 3.8 用隔离 Redis/PostgreSQL 验证多 Worker 心跳、活动计数、积压与停机状态。

## 4. W04 Run 通知 Outbox 与持久化模型探测

工程优先级：P0；产品阶段：产品 P0 基础；模块：M04/M08/M10/M12。

依赖：W00、W03。规格：[runtime-reliability](specs/runtime-reliability/spec.md)。覆盖：G08、G10。

完成标准：数据库提交后的通知与探测任务可恢复、有限重试并可诊断。

- [x] 4.1 新增 Run 通知 Outbox 模型/索引/事件键与状态迁移，通过 Alembic 保存并发约束和字段注释。
  - 实现证据：`RunNotificationOutbox`、迁移 `20261012_0018`；唯一 Run/事件键、状态与次数约束、到期索引、字段注释；旧 queued 回填通知且旧尝试数为 0。迁移尚未执行。
- [x] 4.2 把用户 Message/Run/run.queued/Outbox 写入同一业务事务，保留原 Idempotency-Key 语义。
  - 实现证据：`create_message_and_run` 同事务追加通知，幂等返回路径不创建第二个 Outbox；旧 queued 恢复入口排除已有 Outbox 的 Run，避免绕过投递上限。测试暂缓。
- [ ] 4.3 实现分批 Outbox claim/投递/确认，验证重复分发只通知同一个 Run。
  - 已实现：`run_outbox_services.py` 每条独立事务/`SKIP LOCKED`/有限 Redis 超时，API 尽力即时分发与 Worker 独立分发；旧恢复不绕过 Outbox，前轮阻塞时不消耗后轮次数。重复分发验收暂缓，整项未勾选。
  - 分批领取实现补齐（2026-10-18）：先按next_attempt_at/id稳定排序只读取得固定候选ID/Run/tenant，批次默认20、配置1–100，调用limit只能降低；候选事务结束后逐条新事务先Run再Outbox，两处均SKIP LOCKED。候选不是许可，锁内复检固定绑定/当前状态/到期及原会话/额度/退避门禁；忙、已被处理或推迟的候选不入队、不消耗次数，继续本批下一项，不在原队首反复等待。保留单条Redis超时、原Run UUID、至少一次通知和各条独立提交，不持有整批锁；新到期记录留下一次，不保证整队无饥饿或表锁/整体IO硬截止。API即时入口仍只原Run/limit=1，Worker共用入口；源码纳入发布清单，无新表/依赖/接口/迁移。未运行Redis/数据库、多Worker、重复/取消故障测试或构建，4.3仍保留未完成，163/302不变。
- [x] 4.4 实现退避、次数/时间上限、死信状态与授权重试入口，不无限重推 queued Run。
  - 实现证据：配置指数退避/每周期 8 次/24 小时窗口、dead_letter、当前租户管理员分页查询与 revision 条件重试；保留累计次数、原消息/Run/版本及重试事件，取消或已执行 Run 不复活。测试暂缓。
- [x] 4.5 评估 Redis List+Outbox 与 ACK/Streams 的恢复取舍，固定所选交付语义和监测口径。
  - 决策证据：设计采用现有 Redis List + PostgreSQL Outbox 至少一次通知；delivered 后仍 queued 时有限再次通知，消费依据 Run 状态。无需引入新中间件；claimed 后崩溃归 W05，不能用通知重发冒充执行恢复。
- [x] 4.6 定义 ModelProbeJob 固定端点/模型版本、凭据 revision、阶段、attempt 与终态契约。
  - 实现证据：ModelProbeJob 与产品 TestRun 一对一，固定租户/端点/稳定模型/版本/凭据 revision，记录 owner/lease/generation、累计领取、有界尝试摘要/退避/取消/终态；同模型活跃 job 部分唯一索引。0028 不补造旧执行次数，旧无 owner 活跃历史收敛 failed；升级前停止旧 API 执行者。迁移未执行。
- [x] 4.7 把 BackgroundTasks 探测迁移为可恢复 job 执行入口，避免和聊天 Run 共用会话 checkpoint。
  - 实现证据：API 同事务创建 TestRun/job 后只返回 202；Worker 独立有界探测消费者与续租/监控，领取/进度/验证状态/终态写入前复检 owner/generation/lease 与当前管理员/租户/固定引用。临时网络/429/5xx/超时/过期有限重试，默认三次/十分钟；旧执行者不能覆盖版本验证。停机保留到期恢复，不使用聊天 Run/checkpoint，也未主动执行真实探测。
- [x] 4.8 同步管理页测试状态轮询、取消/失败/恢复提示及旧测试历史读取。
  - 实现证据：响应 nullable probe_job，分页历史批量装配任务摘要；新增租户管理员 cancel 接口，原子终态/fencing，队列取消不执行。管理台恢复读取既有 running/queued、显示累计次数/退避/状态读取错误/取消；终态与旧历史兼容，不把刷新当作重发。浏览器验收按约定暂缓。
- [ ] 4.9 在提交、入队、消费确认与探测执行边界注入故障，验证无永久 running 或重复业务输入。

## 5. W05 Run 租约、执行尝试、恢复与取消

工程优先级：P0；产品阶段：产品 P0 基础；模块：M08/M09/M10/M12。

依赖：W03、W04。规格：[runtime-reliability](specs/runtime-reliability/spec.md)。覆盖：G11、G13。

完成标准：崩溃任务收敛，旧执行者不能覆盖产品/checkpoint；取消不丢副作用事实。

- [x] 5.1 设计 RunAttempt、owner、lease_expires_at、generation/fencing 与恢复原因；固定 Run/attempt 状态迁移。
  - 实现证据：`RunAttempt`/Run 当前尝试与 generation/会话许可字段、设计中的尝试状态及锁顺序；隔离、只读有限重放、旧执行者确认规则已固定。
- [x] 5.2 编写增量迁移与旧 queued/running 数据回填策略，不伪造旧尝试次数或丢历史事件。
  - 实现证据：迁移 `20261012_0019` 新增尝试/许可/输入序号与历史关联；旧 running/cancelling 标明需要确认旧执行者，不伪造 attempt。修正 0018 queued 回填兼容 SQLAlchemy 枚举存储大小写。迁移尚未执行。
- [x] 5.3 为 claim/续约/终态/正文事件写入加当前 attempt 校验，防止旧执行者迟到提交。
  - 实现证据：`run_attempt_services.owned_run` 对 current pointer/owner/generation/租约/会话许可统一加锁校验；Worker 领取、续租、增量、产品完成/失败/取消均使用此边界。旧尝试只允许追加自己的历史观测。测试暂缓。
- [x] 5.4 验证现有 saver 的 namespace/提交指针能力，选择可阻止失效 checkpoint 写入的隔离方案。
  - 代码核对证据：已安装 `AsyncPostgresSaver.aget_tuple` 按 thread/namespace/checkpoint_id 查询，aput 按同范围保存；新实现采用独立 attempt thread，并返回精确 checkpoint ID。避免复制内部 pending writes，从产品事实重建；未执行 saver 测试。
- [x] 5.5 实现 checkpoint attempt 隔离和有效提交指针；不具备安全隔离时要求确认终止旧执行者才移交。
  - 实现证据：attempt 独立 thread、精确 checkpoint ID、产品事务中的会话有效提交指针；旧无 owner Run 的恢复必须确认旧执行者已停止。升级前停止旧版 Worker；测试暂缓。
- [x] 5.6 实现过期 running/cancelling 扫描、可恢复错误分类和有限执行重试，未知动作交由对账。
  - 实现证据：独立过期扫描、只读且 none 副作用的有限恢复（默认 2 次）、临时网络/429/5xx 与配置/权限错误分类；未知/未证明只读尝试隔离，取消优先收敛，恢复通知不更换固定版本。测试暂缓。
- [x] 5.7 处理 checkpoint 已写而产品事务失败，隔离不一致尝试并恢复有效产品事实，禁止清除其他有效尝试。
  - 实现证据：产品提交失败的 attempt 标记 quarantined，允许已证明只读时新尝试重建产品历史；不继续失败 thread、不删除其他 thread、不提前更新提交指针。测试暂缓。
- [x] 5.8 增加恢复事件/attempt 详情与手动诊断入口；恢复保留原 Agent/模型/凭据引用。
  - 实现证据：分页 `/v1/admin/runs/{id}/attempts`、generation 条件 `/recover`，恢复/阻塞事件与 attempt 关联；跨租户隐藏、有效租约/终态/未知副作用拒绝。测试暂缓。
- [x] 5.9 将取消信号接入模型/工具边界与后台输出；定义上游无法即时中止时的安全收敛。
  - 实现证据：独立租约/取消监督、每次模型/工具 middleware 与正文写入边界复检；取消推理子任务，旧尝试迟到不能提交，未知副作用维持待对账。测试暂缓。
- [ ] 5.10 覆盖删除会话、授权撤销、等待审批和对账期间的取消，禁止迟到答案复活已取消 Run。
- [ ] 5.11 真实 PostgreSQL 多 Worker 竞争/租约转移/旧执行者写入测试覆盖产品、checkpoint 与事件一致性。
- [ ] 5.12 故障演练后核对业务 Run 只出现一个有效终态、无永久占用、无重复已完成副作用。

## 6. W06 同会话执行顺序与输入快照

工程优先级：P0；产品阶段：产品 P0 基础；模块：M08/M09/M10。

依赖：W05。规格：[runtime-reliability](specs/runtime-reliability/spec.md)、[context-and-budget-control](specs/context-and-budget-control/spec.md)。覆盖：G12。

完成标准：同会话串行、跨会话并行，当前轮不会读取后续输入。

- [x] 6.1 确定同 tenant/conversation 的输入顺序、会话执行许可与排队冲突响应，建立数据库约束/锁契约。
  - 实现证据：会话 next_input_sequence、Message.turn_sequence、Run.input_sequence 唯一约束与 active attempt 许可；后续输入明确返回 queued，顺序不依赖时间戳精度。
- [x] 6.2 实现只允许一个可执行 attempt 的会话调度，后续消息显式排队而非静默并发。
  - 实现证据：会话许可与最早 input_sequence 门禁、跨会话有界并发；通知分发使用相同输入顺序。测试暂缓。
- [x] 6.3 保存本轮输入序号/消息边界，历史加载只到当前输入并保持同时间戳唯一排序。
  - 实现证据：发送事务分配固定序号，快照只读取 turn_sequence <= 本轮输入；前轮助手按所属轮次排序，即使回答提交晚于后轮输入也能正确重建。
- [x] 6.4 协调消息提交幂等、会话删除、会话锁释放、恢复与过期执行者转移。
  - 实现证据：重复幂等输入不分配新轮次；取消/完成/失败释放匹配许可，过期移交保留原输入与版本，删除后拒绝新执行；未知动作保持冻结。测试暂缓。
- [x] 6.5 定义等待审批期间允许的新消息/只读追问与队列规则，参数修改不能偷偷改变冻结动作。
  - 决策/实现证据：design 第 9 节与 API 文档明确 API 后续输入幂等 queued、最早 input_sequence 在 waiting_approval/waiting_action 终态前阻止后轮执行，Web 当前输入禁用并只读当前获准详情；追问不插队改当前动作，其他会话仍受额度约束。核对 run_attempt_services 会话领取门禁与 approval_services.replace_action_proposal：显式 amend 保存新提案/supersedes/新审批、旧批准失效，执行 attempt/消费/未知副作用拒绝改参换键；等待释放模型 Worker，批准仅继续动作。已有实现复用，不创建旁路；测试暂缓。
- [x] 6.6 同步聊天页排队、取消与恢复状态，禁止刷新启动第二个相同 Run。
  - 实现证据：消息/快照输入序号与尝试字段、前端排队/恢复事件、旧临时正文 replace 清空、死信/待对账提示；刷新只按服务端快照游标重订阅，不创建新 Run。浏览器验收暂缓。
- [ ] 6.7 增加同会话双发送、多 Worker 竞争、前轮失败后后轮继续及跨会话并行测试。
- [ ] 6.8 核对 checkpoint 恢复、历史重建和长期对话不会包含未来消息或重复本轮输入。
  - 已代码核对/加固：正式 checkpoint thread 含 tenant/conversation/run/attempt，已有未提交正式 thread 拒绝复用；恢复新 attempt 从产品 input_sequence 重建，提交指针仅产品事务成功后更新。Worker 有限历史及首轮边界、当前输入必需；history_context_services 复检 ContextSnapshot 与 Run 当前 input ID/序号一致、来源唯一/无未来/实际已用来源子集；消息 UUID 进入图输入防混淆。未执行真实 checkpoint/长对话/恢复验收，整项保持未完成。

## 7. W07 实时授权、风险映射与工具额度

工程优先级：P0；产品阶段：产品 P0/P1 治理；模块：M03/M05/M06/M09/M10。

依赖：W00、W05。规格：[runtime-authorization-policy](specs/runtime-authorization-policy/spec.md)。覆盖：G14、G15、G16。

完成标准：每次调用使用当前主体/资源权限；配置与限额在执行边界生效。

- [x] 7.1 在领取和每次工具调用前重新校验 active 用户、可信租户与当前 Agent grant，保留旧角色快照仅供审计。
  - 实现证据：`current_run_identity`、领取/续租/模型与工具边界及提交前复检；工具实际运行上下文替换为数据库当前角色，API 同步拒绝停用租户。测试暂缓。
- [x] 7.2 统一工具目录/静态定义/Agent 绑定的策略解析，固定工具版本、required_roles、risk 和启停来源。
  - 实现证据：`tool_policy.resolve_tool_policy` 同时检查固定绑定、服务端静态最低权限、目录实时策略/实现键与版本；调用许可保存实现/目录版本，返回前策略变化拒绝结果。测试暂缓。
  - 版本补齐：AgentVersion.resolved_tool_versions 固定实现版本；`20261013_0024` 只回填已知本地 v1，不改变旧 config/digest。旧固定实现不可用即拒绝，不静默使用新版；恢复的只读证明按工具 ID + 实现版本判断。
- [x] 7.3 新增紧急撤销语义，使停用影响历史版本和排队 Run；与仅允许新绑定的开关明确区分。
  - 实现证据：目录 is_enabled 现在控制新绑定与所有后续调用；独立 is_revoked 优先拒绝，普通启用不能解除。管理员显式撤销/解除接口要求 expected_version，Web 确认弹窗与状态显示已接入。
- [x] 7.4 建立资源级 RBAC/ABAC 判定接口，覆盖用户、部门、项目、工单与字段范围，默认拒绝。
  - 实现证据：`resource_policy.TrustedResource/authorize_resource` 校验租户、主体、部门/显式授权、动作及字段，不把模型参数当资源事实；当前身份工具接入本人字段投影。真实项目/工单事实由后续受控连接器提供，未知资源默认拒绝。
- [x] 7.5 规范服务端工具风险 R0–R4 与旧 low/medium/high 保守映射，按参数/目标敏感度复算。
  - 实现证据：low→R1、medium→R3、high/未知→R4；静态/目录取更严格风险，bulk/export/破坏性参数提升风险。发布校验与运行门禁使用相同映射，Prompt/目录不能降级静态风险。
- [x] 7.6 实现单工具 max_calls_per_run 与工具总量、并行许可的原子计数，取消/失败计数策略明确。
  - 实现证据：`ToolInvocation` + Run/租户行锁在执行前原子计数；整个业务 Run 各 attempt 共用额度，失败/取消/未知结果仍消耗，denied 不消费，同一 call_id 不重复执行。失效尝试未回执调用标记 unknown。测试暂缓。
- [x] 7.7 增加租户请求/工具配额及有限消费机制，配额错误可观察而不伪造业务失败。
  - 实现证据：UTC 每日 Run/工具额度、租户有效执行并发，发送幂等后才消费新 Run 配额；原子租户锁与稳定 429 错误，配额满时通知暂缓不消耗投递次数。测试暂缓。
- [x] 7.8 为目录编辑、权限拒绝、风险升级和紧急撤销追加审计，参数只记录批准摘要/哈希。
  - 实现证据：目录/撤销写 AgentAuditEvent，调用许可/拒绝/完成/失败落 ToolInvocation 和带 attempt 的 RunEvent；只存参数/结果 digest 与批准元数据，不存原始参数/完整工具正文。
- [x] 7.9 未接通有效审批前阻止所有需审批副作用，不允许 approval_policy 字段直接授权执行。
  - 实现证据：网关明确拒绝 approval_required，无审批记录不执行；R2 写动作仍要求动作幂等/复核链。配置 required 是申请条件，不能成为执行凭证。
- [ ] 7.10 测试排队后停用/撤 grant、目录修改、模型资源篡改、跨租户与并行抢最后一次额度。
- [x] 7.11 将策略相关错误与可执行原因同步 API/Agent 管理 UI 和 Trace，避免 Prompt 承担权限判断。
  - 实现证据：服务端稳定运行错误中文说明；SSE/会话恢复/错误详情/Trace 显示处置原因，Agent 管理显示紧急撤销并禁用绑定，保留已有单工具限额与审批配置。授权由网关判定，不由 Prompt 决定。

## 8. W08 统一工具网关与受控协议连接

工程优先级：P1；产品阶段：P0 本地/Mock；P1 外部连接；模块：M06/M09/M10/M12。

依赖：W07。规格：[governed-tool-connectivity](specs/governed-tool-connectivity/spec.md)。覆盖：G25、G41。

完成标准：本地/API/MCP 工具共享治理链，无协议绕过和不受控来源。

- [x] 8.1 定义 ToolInvocation 输入、可信上下文、工具/连接器版本、风险与结构化结果契约。
  - 实现证据：`ToolInvocation`、`ToolPermit` 与 attempt 可信上下文；输入/实现/策略/风险/结果摘要独立记录，本地 SDK 适配不接受模型覆写上下文或图状态。
- [x] 8.2 实现统一执行入口，封装授权、Schema、限额、风险、审批钩子、调用、输出过滤和审计。
  - 实现证据：`ToolExecutionGateway` 接入 middleware，严格 Schema/未知参数拒绝、实时授权/额度/审批门禁、返回前复检、脱敏/大小上限/审计；后续远程连接器必须复用入口。测试暂缓。
- [x] 8.3 迁移两个内置工具到入口并保留既有 tool_id/版本绑定兼容；占位查询明确不可用而非伪造业务事实。
  - 实现证据：既有两个稳定 tool_id 由 Worker 网关调用；身份工具使用当前角色，工单占位明确无法确认存在或读取内容，不再伪称“未找到工单”。
- [ ] 8.4 实现获批业务 API 连接器的目标/响应 Schema、超时、大小限制及只读有限重试。
- [ ] 8.5 验证现有依赖的 MCP Client 支持与协议版本，再接入一个隔离 MCP Server 做发现/调用契约。
  - 已实现协议部分（2026-10-19）：源码/uv.lock确认原依赖无MCP SDK；新增mcp-isolated可选组并用uv锁定官方mcp==1.30.0，不升级既有包，按官方固定tag核对ClientSession/stdio/Server API，协议显式固定2025-11-25，不自动采用2.x/新协议。新增只访问随包固定Mock的stdio客户端/Server及catalog/read-status单次入口，封闭无参数只读状态Schema、握手/来源源码与目录摘要/结果双表示校验、当前连接通知失效、有限等待/无重试、固定错误码。独立Compose/Dockerfile使用10002 UID、network none、只读根/无应用卷或env_file、env-i固定环境、cap-drop/no-new-privileges及资源上限；入口核对隔离前提，普通API/Worker不加载可选SDK、不接DB/Redis/模型。只提供模拟协议实现，不授予网关/Agent业务执行，不写8.6治理库或伪造8.10连接健康。未构建/启动镜像或运行发现/调用/隔离验收，按8.1约定混合项保持未完成；真实HTTP授权、认证IPC/许可和产品接线仍待8.7/W20，整体164/302。
- [x] 8.6 实现可信来源/allowlist、工具名称消歧、Schema/digest 版本固定与 listChanged 重新验证。
  - 实现：tool_discovery_contracts/services、追加式ToolDiscoveryRevision/0051及管理员来源策略/失效/目录提交/固定绑定解析API；精确来源指纹、管理员显式工具全描述摘要allowlist和期限，租户/来源/远端名SHA256稳定消歧，固定输入/输出Schema及目录摘要。listChanged或手工刷新追加pending围栏，旧记录ID/目录摘要拒绝，提交只接当前pending修订，迟到结果不能复活旧版；只保存获批工具、未知工具不暴露，变更须新批准/重新验证。目录128KiB/100项/4096节点/16层、严格重复/非有限/引用拒绝；历史ORM与PG追加保护、最小审计、Web API/types同步。66表/0051，目录治理不调用MCP、不把Registry接入执行链，8.5客户端/8.10界面及8.11测试独立待办；按当前约定未运行测试、构建、迁移或真实连接。本项按治理功能实现完成，不代表远端接入或生产验收。
- [ ] 8.7 实现 HTTP MCP 授权受众/scope 与撤销，禁止把平台 JWT 任意透传到目标服务。
  - 原派发只读查询入口（2026-10-19）：新增mcp_http_dispatch_queries、本人Run下http-mcp-dispatches分页/单项GET与聊天结构化结果/最新失败轮次抽屉。SQL先限制tenant/原actor/本人会话/Run，再count/稳定created_at+id分页，仅选择固定引用/状态/时间，不加载request_payload/token_state/摘要/lease/nonce；管理员不绕过本人限制，原执行授权失效不阻断历史元数据查询且不授新许可。独立is_expired绑定服务器observed_at；claimed不证明取密，bound不证明远端执行或业务成功，过期不等于撤销。成功读取no-store，前端按token/Run/序号拒绝迟到或未知契约，手动刷新/分页，不取密/联网/重发/撤销。新源码纳入发布清单，无新依赖/表/迁移；69表/0053、165/302不变，真实provider/产品执行注册仍待，8.7不勾选。未写或运行测试、类型检查、构建、迁移或浏览器验收。
  - 应用侧许可/结果后验接线（2026-10-19）：新增dispatch_authorized_http_mcp_tool，以已有ToolInvocation和同factory的Durable broker冻结请求、复用当前上游/平台门禁保存原派发、只对authorized一次socket发送；回执后匹配nonce/来源/描述/观察时间，复检固定输出Schema/64KiB预算、DB当前许可/原lease/同dispatch与lease的call_confirmed审计，再复查上游当前lease/授权。缺可选jsonschema能力拒绝而不跳过验证；只返回不可信观察，不替代未来网关账本/来源证明。无实际Worker注册、provider/部署，69表/0053、165/302不变，未写或执行测试/构建/迁移/容器或网络联调。
  - 应用到执行器单次派发桥接（2026-10-19）：新增mcp_http_executor_ipc，10001应用侧只通过`/run/supportops-mcp-executor/dispatch.sock`发送已固定的`HttpMcpExecutorRequest`，10004执行器`serve-once`只接一个连接；双向SO_PEERCRED、目录/端点权限、23秒总边界、nonce/dispatch/request/source/descriptor匹配，回执未知不重发、不新建许可。Compose增加独立10001/10004派发socket卷，broker凭据socket仍分开；执行器处理后关闭监听，数据库和令牌仍只在broker侧。源码进发布摘要，无新依赖/表/迁移，69表/0053、165/302不变；未接入Worker/Agent产品调用、真实provider、启动监督或容器验收，8.7仍未完成，测试/构建/迁移/网络继续暂缓。
  - 固定公钥JWT访问令牌验签（2026-10-19）：新增mcp_http_jwt，复用uv.lock的PyJWT2.13.0与既有cryptography，无新依赖；只接受维护者显式批准的1–8个RSA2048–4096位/指数65537公开JWK、固定RS256与at+jwt。header只alg/typ/kid，拒绝动态jku/x5u/内联key/crit/未知kid/算法降级；规范base64url及严格有界JSON拒绝重复/非有限/未知claims。验签强制精确issuer/单一字符串audience/显式external_subject/client_id、整数iat/exp/可选nbf、jti及无重复的精确scope，零时间宽限，时间与原lease元数据及批准期限一致；不从平台user UUID自动映射外部sub、不缓存令牌或自行发现密钥。VerifiedJwtHttpMcpCredentialBroker包装真实上游，在取密前后及当前许可复检固定策略/公钥/主体映射；变化拒绝后续调用，原lease查询/独立撤销不因旧公钥过期被阻止，上游真实撤销时间仍须回查。源码进入发布摘要，模块仅库函数，不配置实际key/provider/主体映射，不代替consent/签发/撤销服务或持久化一次派发；69表/0053、165/302及8.7/20.5未完成状态保持。仅源码/官方固定tag接口核对，按用户要求未写或运行测试、类型检查、构建、迁移、真实验签/取密/撤销或网络联调。
  - 独立HTTP进程入口与运行模板（2026-10-19）：新增mcp_http_executor_contracts/runtime/cli、独立Dockerfile与显式profile Compose。固定Linux10004 UID/GID、清空环境、-I/-B、无额外继承FD、无能力/NoNewPrivs/seccomp模式及CPU/地址空间/句柄/进程/core限制前置检查；只读root策略固定原授权策略/平台标识/精确IP集合，文件root0444/目录0755、拒绝链接/替换/变动。stdin最多192KiB一条换行帧、3秒等待，冻结部署策略/调用/描述/参数摘要后接现有IPC/HTTP；stdout最多128KiB、2秒非阻塞输出，仅脱敏不可信观察或固定码，无令牌/原错误/重试。20秒工作等待及25秒进程退出保护，SIGTERM/SIGINT/输出丢失保留原派发未知，不以进程退出断言远端未执行。模板无应用环境/业务卷/公开端口，日志关闭，必须显式提供已获批镜像/只读策略/专用socket卷/出口网络；不创建实际目标、凭据或宽泛网络许可。未新增依赖/表/迁移，69表/0053、165/302不变；真实provider/签名或内省、部署层精确网络规则/运行验收与产品注册仍待，8.7/20.5不勾选。按用户要求未写或运行测试、类型检查、构建、容器、迁移、实际取密/网络。
  - 持久化派发与认证IPC实现（2026-10-19）：新增HttpMcpDispatch/0053（第69表）、DurableHttpMcpCredentialBroker与固定Unix socket客户端/服务端。可信网关保存前复检上游资源许可及平台当前身份/grant/固定版本/模型凭据/目录/来源/attempt/租约和R0/R1范围，同invocation只一份不可替换范围。先独立提交claimed再取密，一次nonce只绑定一份非秘密token元数据，未知永久保留领取且无重取/明文缓存；原lease首次真实撤销单向保存。10003 broker/10004执行器经SO_PEERCRED双向核对、固定目录/socket权限、32KiB封闭nonce命令、最多4并发无重连；执行器经IPC取密/当前查询/撤销/审计并接入原HTTP调用，客户端不导入DB。原派发表与审计留在broker侧，token只在认证socket/内存传递；无真实上游/注册/部署时仍拒绝。PG/ORM保护历史、保留预览和源码摘要已纳入；取密等待提高至6秒，15秒主预算不变。实际provider/签名或内省、独立HTTP运行部署/OS网络隔离及产品网关注册仍待，8.7/20.5保持未完成、165/302；未写或运行测试、类型检查、构建、迁移、容器、取密或网络验收。
  - HTTP与数据库审计接线（2026-10-19）：新增mcp_http_client，使用固定1.30.0 SDK协议类型及现有依赖JSON Schema校验，限定无会话JSON响应模式；初始化/通知/单页目录/一次工具调用最多四个固定POST。调用前冻结参数/Schema并匹配现有租户来源别名、描述及新增arguments_digest，策略新增固定server_name/server_version/transport_profile并进入请求摘要。每次交换前后复检许可，出站DNS完成后再次复检，固定HTTPS目标/IP/TLS、无代理/重定向/重连/写重试；SSE/会话头/分页/未知能力或协议直接拒绝，不虚构通用Streamable HTTP兼容。严格重复JSON/有限数/16层4096节点、32KiB参数/128KiB响应/64KiB结构化结果，Schema不解析引用；输出Schema/文本镜像核对、共同脱敏和本次令牌遮蔽后再校验，返回不可信观察，不授予业务成功或执行许可。新增DatabaseHttpMcpAuditSink复用AgentAuditEvent与专属mcp_authorization_event，独立2秒事务/PG语句1秒锁250ms，仅查原tenant/actor/Run/attempt/generation/invocation/工具参数/来源历史绑定后追加最小元数据；失败/过期仍可审计、不赋当前许可，提交不明不重发。写入器只供应用侧可信审计broker，隔离执行器不能拿DB工厂，认证IPC仍待。现有VARCHAR非原生审计枚举无需DDL，68表/0052不变，新事件须新API/Worker同步支持。真实服务/凭据broker、签名/内省与持久化一次派发、认证IPC/独立HTTP执行身份仍待，默认提供者None且没有产品网络入口；整项保持未完成。未写/执行测试、类型检查、构建、迁移、取密、数据库或网络请求，整体165/302。
  - 授权编排实现（2026-10-19）：新增mcp_http_authorization_contracts/authorization，固定2025-11-25、可信HTTPS issuer/resource、租户/主体/来源修订与策略、调用/attempt/generation/dispatch、凭据provider/ID/revision及精确scope，范围摘要不能自行授予许可。只从显式可信broker获取SecretStr，核对单一受众、完整scope、原凭据revision、令牌摘要/期限/撤销状态；额外拒绝平台issuer/audience的JWT，未验签claims只用于拒绝，不当作认证。broker须独立验证签名/内省、当前主体/资源/审批及持久化一次派发；默认broker与审计sink均None，缺失直接拒绝，不提供本地假批准或平台JWT回退。调用前后及审计等待后均复检，令牌最多300秒且不超批准/调用截止，一次取密/一次可信适配器回调，无刷新/重试/401发现或scope升级；进入回调后的失败保留call_started/未确认，不当作未执行。撤销只针对原lease，重新读取固定范围及不可变元数据/真实撤销时间，过期仍可走独立撤销授权，未知只报未确认。最小审计隐藏令牌/目标/scope原文，固定错误码；调用主工作15秒、撤销主工作10秒，失败审计另限2秒，子步骤2/3秒；等待预算不承诺驱动清理硬截止。源码纳入后续发布摘要，无新依赖/表/迁移/API；实际HTTP MCP会话、首个获准服务/issuer/scope、broker/provider/审计持久化及认证IPC/独立执行器接线仍待，未接入现有Agent执行链。未写/运行测试、类型检查、构建、迁移或实际取密/网络；8.7/20.5保持未完成，整体165/302。
- [x] 8.8 校验 DNS/地址/重定向/出站域名，防止模型控制未授权 URL 或访问私网。
  - 实现证据：outbound_http提供固定服务端URL/精确IDNA域名和端口策略，空域名集合拒绝；HTTPS/无userinfo/query/fragment/控制字符，全部DNS结果逐个检查并固定单个获准IP、原主机Host/SNI/TLS校验，禁代理/环境CA/重定向/压缩/隐式重试。默认公网、私网只逐个批准RFC1918/ULA地址，回环/link-local/保留/组播/未指定始终拒绝；可选确切IP集合进一步收窄。DNS使用无应用环境的可终止标准库子进程，有限结果和整体超时；请求/响应大小、方法/状态/认证头受限，错误仅固定码。OTLP维护发送已接入并保留其严格IP配置及错误域，未实际联网；原模型SDK专属网络策略不改，真实API/MCP接线/身份/OS隔离和8.11验收仍待，helper不构成业务许可、沙箱或未知写结果重试许可。无新增依赖/迁移/测试，未运行构建或网络验收。
- [x] 8.9 实现响应白名单/脱敏/截断，阻止秘密、原始敏感日志与注入文本扩权。
  - 实现证据：tool_result_services把全部已注册工具返回收敛为封闭Schema/严格JSON类型/额外字段拒绝，身份摘要有独立输出Schema；重复键、NaN/Infinity/溢出非有限数、非对象、深度>16/节点>4096、无效UTF-8及运输/领域状态不一致拒绝，未知工具无Schema默认拒绝。全部声明文本字段统一递归脱敏并复检Schema，网关额外精确遮蔽本轮模型密钥/JWT/密封主密钥，秘密集合只在可信进程内使用。共用redaction完整处理认证头/Bearer/Basic/Cookie/带转义引号的秘密字段/多行PEM，修复旧Authorization仅遮掉Bearer而残留令牌的路径。来源ID/digest/key若含秘密拒绝整份结果，不改定位；知识新增可空content_is_redacted，本次脱敏与上游预算excerpt区分，原分块digest不改，旧未记录保持未知。移除网关重复的字节截断/脱敏分支；大小超限整份拒绝，不截断JSON/删引用后伪称完整，异常不附原输入链，SDK额外元数据/artifact仍剥离。原model_tool_data_v1信封/当前来源授权/冲突与账本摘要复检保留，输出不授予权限，不声称通用语义注入检测。真实API/MCP仍须获批固定Schema及适配器，8.4–8.7/8.10/8.11未据此完成；测试/构建/迁移均未执行。
- [x] 8.10 增加连接健康测试、熔断/恢复、启停和版本变化的管理接口与 UI；不把 Registry 放进执行链。
  - 隔离Mock范围实现完成（2026-10-19）：0052新增一次派发探测任务及追加式连接事件；管理员API提供状态、人工熔断、提交探测/恢复、固定任务查询与取消，Worker在独立事务提交领取后使用固定Unix socket派发，服务端/客户端核对SO_PEERCRED，受限进程不接DB/Redis/模型或业务凭据。只有固定当前来源/策略/会话/代次、未过期owner/generation/lease的结果可确认；失败最多累计3次后熔断，冷却30秒后显式半开探测，只有新结果通过关闭熔断，观察最多60秒且不超批准期限，源版本变化/过期为unknown/stale。人工熔断/取消围栏旧任务，旧结果不恢复，未知不重试；熔断期间固定描述解析拒绝。管理页提供独立隔离观察/熔断状态、显式确认探测/恢复/取消、当前任务及未知结果只查询，原来源启停与版本失效流程保留。默认SUPPORTOPS_TOOL_CONNECTION_MOCK_ENABLED=false且仅隔离local/dev/test可开，独立10002运行身份仅共享socket卷给10001 Worker。68表/0052；业务执行始终关闭，Registry不进入执行链，真实HTTP受众/凭据与业务连接器仍归8.4/8.7/W20。按用户本轮再次确认，只完成实现，不运行测试/类型检查/构建/迁移/容器或协议验收；8.5/8.11及真实验收未完成，整体165/302。
  - 管理界面实现：同租户当前来源SQL count/稳定分页API返回完整批准策略和独立connection_status=not_connected；Web /agent-management/tool-sources及管理员导航、来源新建/编辑批准/启停、手工目录失效确认、当前修订/完整批准集合/固定工具定义抽屉已接通。页面按服务器修订做并发控制，未知契约禁写、当前token/请求序号隔离迟到响应，写入未知只查询原来源、不自动重发；目录ready不当作连接健康。连接控制与隔离范围见上段；真实业务连接及运行验收仍待。
- [ ] 8.11 为本地/API/MCP 三种适配添加一致的授权拒绝、限额、异常、注入和停用契约测试。

## 9. W09 证据上下文与完整运行预算

工程优先级：P0；产品阶段：产品 P0 基础；模块：M09/M10/M12。

依赖：W06、W07。规格：[context-and-budget-control](specs/context-and-budget-control/spec.md)。覆盖：G17、G18。

完成标准：上下文有边界，完整输入及重试消耗受 Run/租户累计预算约束。

- [x] 9.1 定义 ContextSnapshot 的当前问题、确认事实、证据来源/时间、未完成事项与历史边界。
  - 实现证据：严格版本 1 ContextSnapshot；当前输入 ID/问题摘要/input_sequence/历史 ID 与省略数量、确认事实（必须绑定证据）、证据时间/冲突/未完成事项；不把模型摘要自动标成确认事实，保存在 attempt.context_snapshot。
- [ ] 9.2 按固定模型能力计量系统 Prompt、历史、工具 Schema/结果、知识证据和输出预留，验证 tokenizer/保守估算策略。
  - 已实现部分：逐调用完整文本/Schema/系统/结构化结果 UTF-8 字节保守估算，协议开销及输出预留、模型窗口门禁；未知多模态拒绝。真实 tokenizer/供应商策略验证按测试暂缓要求未执行，整项保持未完成。
- [x] 9.3 实现按 Token 预算的历史裁剪与摘要，保留关键事实/当前输入/引用定位，避免摘要丢掉授权边界。
  - 实现证据：history_context_services/ContextSnapshot 的 extractive_history_v1 以 message/role/sequence/digest 保存来源，最多 12 条脱敏用户摘录/已有结果 summary/答案片段、旧 evidence 定位/冲突/未完成标记。SQL 有限旧轮次+首轮最多两条，当前输入缺失拒绝；消息 ID 进入 checkpoint 图输入。fit_model_context 先完整计量和裁完整旧轮次，再在剩余预算内尝试首个问题/最近摘要 400/160/64 字符，不容纳时省略，当前问题/本轮工具轨迹不丢。历史作为 HumanMessage 不可信 JSON，不升级为确认事实或本轮引用；调用前后/正文提交/最终落库按当前 ACL/诊断/动作复检，损坏/失效来源不退回旧正文，历史与本轮证据统一文档锁序。无额外总结模型/长期记忆，摘录可能不完整；测试/类型检查/构建/性能验收未执行。
- [x] 9.4 实现工具结果大小与字段限制、不可信数据包装、证据有效期和冲突标记。
  - 实现证据：tool_result_services为知识结果/证据、诊断、工单查询、动作提案及身份摘要声明受控输出字段；拒绝未知字段/未登记Schema、超限/无效数值/来源错配/状态与事实不一致。保留知识片段空白，逐字段脱敏后完整序列化，不截断JSON；丢弃工具任意artifact/响应元数据。模型边界增加model_tool_data_v1信封，绑定调用ID/实现/目录/接收时间、不可信data/无执行许可及detected/not_assessed冲突状态，内部诊断保留领域契约，知识预留信封预算。tool_context_services逐模型请求以同租户/Run/attempt/call_id和账本结果摘要核对实际ToolMessage（用户同形JSON不算来源，结构化回答重试单独识别），将已用来源并集持久化至ContextSnapshot，裁剪/重试不移除。模型前后、正文/最终提交复查目录/绑定/角色/实现/固定配置摘要、Mock启用/本人提案及工单当前授权/版本；动态观察按原观察时间最多五分钟，不按重连接收延寿，失效以TOOL_CONTEXT_AUTHORIZATION_CHANGED停止。已声明Mock同请求公共标量冲突追加TOOL_SOURCE_CONFLICT/未完成标记，模型不能择一确认；固定诊断与知识冲突降级继续保留，不声称通用语义识别。源码/Prompt清单同步，不增表，旧无来源信封工具消息不重建为可信事实；测试/类型检查/构建/性能验收未运行。
- [x] 9.5 建立 Run/attempt/tenant 预算账本、并发预留和真实 usage 结算，覆盖重试和取消释放。
  - 实现证据：ModelCallLedger + `20261013_0023`；Run/租户锁原子计数/费用预留，真实 usage 释放未消费预留；出站失败/取消/崩溃不能证明未消费时保留 unknown 预留，迟到 usage 可补结算但不能写产品状态。迁移/测试未执行。
- [x] 9.6 定义未知定价/usage、缓存 Token、推理 Token 与未结束流的预算策略，不以零值绕过。
  - 实现证据：可信 USD 定价才启用硬预算，其他币种/缺失定价拒绝；缺 usage 保留预留，缓存/推理作为输入/输出子集不重复累加，不假造折扣；Run 合计缺任一用量为 null。
- [x] 9.7 在每次模型/工具调用前执行累计费用/次数/时长门禁，耗尽时提供安全退出或升级。
  - 实现证据：模型逐物理请求预留，SDK 隐式重试关闭并改为受账本限制的有限重试；工具前检查当前 Run/租户累计费用、原子次数和跨恢复时长，耗尽以稳定说明安全结束，不伪称已升级或完成动作。真实验收暂缓。
- [x] 9.8 恢复 attempt 保留已消费预算，禁止 checkpoint 历史重复计费或恢复时重置额度。
  - 实现证据：许可关联整个 Run，跨 attempt 共用，失效调用保持 unknown；仅实际新增请求建账本，产品合计取物理账本，Trace 父 attempt 独立合计，子 span 不复制整个 Run 成本。
- [x] 9.9 同步管理配置可用性/边界提示与费用退出错误，旧配置保持兼容。
  - 实现证据：Agent 完整输入/累计 USD 配置与 change 校验，窗口包含输出预留；保存不再清空旧预算/审批/单工具额度；稳定失败说明、Trace 账本合计及管理员分页物理请求接口。UI 验收暂缓。
- [ ] 9.10 增加长对话、大工具输出、并行预算竞争、未知 usage、重试及恢复计量测试。

## 10. W10 结构化产品结果与聊天恢复

工程优先级：P1；产品阶段：产品 P0 可信回答；模块：M01/M08/M09/M12。

依赖：W01、W06、W07。规格：[structured-support-results](specs/structured-support-results/spec.md)。覆盖：G19。

完成标准：刷新后完整结果可重建，建议动作和执行事实分离。

- [x] 10.1 设计版本化 SupportResult 与 Message/Run 关联，明确 answer/summary/citations/actions/escalation 的存储契约。
  - 实现证据：`support_result_services.SupportResult` 版本 1，绑定 run/attempt；引用只存获准 evidence，动作状态 proposed，升级状态 recommended 不伪称执行。
- [x] 10.2 新增迁移与旧正文兼容读取，不回填虚构引用、工具结果或已执行动作。
  - 实现证据：`20261013_0022` 为 Message 增加 nullable JSON；旧正文保持 null。迁移未执行。
- [x] 10.3 在最终答案校验/产品事务中保存完整结果，引用记录保留安全 evidence ID 而非未验证 URL。
  - 实现证据：Worker 最终产品事务校验本 Run/attempt 证据、当前权限/版本与摘要，URL/自造证据拒绝提交；完整结果与助手消息同事务保存。
- [x] 10.4 增加结果就绪事件及同一快照读取，保持现有正文 delta/replace 与游标连续性。
  - 实现证据：`result.ready` 只含消息 ID/版本，不传播历史证据正文；会话快照复用 Run 读锁并返回完整结果，正文事件兼容。
- [x] 10.5 实现证据/升级/动作详情查询的当前主体授权与隐藏策略。
  - 实现证据：核对 runs.evidence、actions 详情/回执/升级包与 W15/W16/W17 已接通服务，租户/本人/来源 Run/当前工具与业务资源范围、SQL 权限先于分页；升级包列表仅元数据，详情重新投影来源与双方知识 ACL。project_escalation_package 补服务入口跨租户提前拒绝及 payload Run/attempt 与记录一致性；失效来源隐藏事实/候选/引用并不传播正文，客服独立 ticket_handle + support_context_read 不扩大原主体范围，隐藏 ID 固定 404。待接入范围仍仅隔离 Mock，真实连接器/测试验收未完成。
- [x] 10.6 同步 api.ts/types.ts 与聊天引用、建议动作和升级状态展示，建议不标成执行成功。
  - 实现证据：`MessageSupportResult` 引用抽屉每次重读当前权限，纯文本展示原文/章节/时间/摘要，失效引用隐藏元数据，建议和升级明确尚未执行。测试验收暂缓。
  - 展示来源补齐（2026-10-18）：证据选择绑定SupportResult对象，结果更新时当前渲染即隐藏旧正文，随后清理状态/作废旧请求，关闭动画也不留旧内容；升级包列表/详情绑定动作-Run或工单scope，切换不显示旧数据。引用/两升级详情fetch显式no-store，无新轮询/权限推送/接口字段，未写或运行测试、类型检查、构建或浏览器验收，10.7/10.8与17.7保留。
- [ ] 10.7 测试结构校验重试、流中取消、刷新、断线续传、旧消息和迟到终态。
- [ ] 10.8 完成加载/缺失引用/无权限/错误/窄屏/键盘访问验收。

## 11. W11 知识生命周期剩余验收与来源治理

工程优先级：P1；产品阶段：产品 P0 知识基础；模块：M01/M07/M12。

依赖：W00、W01。规格：[knowledge-source-ingestion](specs/knowledge-source-ingestion/spec.md)、[knowledge-retrieval-and-evidence](specs/knowledge-retrieval-and-evidence/spec.md)。覆盖：G20。

完成标准：旧 4 项待办有独立验收证据，版本/ACL/并发/审计事实可信。

- [ ] 11.1 承接旧 change 5.2：补跨租户、角色/用户/部门 ACL 组合、管理员正文限制与拒绝审计脱敏。
  - 显式读取与拒绝审计补齐（2026-10-18）：knowledge_access_services为GET snapshot和本人Run evidence提供5秒事务/PG语句4秒/锁1秒边界，在共同文档锁下完成当前ACL/有效发布和响应投影；数据库失败/超时用既有KNOWLEDGE_CONFLICT，不冒充权限拒绝。read_knowledge_evidence在ACL前改为快照四列定位，不先加载标题/章节锚点（快照本身不存正文）；实际分块/标题SQL复用_acl_filter及当前租户/文档/版本/有效期限制，内部lock=False仍由原调用方管理事务。get_published_content/get_effective_snapshot先拒绝主体租户不符，再取文档锁复检ACL，正文版本额外绑定document_id。成功读取响应no-store，知识专属403/引用404no-store，隐藏404原契约不变。两个已认证显式入口拒绝后先结束原事务，独立2秒/PG语句1秒/锁250ms尝试写既有KNOWLEDGE_ACCESS_DENIED；knowledge_access_denial_v1仅固定操作/not_accessible/requested_only及tenant域内目标/Run/关联值SHA-256，无原始目标ID/关联值/标题/正文/查询/令牌/异常，不查询另一租户或推断存在。actor/tenant用不可变principal，避免回滚后读取过期User；关联摘要64位满足原字段上限。失败仅固定告警且不覆盖原拒绝/提交原事务/重试，不承诺必达、覆盖身份前401或全部审核/工具拒绝。新源码纳入发布清单并明确暂存，无新依赖/表/迁移/公开字段/路由或错误码，65表/0048不变；API/模块/安全/设计30节/规格同步。仅源码/调用方/差异核对，未写/运行测试、类型检查、构建、数据库或权限/并发/性能验收，11.1及163/302保留。
- [ ] 11.2 承接旧 change 5.3 的生命周期范围：验证并发发布、单当前版本、重复事件和产品事务恢复；索引失败验收交由 W12。
- [x] 11.3 校验发布、取代、失效、归档、ACL 变化与到期产生的索引失效事实，补齐遗漏的知识 Outbox 事件。
  - 实现证据：生命周期同文档锁/同事务追加 Outbox；维护主动刷新到期状态并产生事件，修复发布 payload 在 snapshot ID 尚未生成时序列化的问题。实际故障/并发验收暂缓。
- [x] 11.4 将审核提交与实际审核决策分离，明确审核人/发布权限/自审策略、修改后复审与审计字段。
  - 实现证据：submitted_by/at 与 reviewed_by/at、pending/approved/rejected 分开；review_revision/content_digest 并发复检，文档锁串行创建/送审/决策/发布；创建人或提交人禁止自审。领域层复检当前管理员，批准绑定内容/标题/有效期/ACL，发布复核范围与审核人当前权限；新版本须重新审核，ACL 变化可显式重新送审。新增独立 review API/决策弹窗和审计摘要；0026 将旧 in_review 的原字段还原为提交事实，不回填批准。迁移/测试未执行。
- [ ] 11.5 验证乐观 revision、内容不变性、回滚生成新版本、生效区间及 Mock production 拒绝。
- [ ] 11.6 承接旧 change 5.5：实现真实浏览器知识创建/版本/ACL/审核/发布/归档主路径和异常路径。
- [ ] 11.7 验收知识详情窄屏、键盘焦点、正文渲染消毒、禁止外链或脚本扩权与无权限状态。
- [ ] 11.8 承接旧 change 5.6：执行受影响格式/类型/测试/构建、隔离迁移与 OpenSpec 校验，记录实际结果。
- [ ] 11.9 提供旧任务验收证据链接与完成建议，只有实现验证通过才在其原 change 更新状态。

## 12. W12 知识分块、关键词索引与索引消费

工程优先级：P1；产品阶段：产品 P0 知识检索；模块：M07/M10/M12。

依赖：W04、W11。规格：[knowledge-retrieval-and-evidence](specs/knowledge-retrieval-and-evidence/spec.md)。覆盖：G21。

完成标准：索引事件可恢复，版本/ACL/失效变化不产生过期可检索正文。

当前用户选择（2026-10-13）：先实现关键词检索、权限过滤和证据链；暂不启用向量检索。12.1、12.5 中 embedding 能力及向量相关验收保持待处理，不以关键词实现代替勾选。

- [ ] 12.1 确认 embedding 模型版本、维度、数据边界与费用，先验证既有 pgvector 能力，新增依赖需论证。
- [x] 12.2 设计 KnowledgeChunk/IndexGeneration/Outbox 索引任务状态与快照、段落锚点、digest 关联，Embedding 扩展暂缓。
  - 实现证据：索引代次/分块/证据三个模型，任务复用独立知识 Outbox；keyword_v1 明确不表示向量能力。
- [x] 12.3 添加关键词/过滤索引迁移与老文档重建任务，未索引/失败状态不能伪装已就绪；向量索引另待启用。
  - 实现证据：`20261013_0021` 新表/过滤索引/死信字段，为旧当前快照排重建事件，未构建前无 ready generation。未执行迁移。
- [x] 12.4 实现确定性分块与段落定位，记录算法版本、内容摘要与可复现 chunk ID。
  - 实现证据：规范化 Markdown 章节/字符偏移、1600 字符上限、原文摘要校验及快照/算法/偏移/digest 的确定性 chunk_key。
- [ ] 12.5 实现有限批次 embedding/索引调用与累计预算，处理超限、失败和重复请求。
- [x] 12.6 实现知识 Outbox 分批领取、幂等消费、有限重试/死信与恢复，不与 Run Outbox 混淆。
  - 实现证据：Worker 独立定时消费，文档锁→事件锁、skip_locked/事务复查/保存点、指数退避/最大次数/稳定错误码；恢复创建新有限任务，保留原死信与次数。
- [x] 12.7 处理取代、失效、归档、ACL 撤销和到期的索引无效化及缓存失效。
  - 实现证据：乱序事件按当前版本收敛，维护失效旧 generation；不缓存正文/权限，所有候选和引用直接查询当前 ACL/有效期，维护延迟不能使过期资料合法。
- [x] 12.8 实现重建/重放/切换有效 generation，错误新索引不得破坏旧获准数据的可追溯性。
  - 实现证据：文档锁串行构建，新代次和分块同事务成功后切换旧代次；保存点回滚失败构建，旧分块不删除，历史 evidence 仍可追溯并需实时复查。
- [x] 12.9 增加管理员索引状态、失败原因、重试与重建入口，前端保留加载/错误/权限态。
  - 实现证据：租户管理员分页 index-jobs、文档索引聚合、重建/死信恢复接口；知识详情独立 hook/索引面板，旧任务不清零，expected_attempts 防止陈旧恢复。UI 验收暂缓。
- [ ] 12.10 承接旧知识 5.3 的索引范围：测试重复事件、embedding 失败、乱序失效、重启和事务边界。

## 13. W13 ACL 检索、证据组织与引用校验

工程优先级：P1；产品阶段：产品 P0 可信知识回答；模块：M07/M09/M01。

依赖：W07、W08、W09、W10、W12。规格：[knowledge-retrieval-and-evidence](specs/knowledge-retrieval-and-evidence/spec.md)、[structured-support-results](specs/structured-support-results/spec.md)。覆盖：G22。

完成标准：只授权有效证据进入模型，引用可定位且不足时追问或升级。

- [x] 13.1 实现内部检索服务与版本化查询契约，先按 tenant/主体/有效版本限制候选。
  - 实现证据：keyword_v1，英文/数字词项和中文二元组、词项/结果数上限、通配符转义，SQL 同时限制租户/当前版本/有效期/ACL，再读取正文；分数明确为关键词命中。
- [x] 13.2 实现候选正文读取前 ACL/有效期复检，权限缓存按主体/策略/版本隔离并支持撤销。
  - 实现证据：候选 ID 阶段 SQL ACL，正文阶段文档锁后实时复检；工具返回、结果提交、用户展开时均检查当前权限，不启用权限/正文缓存。
  - 读取链路补齐（2026-10-18）：正文SQL重新取读取时间，复检全部租户/文档版本关联/发布指针/有效期/ACL和索引-快照-版本摘要，仅加载命中分块及引用元数据，不再为版本号加载整篇Markdown；引用展开同样校验版本/快照摘要。只做源码核对，未执行权限/到期竞争或检索验收。
- [x] 13.3 提供受控 search_knowledge 工具并通过统一网关接入 Agent，而非开放客户端任意快照读取。
  - 实现证据：平台注册/固定绑定/严格输入 Schema/网关许可后内部调用；可信身份由数据库重建，普通工具函数直接调用拒绝。历史 Agent 需显式重新发布绑定该工具。
- [x] 13.4 固定 evidence/snapshot/version/section/检索时间与分数，生成 ContextSnapshot 可消费证据。
  - 实现证据：证据绑定成功 ToolInvocation/user/Run/attempt/snapshot/chunk/digest/查询摘要/检索时间/关键词分数；网关返回前授权成功后才将证据加入 attempt ContextSnapshot，不将失败调用视为已送模型的证据。
- [x] 13.5 实现引用 ID/段落/权限/有效性校验，拒绝模型自造 URL 或未经检索的出处。
  - 实现证据：只接受本 Run/attempt 检索产生的 ID，正文摘要及当前版本/ACL 复检；标题/章节来自服务器，模型 URL 和伪 ID 以稳定错误码拒绝产品提交。
- [x] 13.6 将合法引用保存产品结果并支持刷新、展开定位与失效/撤权后的安全展示。
  - 实现证据：消息完整结果、当前权限投影与 evidence 详情 API/聊天抽屉，旧版本取代或撤权只返回 unavailable，不泄露引用标题/快照/摘要。测试验收暂缓。
- [x] 13.7 实现无结果、过期、冲突、检索超时的追问/降级/升级与有限重试。
  - 实现证据：关键词查询独立超时，返回受限 unavailable/稳定原因，重试受单工具和 Run/模型额度限制；无有效证据由服务端替换为未确认/追问/升级意向，过期/撤权拒绝继续输出。模型标记冲突必须关联不同且获准的本次证据，服务器替换为待人工复核，不自选根因或执行建议；保存/刷新后撤权隐藏冲突原因。语义冲突识别正确性依赖后续评测，不宣称正式验收已通过。
- [ ] 13.8 评测标准检索基线，再按收益启用混合搜索/重排/查询拆解，记录策略版本与费用。
- [ ] 13.9 增加跨租户相似文档、缓存撤权、Prompt Injection、伪引用与过期索引的安全测试。
- [ ] 13.10 以固定评测样本比较检索正确性、引用覆盖与上下文成本，未过门禁不发布策略。

## 14. W14 429 诊断编排与只读业务工具

工程优先级：P1；产品阶段：产品 P0 Mock；P1 真实诊断；模块：M06/M09/M10/M01。

依赖：W08、W09、W13。规格：[diagnostic-support-workflows](specs/diagnostic-support-workflows/spec.md)。覆盖：G24。

完成标准：429 场景以证据闭环诊断，缺信息/依赖失败有明确退路。

- [x] 14.1 把 S01–S06 等 429 基线映射为分类、信息补齐、查询、规则、判断与升级节点，固定循环/步骤上限。
  - 实现证据：`diagnostic_workflow` 固定十个节点、最多四个业务查询和一个关键词查询，不调用模型猜测根因，不建立无限循环；普通问题继续既有 Agent 图。只在隔离 Mock 开关下生效，生产拒绝。
- [x] 14.2 实现请求 ID、受限时间、模型/区域、用户/项目上下文校验，不请求完整密钥或敏感 Prompt。
  - 实现证据：严格 DiagnosticQuery、带时区且 <=15 分钟/成对时间窗口、模型/区域过滤；请求 ID 必须出现在当前主体的有限用户历史中，模型自造 ID 不采纳；只接受本人及服务器生成的隔离项目范围，不请求完整密钥或 Prompt。真实范围待 W14.10。
- [x] 14.3 实现隔离 Mock 请求日志查询工具，返回受限字段、时间戳与结构化错误。
  - 实现证据：受控 get_request_logs 通过统一网关返回 S01–S06 固定字段、429 分类、计数/Token 预占及时间；无完整日志/Prompt/密钥，直接调用工具函数拒绝，默认 flag 关闭。
- [x] 14.4 实现隔离 Mock 用户/项目配额与账户摘要工具，区分 RPM/TPM/并发及账户限制。
  - 实现证据：get_current_user_profile 返回本人隔离项目的限额/用量、账户状态、现金/赠送/冻结与套餐摘要；只读且不执行额度/资金变更，输出和最终产品结果明确 mock。
- [x] 14.5 实现隔离 Mock 模型路由状态与公开/租户可见故障事件工具，总体保持 4–6 个必要只读能力。
  - 实现证据：get_model_route_status/list_active_incidents 只返回模拟公开字段与受限事件；4 个业务查询加 search_knowledge 为 5 个核心只读能力。Agent 显式绑定；production/关闭 flag 拒绝，角色/资源/版本/额度仍走网关。
- [x] 14.6 为每个 Mock 工具配置正常、空结果、超时、权限拒绝与证据冲突固定结果。
  - 实现证据：已定义六个固定 request_id；当前用户输入的 [mock:empty/timeout/denied/conflict] 控制隔离结果，模型参数不能任意更改模式。这里是产品演示数据与错误契约，未执行测试/故障演练。完整诊断编排与事实判断等待后续任务。
- [x] 14.7 实现诊断事实集、根因候选与已确认/未确认标记，结合合法知识引用形成回答。
  - 实现证据：白名单标量事实绑定 ToolInvocation/实现/目录版本/摘要/查询时间；RPM、输入加输出预占 TPM、并发、套餐、上游关联按固定规则判断。只有唯一且跨源一致候选与当前获准 `supportops_429_rule_v1:<code>` 知识声明一致才 confirmed_mock；提交再次复检事实与引用，不把模拟数据当真实生产证明。迁移 0025 未执行。
- [x] 14.8 实现缺请求 ID 追问、矛盾证据拒绝确定判断、依赖失败降级与结构化升级包。
  - 实现证据：缺失/多个 ID 先追问；冲突/未知/空/超时/权限拒绝/查询额度耗尽保持未确认，接管包含来源引用、已观察事实数、实际完成步骤和未确认项，升级仅 recommended，不伪称建单。
- [x] 14.9 记录诊断阶段/SSE 进度与排查结果，刷新恢复不重做已完成查询或丢阶段。
  - 实现证据：阶段与报告保存 attempt ContextSnapshot，SSE 只发节点元数据，会话一致快照携带节点，结果保存完整报告；详情每次重读当前授权。恢复只复用同 Run/参数/版本/策略、五分钟内且成功的只读事实，原调用消费不清零，失败或未知不复用；知识引用由新 attempt 重新检索。浏览器和崩溃验收按约定暂缓。
- [ ] 14.10 真实接入前确定日志/配额/路由/故障 API 与租户字段范围，在沙箱完成各连接器契约验收。
- [ ] 14.11 验收正常限流、账户限制、上游事故、未知原因与预算耗尽路径，确保不伪造根因。

## 15. W15 业务动作账本、幂等、复核与对账

工程优先级：P1；产品阶段：P0 低风险 Mock；P1 完整动作；模块：M06/M08/M10/M12。

依赖：W05、W07、W08。规格：[business-action-reconciliation](specs/business-action-reconciliation/spec.md)。覆盖：G26。

完成标准：R2 Mock 动作可幂等复核；未知结果先对账；R4 未获批准仍拒绝。

- [x] 15.1 定义 BusinessAction/ActionAttempt/Receipt 的逻辑键、参数哈希、版本、风险、主体与结果状态。
  - 实现证据：三种 ORM 实体分开逻辑动作、execute/reconcile/verify 许可与追加回执，固定参数 Schema/摘要、Agent/工具/目录版本、主体/资源与 R2–R4。业务键由可信主体/资源/issue 意图组成，不含 Run/attempt/模型调用 ID；参数只允许 MockTicketParameters 白名单且拒绝疑似凭据。尚未放行执行入口。
- [x] 15.2 增加业务幂等唯一约束、外部 ID/回执关联和状态迁移，保留 Run/attempt 独立语义。
  - 实现证据：0029 为 tenant/tool/logical_key 与 action/generation 增加唯一约束、外部 ID/回执/current attempt 关联；状态函数锁动作并复核 generation/冻结参数，不改 Run 终态。成功须当前有效 verify/reconcile 的 verified 回执，未知重排须 not_executed 独立证明；审批未接通仍禁止要求审批的动作排队。迁移与测试未执行，执行/对账/产品入口继续后续任务。
- [x] 15.3 实现动作提案与冻结参数；建议动作、可执行提案、已执行结果在 API 中分开。
  - 实现证据：create_mock_ticket 只生成提案，模型参数为空，主体/资源/问题键/最小摘要由可信 Run 派生；静态最低 R2，目录不能降低。工具网关只放行提案用途，不执行业务写入；API 与聊天分别展示建议、冻结提案/意图和已复核状态。默认 business_action_mock_enabled=false，production 拒绝；需要审批的冻结动作仍不能排队。
- [x] 15.4 实现原子创建执行意图与幂等冲突拒绝，模型重试/Run 恢复复用同一逻辑动作键。
  - 实现证据：tenant/tool/可信主体资源/issue 业务键加唯一约束、保存点和冻结摘要冲突拒绝；有请求 ID 用请求意图，无标识用会话排查意图，不含模型调用/Run/attempt ID。重复提案在 Run 锁下关联同一动作事件；新的已完成且获准 Run 可作为执行上下文，不覆盖原出处/参数。执行意图事务复检参数/generation/当前策略与 R2 门禁，保留原动作 ID，unknown 不重排；实际执行/对账待 15.5–15.9，迁移与测试未执行。
- [x] 15.5 实现 R2 Mock 连接器的执行/重复返回路径，需审批动作在 W16 完成前保持拒绝。
  - 实现证据：隔离目标操作/工单表与独立目标事务，稳定业务键和冻结摘要去重，重复返回同一 MOCK 编号。Worker 独立动作消费者领取 owner/generation/lease，执行前再次复检当前主体/角色/资源/固定实现/策略与两次调用额度；R3/R4/需审批继续拒绝，production 禁止，0030 未执行。
- [x] 15.6 实现 response lost/timeout 的 unknown 状态与持久化对账调度，不立即使用新键重试。
  - 实现证据：目标提交与平台回执分事务；超时/响应丢失/执行租约到期保持 unknown，下一阶段只查询原业务键。执行许可/对账次数/回执持久化；旧 owner 不能补写成功，取消/停机不把可能发生副作用伪称未执行。
- [x] 15.7 实现外部查询证明成功、证明未执行或仍未知的三分支；仅第二种按策略同键重试。
  - 实现证据：Mock 目标显式封锁旧 generation 才返回 not_executed，普通查无记录不直接当作安全重试；独立 verified/not_executed/unknown/conflict 契约和摘要校验。仅已证明未执行且仍 R2/剩余额度允许时同键重排；这项目标端能力不能外推到真实系统，真实连接器仍待契约确认。
- [x] 15.8 为成功动作执行独立只读状态复核，HTTP 成功但业务不一致时记录冲突。
  - 实现证据：提交回执只标 submitted，独立 verify/reconcile 重新读取目标主体/项目/参数摘要/受限状态。只有当前有效查询尝试的 verified 回执可提交业务成功；模拟成功响应但存储不一致进入 needs_attention，不自动补偿。产品固定故障模式已提供，但未运行故障测试。
- [x] 15.9 实现有限对账、人工处置与受控补偿提案；补偿再次授权，不默认自动撤销。
  - 实现证据：既有有界对账/needs_attention/一次有限人工查询许可保留累计次数与默认人工总上限十次，不接受人工猜测回执。新增compensation_contracts/services与0046独立不可变ActionCompensationProposal，只在当前本人已完成关联Run、原动作冻结参数、当前generation独立verify/reconcile回执与同项目Mock目标均有效时生成关闭意向；unknown/submitted/无目标/已关闭拒绝。提案固定源回执/当前目标版本与参数摘要/当前目录/模型/凭据/配置/原与当前工具实现/本人范围，陈旧digest409，同范围幂等；新提案复检版本撤回，最低R3且原或当前策略R4不降低。原动作/批准/回执/目标状态不改，追加控制面和Run审计，历史/当前适用性分开。本人SQL分页/详情/readiness/显式保存API及聊天查看/确认/历史已接通。仅proposed，没有批准/排队/Worker执行入口，未来执行须新动作、新独立非本人审批与当前权限/目标复检，不能复用原建单批准或客服权限；不执行真实补偿/资金操作。新源码纳入manifest，保留预览计入本人提案关联；迁移/测试/类型检查/构建/浏览器未执行。
- [x] 15.10 增加动作列表/详情/回执/重试原因的权限接口与聊天/运行详情展示。
  - 实现证据：当前 Run 关联动作/本人回执标准数据库分页，主体/租户/当前 Agent/工具/资源过滤后 count/offset/limit；聊天独立状态/冻结参数/回执/刷新/人工查询许可，撤权隐藏详情，submitted 不标成功。运行详情增加分页控制面动作审计，仅 tenant 管理员读取状态/风险/次数/错误等元数据，不授予他人业务参数/外部编号/回执正文权限。浏览器验收暂缓。
- [ ] 15.11 故障注入覆盖执行前后崩溃、回执丢失、重复模型意图、并行同键及审计写入失败。

## 16. W16 持久化人工审批与恢复工作台

工程优先级：P1；产品阶段：产品 P1；仅隔离 R4 演练；模块：M01/M02/M03/M08/M09/M10。

依赖：W05、W07、W10、W15。规格：[durable-human-approval](specs/durable-human-approval/spec.md)。覆盖：G27。

完成标准：等待释放 Worker，非本人审批，改参/过期/撤权后不得执行，凭证一次性。

- [x] 16.1 定义 ApprovalRequest/Decision/Consumption 与 action/Run/tenant、工具版本、参数哈希、期限关联。
  - 实现证据：新增申请、追加决定、一次性消费三张表及 0031 迁移；冻结指定审批者、原动作/Run/主体/资源、实现/目录/Agent/模型/凭据 revision 和参数摘要，期限由服务器限定，数据库禁止本人审批。迁移与测试未执行。
- [x] 16.2 迁移 Run waiting_approval 与对应事件/终态分支，旧客户端状态兼容明确。
  - 实现证据：Run waiting_approval/waiting_action、0032 等待索引/说明、事件/API Enum/Web 状态与刷新恢复同步；保留旧终态，不回填历史批准，新状态启用要求同步升级客户端。审批等待/动作复核不计作活跃模型执行；后续同会话排队不消耗通知额度。迁移/测试未执行。
- [x] 16.3 实现审批人角色/资源范围和非本人规则，审批不能扩大申请者权限。
  - 实现证据：按用户纠正后的推荐方案，MockResourceGrant 显式绑定人员/隔离项目/approve 或 ticket_handle；当前有效员工类角色与显式授权同时成立，管理员无隐式业务权限。申请、决定及出站边界复查，指定非本人审批；执行始终用原申请者身份复检 Agent/工具/资源，授权不覆盖知识 ACL。测试未执行。
- [x] 16.4 实现 Runtime 持久化 interrupt/业务等待与 Worker 释放，重启后恢复待办。
  - 实现证据：模型回答/产品结果/attempt checkpoint 与业务等待同事务；唯一显式项目审批者时自动冻结申请，结束模型 attempt 并释放会话许可/Worker，Run 保持等待。批准后独立动作消费者继续，重启读取数据库待办，不重新推理或重复回答；人员不明确时保持未执行提案供用户选择。测试未执行。
- [x] 16.5 提供分页待办/历史/详情及批准/拒绝 API，记录可脱敏的理由和冻结参数摘要。
  - 实现证据：本人/指定人员资源过滤后 SQL 分页的待办/历史、重新授权的冻结详情、申请/批准/拒绝/取消接口；审批理由必填限长脱敏，事件仅保留摘要。分页指定审批人员和显式授权控制面、api.ts/types.ts、审批工作台/聊天申请入口已接入；浏览器验收暂缓。
- [x] 16.6 批准/拒绝与恢复通知采用事务 Outbox 和幂等事件键，重复决策拒绝或返回同一事实。
  - 实现证据：申请/action/决定/RunEvent 同事务；同申请唯一决定，同内容重放返回原事实，不同决定拒绝；动作账本 queued 行作为数据库轮询消费的事务 Outbox，不另发内存通知，不复用已结束模型 Run 的通知。决定/过期收敛 Run 等待阶段，重放不追加第二个执行意图。故障验收暂缓。
- [x] 16.7 恢复执行前校验当前主体/Agent/工具/凭据、参数哈希和审批有效期。
  - 实现证据：申请者当前身份/租户/Agent grant/工具撤销及固定实现、资源、参数、完整 scope 摘要、固定凭据 revision、指定审批者当前显式权限与期限，在决定/领取/出站复检。只作用于隔离 Mock，不支持真实 R4 操作；测试未执行。
- [x] 16.8 实现一次性消费与动作意图协调，数据库提交后失败仍使用动作幂等/对账。
  - 实现证据：ApprovalConsumption 的 request/action 双唯一约束，和 ActionAttempt/ToolInvocation 同事务保存；出站匹配 attempt/generation，消费后不再允许重执行（包括自愿审批 R2）。结果未知继续同键查询，未执行证明也不自动重放已消费批准。测试未执行。
- [x] 16.9 实现改参创建新审批、到期、撤销、工具紧急停用、会话删除和用户取消收敛。
  - 实现证据：申请者取消与 Run 等待取消/会话删除联动；有限治理轮询使到期/撤权/停用申请失效；改参 API/聊天弹窗仅改受限标题/摘要，要求当前项目非本人审批者，新 action 关联 supersedes_action_id，原参数/决定不覆盖，原批准失效且新意图必重新审批。原动作已有执行许可/一次消费/未知结果则拒绝换键；同一父提案与改参摘要重放复用同一新意图。0034 迁移/测试/浏览器验收未执行。
- [ ] 16.10 实现审批工作台和聊天等待、进度、拒绝/过期/取消反馈，覆盖窄屏、键盘与焦点。
  - 已实现部分：/approvals 待办/历史/筛选/重新授权详情/理由/决定/取消，管理员显式项目人员授权面板；聊天动作审批、等待审批/业务复核事件与刷新恢复，只列获准人员的搜索下拉；旧内容在权限报错时清除。窄屏/键盘/焦点验收暂缓，整项保持未完成。
- [ ] 16.11 用隔离 Mock R4 操作验证批准前无副作用、本人/越权拒绝和恢复后准确执行。
- [ ] 16.12 验证改参重审、凭证重放、并行批准、批准后撤权/过期以及执行中超时对账。
- [x] 16.13 记录审批 SLA、到期处理和客服升级流程，不连接真实退款/封禁接口。
  - 实现证据：README 的隔离审批说明记录默认一小时期限/配置范围、当前未承诺生产响应 SLA、有限轮询/停机恢复、显式人员授权与升级到 W17 的边界；等待/执行/未知状态分开，不连接真实 R4 接口。运行与测试暂缓。

## 17. W17 工单闭环、升级包与客服接管

工程优先级：P1；产品阶段：P0 Mock 建单；P1 真实工单全流程；模块：M01/M06/M08/M09/M10。

依赖：W14、W15。规格：[support-ticket-lifecycle](specs/support-ticket-lifecycle/spec.md)。覆盖：G28。

完成标准：证据不足形成完整升级包与可查工单，重复请求不重复建单，接管后动作受控。

- [x] 17.1 确认升级包字段、工单状态、租户/项目资源边界与动作幂等键，先固定 Mock 契约。
  - 实现证据：EscalationPackagePayload v1 固定脱敏问题/Run/attempt/Trace、观察事实、确认模拟原因/未确认候选、来源查询时间、步骤/引用/待处理事项；Mock 工单 open/in_progress/escalated/closed 与版本，主体派生项目及显式人员授权，动作按 tenant/actor/tool/resource/issue 稳定去重，升级包按 action/来源 Run/source_digest 去重并保留变化历史。真实连接器能力不外推。
- [x] 17.2 把 support_ticket_lookup 从占位转换为隔离 Mock 查询，未配置真实系统时明确不可用。
  - 实现证据：统一网关执行 v2，严格限长输入、Mock flag/production 门禁；SQL 在正文前过滤租户/本人/显式 ticket_handle 项目权限，返回前再复查；not_found 与越权隐藏存在，未启用明确 unavailable，不返回虚构业务数据。工具固定版本升级，旧 Agent 需显式重新发布，不静默切换；测试未执行。
- [x] 17.3 实现 create_ticket 的 R2 Mock 动作与独立读取复核，允许批准策略自动建单。
  - 实现证据：动作账本/稳定业务键/独立目标提交与 verified 查询证明复用 W15；新增默认关闭的 business_action_mock_auto_create_enabled，须同时启用非生产 Mock。固定诊断升级提案仍过网关许可，产品事务只为当前 R2/无需审批/成功提案提交 queued 意图；缺信息不自动建单，需审批仍进独立流程，额度失败不伪称成功。运行/迁移/测试未执行。
- [x] 17.4 将问题、确认/未确认事实、查询时间、已排查步骤、引用和 Run Trace 保存到升级包。
  - 实现证据：TicketEscalationPackage/0033 保存有界包与摘要，原动作和每次来源独立追加，缺失上下文不伪造事实，不把原始日志/历史 Prompt 复制到目标工单。本人升级摘要标准 SQL 分页、详情复检当前来源/证据，撤权隐藏问题与旧事实；聊天升级详情已接入。迁移/浏览器/测试未执行。
- [x] 17.5 将 429 无证据/冲突/依赖失败/预算耗尽分支接到幂等升级入口。
  - 实现证据：无证据/冲突/依赖失败沿用固定诊断同键提案；预算/上下文/调用/费用/时长耗尽终止时，在保存点内保存当前获准提案及升级来源，失败只追加 unavailable，不回滚 Run 终态。聊天失败后及刷新均提供独立升级入口，无需伪造完整 SupportResult、重跑模型或允许失败 Run 写动作/审批。当前用户输入共用严格请求 ID 解析，失败也使用稳定 issue 键；来源变化按有界 source_digest 追加不可覆盖快照，原样重放复用，保留预算原因/未完成事项。建单仍需新的有效授权上下文，未伪称预算耗尽后已建单；0038/浏览器/测试未执行。
- [x] 17.6 实现客服队列/认领/备注/回复/关闭或升级的接口和授权，明确接管后的 Agent 写动作冻结。
  - 实现证据：Mock 工单/命令队列 SQL 权限过滤与稳定分页，显式 ticket_handle 授权后认领、备注、回复、关闭、升级和释放；版本与同键同参数命令避免重复写。MockSupportCase 冻结同问题/来源会话 Agent 写动作，独立目标提交与客服命令共用锁边界，只读查询仍允许。升级包须另有 support_context_read，原主体来源权限与客服/原主体知识 ACL 独立复检；0035/0036 未执行，测试暂缓。
- [ ] 17.7 实现聊天/工单工作台的接管、工单链接、业务状态与错误/空态/窄屏交互。
  - 代读边界补齐（2026-10-18）：修正客服ACL预检查与仅按原主体读取正文的分离窗口；read_delegated_knowledge_evidence经共同读取函数把双方ACL放入同一正文SQL，默认加锁且租户不符在定位前拒绝。升级包预先按文档ID统一加锁后检查最多6份引用，任一不可用沿用隐藏上下文投影。本人动作/客服工单升级包详情在5秒/PG语句4秒/锁1秒事务内完成DTO投影、成功no-store，DB故障既有KNOWLEDGE_CONFLICT；不扩展拒绝审计覆盖。escalation_services纳入清单，无新表/迁移/依赖/公开字段；源码/调用方复查，未运行测试、类型检查、构建或DB/权限/并发验收，17.11和163/302保留。
  - 已实现部分：工单工作台/详情/命令弹窗、升级包抽屉、聊天接管提示与有限可见页轮询、Mock 工单链接、同命令网络未知重试、列表错误/空态已接通；浏览器、窄屏与键盘验收按当前约定暂缓，整项保留未完成。
- [ ] 17.8 选择首个真实工单系统并确认沙箱、幂等/查询/字段契约；真实接入依赖 W20 凭据隔离。
- [ ] 17.9 实现选定连接器的受限查询/建单/更新；需审批动作额外依赖 W16，不阻塞 P0 R2 Mock 验收。
- [ ] 17.10 实现目标系统回调验签、租户映射、重复/乱序去重与业务状态对账。
- [ ] 17.11 验收跨租户工单、重复升级、客服与 Agent 并发、外部超时以及完整升级上下文。

## 18. W18 运行观测、健康口径与 Dashboard 剩余任务

工程优先级：P1；产品阶段：产品 P0 基础观测；P1 完整验收；模块：M01/M02/M10/M11。

依赖：W00、W03、W05。规格：[runtime-observability-completion](specs/runtime-observability-completion/spec.md)、[agent-health-computation](specs/agent-health-computation/spec.md)、[dashboard-data-contract](specs/dashboard-data-contract/spec.md)。覆盖：G29、G30。

完成标准：本轮计量准确，等待/恢复可解释，旧 Dashboard 16 项有明确验收证据。

- [x] 18.1 修正模型/工具次数与 Token 统计为当前 Run/attempt 调用事实，避免累加 checkpoint 历史。
  - 实现证据：ModelCallLedger/ToolInvocation 为次数事实源，按整个 Run 和独立 attempt 聚合；失败/未知调用保留消费、拒绝不消费。checkpoint 旧消息与 SDK 工具 callback 不再累加，缺任一模型 usage 不伪称完整 Token 合计。测试暂缓。
- [x] 18.2 记录实际模型/工具/策略版本、attempt 与成本来源，覆盖重试、路由和缺失 usage。
  - 实现证据：物理模型许可记录固定端点版本、runtime_budget_v1/估算策略/依赖重试序号/attempt/未知 usage；当前仅固定模型，无虚构动态路由。工具许可及独立追踪记录固定实现/当前目录版本/风险/attempt；父 attempt 成本独立合计，子 span 不复制整个 Run 成本。测试暂缓。
- [x] 18.3 区分模型首输出、API 事件提交、客户端首正文的计时边界与起点，前端上报不决定业务状态。
  - 实现证据：SDK 首个非空输出块、API 首次读取已提交正文的可见性上界、浏览器本页提交至 React 正文提交分别记录。客户端事件号按当前主体 Run 验证，严格有界数字、Run 锁去重，不跨时钟相减或回填刷新测量。API 观测含轮询/重连延迟而非精确 DB commit/网络耗时；独立遥测写失败不改产品事实，Trace 接口/界面解释边界与缺失。测试未执行。
- [x] 18.4 为 retrieval/诊断/审批/恢复/对账增加父子观测和阶段耗时，等待与执行分别统计。
  - 实现证据：既有网关 retrieval/调用观测保持 Agent attempt 父关系；Trace 增加持久化事实的有界 business_phases_v1 投影，分别展示执行尝试、诊断、审批等待/批准后派发等待、恢复等待、动作执行/验证/对账及其回执子阶段。按本 Run action.started 事件关联历史 generation，不以可变执行上下文猜测归属；每类最多 100 条并显式 truncated。未结束/缺失/逆序耗时为空，阶段不额外累计 Token/费用，观测投影不改产品事实；UI/契约已接通，测试未执行。
  - 观测边界：网关独立记录 retrieval/tool/拒绝追踪，关联 attempt 父 span，不存原始正文；可选观测失败不回滚已提交调用许可/回执。阶段投影实现已经补齐，正式验收仍由测试任务承担。
- [x] 18.5 为工具策略编辑、授权拒绝、审批与动作回执补安全审计；观测失败不得回滚产品终态。
  - 实现证据：目录编辑/撤销与审批已有追加控制面审计；工具拒绝/重复调用和审批状态变化保留同事务追加 RunEvent，工具标识先脱敏，决定记录实际人员。动作结果同事务追加 generation/attempt/receipt/status/冻结摘要/版本的最小关联事实，不用 HTTP 成功冒充 verified。新增租户管理员 security-events 标准 SQL 分页和 Trace 审计面板，白名单限长脱敏、不返回理由/正文/外部响应，不补造旧 actor/receipt。SDK/阶段观测仍隔离产品事务，失败不改变回执/终态；测试未执行。
- [x] 18.6 实现 OpenTelemetry 映射/exporter 配置与脱敏/租户/约定版本控制，业务审计仍保存在数据库。
  - 实现证据：telemetry_services/telemetry_export 将已结束数据库观测映射为固定 supportops_otel_v1/OTLP HTTP JSON；独立只读维护命令默认关闭，显式租户允许列表/≤24小时窗口/有限批次/双字段游标，默认只预览数量与摘要，--send才联网。租户域HMAC假名化trace/span/版本/工具，父关系复检同租户/Run/attempt；封闭标量投影不透传名称/原始错误/provider request ID/正文/任意metadata，父聚合不重复导出用量，缺失保持不可用，客户端计时仍不可信。HTTPS固定获准IP并校验原主机TLS，拒绝环境代理/重定向/压缩，整体超时/载荷和响应大小受限，HTTP200部分成功独立报告且不自动重试。沿用HTTPX无新增依赖/表/业务写入，数据库审计保留；配置示例、观测文档和协议来源同步。不保证可靠投递/迟到数据完整性或接收端去重，业务阶段不补造span；未实际导出、接收端联调、测试、构建或迁移。
  - W18.6发送结果补齐（2026-10-18）：telemetry_export_report_v1区分预览/无发送/完整及部分回执/发送前失败/结果未知；进入单次发送路径后失败或中断退出4，无回执计数为null，部分成功退出2且无next_cursor。原requested_after/batch_last_cursor/载荷摘要只作扫描定位，不是成功水位；0拒绝警告只投影存在性、完整回执才确认计数。重复JSON字段/非有限常量/int64及批次数量/警告类型校验，不输出原文。输出失败不重发；默认关闭/显式send/无调度或可靠投递保证保留。无新依赖/表/公开API，未运行命令、接收端联调、测试或构建，163/302不变。
- [x] 18.7 增加首文本、预算、租约、恢复、对账与权限拒绝的告警和缺失字段诊断。
  - 实现证据：`runtime_alert_services.py`/Worker 独立有限批次扫描，八类规则、来源唯一去重/generation 再提示、缺失宽限、独立已阅版本与审计；新增 0039 两张观测表。API 租户管理员 SQL 权限筛选/count/分页；Web `/analytics/alerts` 筛选/分页/已阅/详情及关联 Trace。仅读取指标元数据、不改产品状态、不发外部通知；SDK 首输出阈值默认未配置，客户端缺报不自动报警，逐条/扫描 stale 区分未知与正常。迁移/测试/构建均未执行。
- [x] 18.8 承接旧 Dashboard 2.3：核验健康协调任务与版本/服务/Run 租约事实，缺失时补齐；不伪造 per-Agent 进程租约。
  - 实现证据：健康与状态 API 共用数据库聚合 agent_activity；活跃请求不被 24 小时历史窗口漏掉，执行须有当前 generation/会话许可/有效 attempt 租约。过期/旧 owner 待确认/死信/待对账不假装模型在执行；共享 degraded Worker 仍视为存活，并说明维护/排空。活动模型启停/固定工具版本/撤销/Mock flag 影响健康，缺快照不以最近 20 条另造健康结论。实际验收仍待后续测试任务。
- [ ] 18.9 承接旧 Dashboard 2.4：验收生命周期/执行态/健康态，Run 去重与无近期活动/等待解释。
  - 已实现部分：业务审批/动作等待的执行态已区分，健康原因明确等待数量与模型 Worker 已释放，避免无近期活动解释遮蔽等待；验收暂缓，整项未完成。
- [x] 18.10 承接旧 Dashboard 2.5：固定错误率、P95、活跃计数窗口及最小样本，单次失败不误污染。
  - 实现证据：健康历史统计按 24 小时 Run 创建窗口，完成/失败计入，取消排除；错误率/P95 至少 3 个有效样本，P95 使用 nearest-rank。活跃请求另按全时段 queued + 当前有效 executing/cancelling，排除未证明 owner/过期/死信；状态 API 删除最近 20 条另行推断与单次失败降级，复用健康读模型。未执行验收，不回填旧 change 的测试勾选。
- [x] 18.11 承接旧 Dashboard 2.6：核验 pending_publish、health_reason、computed_at/observed_at 与 stale/unknown 展示。
  - 实现证据：状态 GET 只读 Worker 快照，去除读取时重算/缺失伪造当前时间；computed_at/observed_at/health_stale 契约贯通 API/Web。默认 90 秒过期、缺时间或超前超过 5 秒显示 unknown/原因并清除过期当前指标；执行许可仍独立实时读取。pending_publish 按当前草稿/活动版本摘要而非旧快照，停用生命周期覆盖旧健康。Web 展示计算/观测/差异、标准 SQL 服务端分页、错误重试/清空旧数据及请求序号保护。仅代码/契约审阅，测试/类型检查/构建未执行，原 change 浏览器验收仍未勾选。
- [ ] 18.12 承接旧 Dashboard 3.7：补系统 API 管理权限、隔离、分页、空租户与指标范围一致性测试。
- [x] 18.13 承接旧 Dashboard 4.2：补时间、Agent、生命周期、健康、版本、模型、用户/部门、运行状态筛选并保留刷新语义。
  - 实现证据：共用 DashboardFilters/useDashboardScope 与 API 查询范围贯通系统/Agent 总览、调用、错误、状态、排行；时间/Agent/当前生命周期/健康/固定 Agent 或模型版本/原用户及当前末级部门/全部 Run 状态（含审批/动作等待）统一传递。区间与分页保存在 URL，筛选重置页码、刷新保持区间；非法 UUID/枚举/时区或不成对/超过 90 天区间拒绝、不扩大查询。请求序号保护、300ms Loading、权限错误清空旧数据；健康/全局服务/用户总数和新增会话口径明确说明。按代码和契约核对；仅针对发现的 JSX 语法错误做最小解析排查，未做类型检查/构建/业务测试，旧 change 验收未勾选。
- [x] 18.14 承接旧 Dashboard 4.4：核验用户/会话/调用/成功率/错误/Token/成本及 P50/P95/P99 卡片，缺失值区别于零。
  - 实现证据：dashboard_run_metrics 共用 SQL 范围，摘要/桶不再加载完整 Run 正文或对已知 usage 子集求和；空集合已知 0，任一匹配 Run 缺 usage/费用则总量 null，并返回 source/sample_count/missing_count。终态非负端到端耗时至少 3 样本、nearest-rank P50/P95/P99，包含此前等待，未完/取消不混入。Web 补 14 张指标卡、样本/缺失说明、微美元显示、区间末 5 分钟语义。共享 degraded Worker 仍存活；队列按固定名最新有效样本去重，不相加多个 Worker 的 LLEN，旧无名/未知/stale 观测不填零。代码/契约核对完成，测试/类型检查/构建未运行，原 change 的正式验收仍未勾选。
- [x] 18.15 承接旧 Dashboard 4.5：核验趋势与时间桶下钻，目标页面保留原时间/Agent/其他有效范围。
  - 实现证据：timeseries 桶按请求 start/end 裁切，补 bucket_end，首桶不再统计选定起点前记录。趋势点击与可操作时间桶选择用准确左闭右开区间下钻调用列表，保留 Agent/用户/部门/生命周期/健康/版本/模型/Run 状态并重置分页；排行/状态入口同样保留范围。Trace 抽屉复用观测/预算/业务/安全/回执组件，单独错误重试与请求序号避免迟到结果污染；浏览器/窄屏/键盘实际验收仍由 18.21/18.22 承担。
- [x] 18.16 承接旧 Dashboard 4.6：核验排行/分布、调用/用户/失败率/成本/P95/工具数据，不重复现有实现。
  - 实现证据：dashboard_rankings 共用 SQL 范围与关联租户约束，补调用/活跃用户/失败率/Token/估算费用/失败调用/P95/工具许可八指标；失败率分母仅 completed+failed，P95 按终态有效耗时 nearest-rank/至少 3 样本，窗口函数只返回每 Agent 聚合值。Token/费用要求所有匹配 Run 有值；工具按去重许可账本排除 denied，并与已知 Run 汇总核对，缺失/不一致保留未知，不把许可当业务成功。已知优先、UUID 平局排序、固定 Top 10；Web 可选指标并保留 URL，未知单独展示、不填零或造图柱。完整健康 SQL 分布不以第一页推算，全部下钻保留范围。代码/契约核对完成，正式测试/类型检查/构建/浏览器验收未执行，旧 change 验收保持未勾选。
- [ ] 18.17 承接旧 Dashboard 4.11：验收主题、桌面/窄屏、概览骨架、列表加载、空态、服务异常与缺失指标。
- [ ] 18.18 承接旧 Dashboard 5.1：真实多 Worker 服务租约、积压与过期聚合测试。
- [ ] 18.19 承接旧 Dashboard 5.2：覆盖无活动、尝试过期、连续失败、待发布、服务不可用与审批等待的健康测试。
- [ ] 18.20 承接旧 Dashboard 5.3：测试空租户、全部停用、并发 queued/running、恢复去重、unknown usage 与统计窗口。
- [ ] 18.21 承接旧 Dashboard 5.4：浏览器验收筛选、趋势下钻、排行、状态表分页与 Trace 打开。
- [ ] 18.22 承接旧 Dashboard 5.5：验收状态文字/颜色、抽屉、筛选保持、窄屏与键盘可访问性。
- [ ] 18.23 承接旧 Dashboard 5.6：记录租约/快照留存、告警阈值、stale 条件和小时聚合启用阈值。
  - 已实现部分：`docs/modules/observability/overview.md` 记录服务/attempt/动作租约、当前快照覆盖、checkpoint 留存、健康窗口/最小样本/错误率、告警阈值/宽限/stale 与无自动清理的边界。小时聚合当前未启用，尚无批准的容量/延迟数值阈值，须取得隔离负载证据并批准后补齐；整项保持未完成，不凭空填写实测数据。

## 19. W19 固定场景评测、真实故障与安全门禁

工程优先级：P1；产品阶段：产品 P0/P1 发布；在线反馈 P2 候选；模块：M01–M12。

依赖：W00。规格：[support-quality-evaluation](specs/support-quality-evaluation/spec.md)。覆盖：G31、G41。

完成标准：批准场景/版本/结果可复现，发布级测试使用对应真实隔离依赖，安全失败阻断。

- [ ] 19.1 将 S01–S20 编为版本化样本，每场景正例/边界/失败至少各一个，形成不少于 60 个样本。
- [ ] 19.2 固定允许/禁止工具、风险、期望事实、引用、升级条件和 Mock 输出；评测不默认调用外部生产模型。
- [ ] 19.3 建立可重复评测 runner、样本 manifest 与结果存储，关联 Prompt/模型/知识/工具/策略/图版本。
- [ ] 19.4 实现事实、引用、路由、参数、升级包和目标业务最终状态评分，不能仅对回答文本评分。
- [ ] 19.5 实现 Token/成本/首文本/完整响应与负载记录，区分 unknown、取消、断网和外部故障。
- [x] 19.6 沿用既有批准目标并记录适用范围；非安全调整需正式决策，跨租户/审批绕过/严重泄露不允许豁免。
  - 交付证据（2026-10-19）：README“W19.6既有目标的版本与适用范围”引用0.1.0/2026-09-14真实归档评审，明确P0内部批准与P1企业复核边界；逐项保留七质量目标/公式、3秒与10秒P95、安全零容忍、60样本/负载/时钟/缺失成本及正式非安全调整要求。同步support-quality-evaluation规格，不改阈值/门禁、不生成例外或企业批准；样本/runner/实测仍待19.1–19.5及其余验收，未编写或执行测试。
- [ ] 19.7 增加 Prompt Injection、工具描述污染、参数篡改、凭证重放、ACL 撤销与外链泄露安全集。
- [ ] 19.8 提供隔离 PostgreSQL/Redis/模型与工具替身的全链路测试环境，明确禁止生产连接并保留重复运行清理。
- [ ] 19.9 故障矩阵覆盖入队、领取、checkpoint、产品提交、SSE、权限撤销、审批恢复与外部 response lost。
- [ ] 19.10 在 W13/W14/W17 完成后验收知识问答、429 诊断、无证据建单和依赖失败降级四条 P0 主路径。
- [ ] 19.11 在 W15/W16 完成后用隔离 R4 动作验收完整申请/冻结/非本人批准/拒绝/改参/过期/一次性/对账/复核。
- [ ] 19.12 企业试运行前用批准的脱敏真实工单与正式规则复核内部场景，未通过样本不能当作企业成功标准。
- [ ] 19.13 输出每次实际失败的根因、责任人、修复/回滚决策和版本差异，不伪造已通过结果。
- [ ] 19.14 在线采样/反馈启用前决定同意、租户范围、脱敏、费用与保留期；未批准默认关闭。

## 20. W20 秘密提供者、短期凭据与连接器隔离

工程优先级：P1；产品阶段：产品 P1 真实系统接入前；模块：M03/M04/M06/M12。

依赖：W00、W07、W08。规格：[production-operations-and-retention](specs/production-operations-and-retention/spec.md)、[governed-tool-connectivity](specs/governed-tool-connectivity/spec.md)。覆盖：G33。

完成标准：生产连接器持有受限秘密，撤销即时生效且模型/日志不可读取。

- [ ] 20.1 选择获批准 Vault/KMS provider 与服务身份，明确 local/test 密封兼容、租户/用户命名和成本。
- [x] 20.2 把直接 LocalEnvelopeSecretProvider 构造收敛为 provider factory，API 与 Worker 采用同一策略。
  - 实现证据：create_secret_provider(settings) 收敛 API 依赖、聊天 Worker 与独立模型探测 job；显式 SUPPORTOPS_SECRET_PROVIDER=local_envelope_v1，保留旧密封格式/默认兼容，未知值由配置拒绝且工厂不静默降级。Vault/KMS 和服务身份仍由 20.1 决定，未宣称完成生产 provider；未新增依赖，测试未执行。
- [ ] 20.3 实现密封/解封、轮换、revision、revoked 状态与访问审计，不把 provider 秘密放到模型上下文。
  - 已实现部分：本地密封、固定 provider/历史脱敏提示、追加 revision、永久撤销、解封前后检查、API/Worker 独立解封审计短事务和运行/探测拒绝审计已接通。0037 不虚构历史撤销/访问记录；企业 Vault/KMS provider、服务身份仍待 20.1，整项保留未完成。测试/迁移未执行。
  - 元数据与密文读取分离（2026-10-18）：assert_credential_revision_active只SELECT修订ID/端点/provider/撤销时间，返回frozen元数据；显式tenant/credential_id/revision及父子未删除条件，不加载历史密文/提示/说明或依赖identity map旧实体。已核对全部调用方，仅解封入口消费返回值，其余继续只检查。resolve_credential_secret先核对端点/provider，再以同一SQL限制原修订ID/范围/当前未撤销未删除状态才读密文；未命中只重查元数据分类、无重读或换修订，审计后返回前再核对原ID/端点/provider。provider普通异常及非字符串/空白结果统一既有UNSEAL_FAILED并隐藏原异常链，取消不捕获，不新增重试/恢复许可。原独立审计/调用边界保持，已发请求及其他调用方已有凭据实体读取不由此获得全局隔离保证。源码已在发布清单，无新文件/依赖/表/API/迁移；仅源码/调用方/差异审阅，未运行测试、类型检查、构建、DB、实际解封或外部调用；20.3与163/302不变，企业provider/身份仍待20.1。
  - 默认实体密文加载保护（2026-10-18）：ModelCredential/ModelCredentialRevision.encrypted_secret在现有SQLAlchemy映射设置deferred/raiseload，默认实体查询不选择密文，未加载属性误读直接拒绝，不隐式SQL。create_model_endpoint/save_model_endpoint_configuration创建分支及rotate_model_credential复用一次seal局部结果写当前值和新revision，移除实体密文回读；受控解封仍用既有带当前状态/固定范围显式列查询。已核对控制面/发布/健康/聊天/探测读取与历史before_flush，不加undefer或绕过开关；显式可信SQL/此前已赋值加载对象不受此加载策略隔离。models/model_services纳入新发布源码摘要，不改旧manifest，无新文件/表/依赖/API/迁移，65表/0050不变。源码发现旧test_agent_management直接访问新会话查询后revision.encrypted_secret，恢复测试时须按新内部读取契约适配；按用户要求本轮不修改或运行测试。仅源码/调用方/差异审阅，未运行类型检查/构建/数据库或实际seal/open，20.3及163/302仍部分完成，企业provider与身份待20.1。
  - 访问审计等待与固定失败域（2026-10-18）：解封后统一_persist_resolution_audit，单调2秒覆盖独立会话建立/事务写入/提交及退出，PG事务内语句1秒/锁250毫秒；退出后复核截止，驱动迟到完成也不返回密钥。普通异常/超时统一CredentialAuditUnavailable/MODEL_CREDENTIAL_AUDIT_UNAVAILABLE并隐藏原SQL/参数/异常链，不重读密文、再次解封或重放审计；外部取消传播，未知提交不当作未访问/未记录，不删除已有事实。无工厂核心兼容路径只限定原flush等待，不提交/回滚或修改调用者事务配置，不能当独立持久化保证。API该子类503/no-store、其余凭据拒绝403/no-store；现有Run/探测按原CredentialAccessDenied保留错误码且不自动恢复，Web通用字符串错误与OpenAPI既有503契约适用，未扩新字段。核心源码已在发布摘要，无新文件/表/依赖/迁移，65表/0050；仅源码/调用方/差异审阅，未运行测试、类型检查、构建、数据库、实际解封/审计或网络调用。清理不承诺硬截止，企业provider/身份仍待20.1，20.3与163/302保持未完成。
  - 密封写入边界补齐（2026-10-18）：两条创建路径和轮换统一_seal_model_credential，API/Core/LocalEnvelope复用secrets中8–20000字符边界并去除首尾空白；非法输入422，不进provider。配置编辑null/未提供保留，核心显式空字符串不再静默忽略。provider普通异常/非字符串/空白或等于规范输入的结果固定CredentialSealUnavailable/MODEL_CREDENTIAL_SEAL_FAILED、隐藏原异常链，不重试或回退旧密钥；CredentialServiceUnavailable统一密封与原审计依赖503/no-store，其余凭据拒绝403/no-store。创建先密封再新增端点，轮换先验证密封结果再改凭据/revision，一次结果写当前与新历史，API原事务回滚配置变化；结构检查不证明通用加密正确。schemas同步约束/轮换strip，配置表单增加长度提示及错误aria关联，Web既有错误/请求类型沿用，无新接口字段；模型发现不持久化的Key契约不变。secrets加入新发布源码摘要，不改旧清单，无新文件/依赖/表/迁移，65表/0050。仅源码/调用方/差异审阅，未运行测试/类型检查/构建/浏览器/数据库或实际seal/open，20.3与163/302仍部分完成，企业provider/身份待20.1。
  - 配置编辑顺序与表单校验（2026-10-18）：现有连接名称/供应商预设移到可选轮换成功后赋值，避免新Key失败前由本次编辑造成待写/自动flush，整体原子性仍由调用者事务保障。弹窗新增共用规范Key码点长度校验，保存/保存并使用/保存测试配置均拦截非法值，输入与提交错误关联aria并支持聚焦；编辑留空仍省略Key保留原值，模型发现沿原请求契约，繁忙时不重复委托保存。未改用户已有未跟踪hooks/拆分，无新文件/依赖/接口/表/迁移；仅源码/调用方/差异审阅，未运行测试/类型检查/构建/浏览器/数据库操作。企业provider/身份仍待20.1，20.3及163/302不勾选。
  - 轮换竞争的元数据刷新（2026-10-18）：轮换FOR UPDATE查询增加populate_existing和显式未软删除条件，按可信租户/端点刷新同会话已预加载的当前revision后递增；配置保存显式使用轮换返回值。默认映射不选择旧密文，原事务内当前值/新历史/审计保持，不自动重试或改写Run固定引用。已审阅直接轮换/配置保存调用方，无新文件/依赖/接口/表/迁移，model_services已有发布摘要覆盖；未运行测试、类型检查、构建或数据库并发验收，20.3及163/302不变，企业provider/身份待20.1。
- [x] 20.4 设计连接器独立执行身份/进程或沙箱及最小出站权限，模型不能读取连接器环境。
  - 设计证据：design第12节固定独立OS身份执行进程/认证IPC与网关/broker/模型四类最小能力、封闭请求/服务端目标别名/一次派发许可/current generation及授权复检；不继承应用环境/描述符/主密钥、无业务库权限/host挂载/任意代码，进程资源与网络默认拒绝、DNS固定/TLS/禁止代理重定向、broker受众与固定revision。输出仍经统一Schema/脱敏/来源证明，进程取消不代表远程未执行，写结果未知按原键对账。只完成设计，不把现有可信进程内Mock或OTLP维护命令称作沙箱；20.1/20.5及外部协议/IPC/OS网络实现和20.7/20.8验收均保持未完成，没有选定provider、部署或读取真实凭据。
- [ ] 20.5 实现短期受限凭据获取、受众/scope、撤销和更新，禁止共享通用管理员凭据访问用户资源。
- [x] 20.6 实现旧固定 revision 可追踪但撤销后拒绝执行的契约，不默默替换 Run 历史引用。
  - 实现证据：ModelCredentialRevision 固定 provider/masked_hint、revoked_at/by/reason；撤销与轮换共用凭据锁，指定 revision 的决定不可覆盖/解除。新消息/发布/启用与健康、Run 领取/每次调用/提交、模型探测领取/续租/阶段及出站检查固定 revision，撤销硬错误不自动恢复；轮换不改旧 Run 引用、不替换旧密钥。已发请求不保证撤回，只阻止后续授权边界和提交。0037/测试未执行。
- [ ] 20.7 为 provider 不可用、轮换竞争、过期、越权解封及日志/Trace 脱敏增加测试。
- [ ] 20.8 在隔离环境演练注入内容读取秘密失败、连接器故障与凭据服务恢复。
- [x] 20.9 同步控制面 masked hint/revision、停用原因和审计查询，不向前端返回明文秘密。
  - 实现证据：模型响应新增当前凭据可用性/provider/撤销时间；管理员历史 revision 与 credential-audits 先租户过滤再 SQL 稳定分页，历史接口仅选元数据列，不读出密文。模型详情具备分页历史/白名单访问审计、明确说明的永久撤销确认与当前 revision 并发保护；历史探测通过不覆盖撤销，未知历史提示不补造。没有返回明文/密文，浏览器和迁移验收暂缓。
  - 撤销确认与未知响应补齐（2026-10-18）：面板按端点重挂载并固定当前会话，延迟读取发送前及结果回写复核会话/scope/请求序号；确认固定目标ID/revision与打开时当前revision，已知变化拒绝且不借新会话发送。同步mutation围栏阻止重复提交，核对目标回执和撤销时间/人员后才显示成功；失败/不匹配关闭旧确认并清说明，仅刷新GET，重新写入须重新确认。详情/两列表GET及撤销成功响应no-store，Web四请求禁缓存，无新字段/接口/依赖/表/迁移/文件；未改已有未跟踪页面/hooks。仅源码/调用方/差异审阅，未运行测试、类型检查、构建、浏览器或数据库，163/302保持，企业provider/身份仍待20.1。

## 21. W21 数据保留、清理与授权擦除

工程优先级：P1；产品阶段：产品 P1 数据治理；模块：M07/M08/M10/M11/M12。

依赖：W00、W05、W20。规格：[production-operations-and-retention](specs/production-operations-and-retention/spec.md)。覆盖：G35。

完成标准：软删除/留存/擦除边界明确，授权维护不削弱业务 ORM，恢复不复活删除数据。

- [ ] 21.1 确认消息、checkpoint、事件、观测、知识、记忆、凭据 revision、回执和备份的留存/法律保留规则。
  - 盘点进展（2026-10-19）：根据`DATA_CATEGORIES`、`retention_inventory.inventory_queries`和现有checkpoint清理配置，在design.md记录16类应用数据的可定位范围及未覆盖副本。此矩阵只描述当前代码覆盖；所有保留期限/法律保留仍未获业务确认，checkpoint 30天只是运行配置，不作为批准规则。备份/外部副本、记忆、日志外送及共享知识/凭据/动作/审计引用仍有未知项。未新增可执行规则或清理许可；须由企业数据治理负责人提供期限、例外及备份恢复重施依据后才能完成21.1，也不能据此解锁21.3实际清理。
- [x] 21.2 设计 RetentionPolicy/ErasureRequest/DeletionTombstone 与审批范围，先提供 dry-run 影响清单。
  - 实现证据：retention_contracts/services/inventory、data_governance API和0045三表；草稿不可变、周期/法律保留未确认为null、候选复核人不产生授权。本人申请由可信身份派生租户/主体，同键同规范范围复用、异范围409，固定创建时策略/截止时间；expected_revision控制一次预览/撤回，报告不可覆盖，撤回保留原历史。16类静态SQL计数包含软删除历史、只读元数据，不读正文/密文；共享关联保留人工审查，未枚举依赖null，checkpoints/memory/backups/external_copies无适配时明确未知。当前在途Run/动作另查不受截止限制；整体10秒/PG语句2.5秒/锁1秒，失败回滚不保存半份报告。报告GET复检摘要/范围、坏报告隐藏，未来复核绑定申请/预览/策略且非本人/显式主体/独立维护/重新盘点，approval/execution/complete/eligible始终false。Web本人数据申请分页/新建/预览/详情/撤回与用户菜单已接通；管理员无他人申请入口。tombstone只有追加事实结构，无生成/实际擦除/备份恢复重施，21.1及21.3–21.9仍待。README/AGENTS/docs同步59表/0045，迁移/测试/类型检查/构建未执行。
- [ ] 21.3 实现分批保留清理 job、有限锁/重试与可观察状态，避免阻塞在线执行。
  - 独立PG Store继续实现（2026-10-19）：retention_batch_events/store及0056新增第71表retention_cleanup_events，具体实现主体域事务try锁、500ms SQL/250ms锁/2秒事务等待、固定计划/owner/generation/事件摘要链与32条/128KiB边界。原计划未派发且旧租约到期才可显式最多三次重领，已派发永久拒绝重领；前批/其他同主体作业未完成、作业序号/上下文变化、重复资源/既有事实拒绝新批。验证次数最多三次、最新验证ID围栏，verified项按原fact_v1/+00:00摘要与原验证/批次回执/事件同事务写tombstone；已有事实不覆盖，部分/未知保留需处理，不推进整作业。固定独立PG库/原维护角色/单主体/ledger/authority，拒绝超级/RLS/所有权/DDL和其他业务表写权，两个账本表只SELECT/INSERT；0056四个具名ALWAYS保护覆盖两表UPDATE/DELETE/TRUNCATE，有历史拒绝降级，Store只读核对固定体/形状。批次read_status及作业read_job_status提供有界状态投影；后者在只读REPEATABLE READ快照内按连续序号游标分页，每页最多20批，先汇总事件数和JSONB载荷字节再读取核验，历史链总输入不超过4MiB，并复检同一作业固定上下文，不续租/调用目标/补造终态。authority目的现分execute/verify/read/reconcile，单批与作业查询只申请read权限。另新增过期派发只读恢复协调：authority新签最多60秒permit绑定原plan/lease/generation/authority/approver/verifier，Store拒绝申请主体自批及重复permit_id，追加reconciliation_started后只读观察，累计验证仍最多三次、最新ID围栏、已确认事实与回执原子落账并追加reconciled；不新领租约、不调用executor或重放目标。approver_id仅为可审计声明，真实身份/角色与批准仍须由authority核验；该路径无默认authority实现，缺真实批准/法律保留/完整依赖复核即拒绝；ORM/本人预览和发布摘要同步。实际规则/身份、类别及副本适配、后台调度和验收仍待，21.3和169/302保持未完成。71表/head0056源码，未运行迁移、DB、实际清理/事实落账或测试/类型检查/构建。
  - 单批次协调实现（2026-10-19）：新增retention_batch_contracts/runtime，固定租户/本人/原申请/类别/操作/政策/批准/盘点/账本及排序唯一的最多100项摘要，短租约owner/generation与原计划绑定。执行前要求可信持久账本先提交一次派发，再进入固定类别适配器；独立只读验证精确集合，落账回执逐项匹配操作/证据/事实，全部verified才算批次completed，部分/未知为needs_attention，job_complete始终false。执行最多10秒、复核8秒，各边界按原租约/许可窗口裁剪，未知仅尝试追加最小事件、从不重派，取消传播；显式复核不持执行器。初始协调器交付时，Store/authority/类别执行或复核适配器尚缺；后续Store实现见同任务“独立PG Store继续实现”记录。默认缺任一依赖拒绝，无API/Worker/调度/实际清理；21.1/21.4及authority/类别适配/调度与验收仍待，21.3与169/302不勾完。协调器初始实现无新表/迁移，70表/head0055；未写或运行测试、类型检查、构建、DB、迁移或清理。
  - 过期恢复签发前置核对（2026-10-19）：reconcile_expired_retention_batch先走独立read许可，核对原lease已经过期、账本仍有dispatched_at且需要对账后才向authority申请permit；Store在实际追加开始事件的主体锁事务中再次复检当前事件、许可绑定和permit_id未使用，覆盖读取与签发之间的状态竞争。已完成/未派发批次不会触发恢复许可签发，预检/签发失败不追加未确认事件，恢复开始成功提交后才记录后续未知；未新增目标执行路径、表或迁移。仍不运行测试、数据库或实际操作。
- [ ] 21.4 建立独立受控擦除服务/维护身份；不移除普通业务 deleted_at 或物理删除保护。
- [ ] 21.5 处理索引/缓存/外部副本和未来记忆数据的删除传播，保留最小必要审计证明。
  - 首个具体知识索引观察器（2026-10-20）：retention_knowledge_propagation提供PostgresKnowledgePropagationVerifier，原knowledge快照locator摘要必须匹配原fact，固定文档/版本/内容摘要、目标DB/原角色摘要、目标别名和keyword_v1读取隔离投影。独立PG角色仅允许snapshot/generation/chunk三表明确元数据列SELECT，标题/锚点/正文及其他public表读取或写入拒绝；RC只读事务取得共享维护try锁后复用0049/0050保护核对，快照/最多128代次/10000分块计数在同一SQL快照查询并复核租户/绑定/算法/计数。缺快照unknown，不用缺行或零分块冒称擦除；仅软删除快照且所有代次invalidated为quarantined，其他为present，永不返回erased。0.9秒整体工作等待、SQL100ms/锁50ms，authority前后复查真实来源/当前读取许可/完整拓扑及实际部署读路径，不注册默认目标或调用清理。新源码纳入发布摘要，无新表/迁移/依赖，72表/head0057；实际外部缓存/记忆及删除传播适配、批准输入和验收仍待，21.5及169/302未完成。未编写或运行测试/类型检查/构建/迁移/数据库/真实观察或删除。
  - 持久审计适配实现（2026-10-20）：retention_propagation_audit/0057新增第72表retention_propagation_events，固定独立PG库/原维护角色/主体/原请求，只有本表SELECT/INSERT且拒绝其他public表写权限。每request最多18条/每条8KiB，try事务锁、连续序号/原请求与列绑定/摘要链/目标顺序完整核对；缺项结束、重复或结束后追加拒绝。两条ALWAYS保护及ORM历史保护禁止改删清空，有历史拒绝降级。追加0.45秒工作等待、SQL100ms/锁50ms，许可在写前/提交前后复检，未知不重放；read_history使用独立当前历史读取许可、只读REPEATABLE READ与2秒预算，原请求过期仍须新读取许可，不调用目标或产生执行许可。本人预览与发布源码摘要同步。只交付源码/迁移，未运行测试/类型检查/构建/迁移/DB或真实审计；实际authority/副本适配/删除传播与验收仍待，21.5及169/302不勾完。
  - 只读传播核对实现（2026-10-20）：新增retention_propagation_contracts/retention_propagation，将原fact_v1完整性、当前账本代次、固定副本盘点摘要和最多16个排序唯一目标绑定到最长60秒请求。独立authority每边界核对原事实实际成员、当前读取许可及完整拓扑；按服务端固定目标别名分派只读verifier，逐项核对原资源/事实/范围/代次/观察时间。缺失适配器、超时、异常、仍存在或动作不符返回needs_attention；独立持久审计先记开始、逐项最小摘要和结束，失败不返回半份成功，无自动重放。全部匹配仅listed_targets_confirmed，擦除完成/恢复开放/执行许可恒false。新源码进入发布摘要；无默认authority/审计提供者/目标注册、删除执行器或API/Worker接线，实际索引/缓存/外部副本及记忆适配仍待，21.5保持未完成。无新表/迁移/依赖；未编写或运行测试、类型检查、构建、迁移、数据库或实际核对/删除。
- [ ] 21.6 保护仍被固定版本/在途动作引用的凭据与证据，定义销毁后不可恢复的明确行为。
  - 已实现历史保护部分（2026-10-18）：reference_history接入既有Session before_flush，拒绝历史凭据内容/定位和知识证据/分块列改写；首次完整撤销及单向软删除保留，旧值未加载时拒绝，不为检查解封或查询正文。0049为model_credential_revisions/knowledge_evidence/knowledge_chunks增加PG行UPDATE/DELETE及语句TRUNCATE保护，ALWAYS启用；密文/主体/版本/摘要不可原地覆盖，已有撤销/软删除事实不可清除或改写，轮换仍新建revision。三表有任意历史即拒绝降级，不回填或扫描修复旧内容；新核心源码和db纳入发布清单。保护范围保守覆盖全部三表历史，不靠先查引用再修改，避免该检查窗口；原授权/ACL失效仍拒绝使用，不替换固定引用。未来独立维护身份、全部依赖盘点、真实销毁及恢复tombstone仍待21.1/21.3–21.7，整项保持未完成。65张ORM表不变，迁移head为20261018_0049；未执行迁移、测试、类型检查、构建或实际清理/擦除。
  - 固定引用影响说明已实现：本人预览的credentials按截止时间内本人会话/Run关联统计Agent/模型版本及实际credential_id+revision组合，不再把同端点全部修订算作固定Run引用；knowledge按本人Run/evidence统计所引用快照/分块，IN集合去重。分块无created_at，使用证据检索截止限定，不补造时间；全部SQL先tenant/本人范围并包含软删除历史，只COUNT与ID/revision元数据，不读正文/密文或他人引用集合。既有tables.reason_code承载六个固定说明，存在已知引用追加FIXED_REFERENCES_PRESENT，未知依赖/法律保留和不可擦除标记保留。Web复用Collapsible/AppTable显示范围/数量/依赖意义，未知或旧未记录不当作零，新申请ID当次隐藏旧详情；原在途Run/未确认动作仍独立当前计数。旧报告/摘要不回填、不重算；无新字段/路由/表/依赖/迁移，治理源码纳入发布摘要。仅源码与差异审阅，未运行API/数据库/测试/类型检查/构建/浏览器，21.6与163/302保持未完成。
  - 证据上游保护补齐（2026-10-18）：reference_history扩展版本原文/标题/摘要/锚点/创建定位、快照固定列及索引代次快照/算法/摘要/编号定位；原审核字段、首次完整发布、已发布退役缩短有效期、版本/快照单向软删除、索引构建状态保留。已有发布事实不可覆盖，有效期不得延长/清除；旧状态未加载拒绝且不额外加载正文。0050接续0049为新三表安装ALWAYS行UPDATE/DELETE与TRUNCATE保护，复用原TRUNCATE函数，不改旧迁移或历史内容；降级先排他锁三表再判空，有历史拒绝。目录核对升级v2、六表十二触发器三函数及固定摘要，查询最多4函数/13触发器、每体16KiB，仍不验证其他触发器/权限/历史数据或执行修复。源码已在发布摘要范围，新增迁移明确暂存，无新表/依赖/API；当前65表/0050。仅源码及差异审阅、生成固定函数体摘要，未运行测试/类型检查/构建/预检/DB/迁移或实际数据操作；独立擦除/全部依赖盘点/恢复tombstone仍待，21.6/22.4及163/302不变。
- [ ] 21.7 将删除事实保存为可恢复 tombstone，备份恢复后先重新应用删除/隔离再开放访问。
  - 新增模型凭据修订恢复隔离（2026-10-20）：restore_credential_contracts/reapplication固定原credentials事实与精确修订定位、credential_id/revision/provider、账本/备份/恢复标记及不同写读身份，最长60秒请求；缺独立当前authority或该资源持久唯一意图审计即拒绝，共享修订整体范围及法律保留须独立批准，不从个人关联/管理员角色推断。七个明确元数据列SELECT，写身份额外仅UPDATE deleted_at，其他public表读写/密文/提示/撤销字段权限拒绝；共享维护try锁、0049/0050保护核对后锁原行，首次软删除且原时间保持，不改父凭据/其他revision/撤销历史。当前固定revision解封路径会拒绝软删除；提交后不同角色独立只读引擎核对，缺目标/仍存在不当成功，未知不重放，verify入口无写引擎。擦除/provider秘密撤销/恢复开放恒false，父密文/备份/已在内存的密钥仍需独立处理。整体8秒/复核5秒、SQL写1秒/读100ms、锁250/50ms及许可/审计各0.5秒，无清理硬截止保证。新源码纳入发布摘要，无新表/迁移/依赖，无默认提供者/维护配置/API或Worker，实际authority/该资源具体持久审计及完整恢复验收仍待。73表/head0058、169/302；未运行测试/类型检查/构建/迁移/DB/真实隔离或复核，21.7不勾完。
  - 知识隔离持久审计实现（2026-10-20）：restore_knowledge_audit/0058新增第73表restore_knowledge_events，显式独立PG引擎/审计角色/主体/原请求与当前authority，不默认接入API/Worker。每请求最多8条/每条8KiB，try事务锁、固定原恢复/请求/事实摘要与连续链，已有任何历史拒绝再领写意图；允许verify结果首记但之后也不能转为写入。仅本表SELECT/INSERT，其他public表读写拒绝；精确ALWAYS保护/ORM拒绝改删清空，有历史拒绝降级。追加0.45秒、SQL100ms/锁50ms，写前/提交前后复检当前许可，未知不重放；独立当前历史读取许可、只读REPEATABLE READ/2秒允许到期请求查证原链，不读目标或释放屏障。本人预览/发布摘要同步，覆盖下条具体唯一意图审计缺口；实际authority/维护身份/完整副本重施与恢复验收仍待，73表/head0058、169/302，21.7不勾完。未运行测试/类型检查/构建/迁移/DB或真实审计。
  - 知识快照恢复隔离实现（2026-10-20）：新增restore_knowledge_contracts/reapplication，原fact_v1/knowledge_snapshot定位、文档/版本/内容、DB与写/读角色摘要、账本代次/快照及恢复受保护标记在同一最长60秒请求中绑定。缺独立authority或持久唯一请求意图sink拒绝；当前apply许可须确认共享快照整体隔离获批、法律保留/范围和旧执行者停止/网络隔离，个人关联不授予共享资源写权。写身份仅可读两表明确元数据列、UPDATE snapshot.deleted_at与generation.status，限定原快照/最多128代次/10000声明分块，同事务首次软删除与失效代次、原时间保持，不清理分块或原文。提交后独立只读引擎/不同角色复用W21.5具体观察器复核，缺目标not_observed、未隔离needs_attention，只有匹配观察确认该读取投影；原擦除完成及恢复开放恒false。显式verify入口无写引擎，未知不重放。目录保护核对默认只读不变，新维护入口显式允许写事务中的目录SELECT复用原0049/0050定义校验，以避免复制保护逻辑；无DDL/关闭保护。新源码进入发布摘要，无新表/迁移/依赖，72表/head0057；未写或执行测试/类型检查/构建/数据库/迁移、真实重施/复核或擦除。实际authority/唯一意图审计提供者、维护身份和完整副本重施/开放验收仍待，21.7及169/302不勾完。
  - 新增登录会话资源隔离重施（2026-10-19）：restore_session_contracts/reapplication/history固定原事实/精确定位摘要/恢复标记/独立账本与最多60秒请求，缺可信当前authority/持久audit则拒绝。专用PG维护角色只可SELECT及两撤销列UPDATE，首次撤销原会话，已有撤销保持；提交后新只读事务复核，缺失/未知不当成功、无自动重试/擦除/屏障释放。0055为原AuthSession撤销单向/固定定位期限/DELETE/TRUNCATE加ALWAYS保护，避免恢复隔离后再次清除撤销；有历史拒绝降级，服务核对固定保护，不改现有登录注销实现。70表/head0055、169/302，其他资源/真实权威接线/擦除/开放验收仍待；未运行测试/类型检查/构建、迁移/DB/实际重施或复核，新文件明确暂存，设计第66节与运维/规格同步。
  - 新增启动屏障实现（2026-10-19）：restore_startup_barrier接API工厂/lifespan、Worker创建依赖前和preflight，normal无目录/无操作配置才放行，quarantined或保留目录一律拒绝；受保护root0444有界标记绑定原恢复操作/独立摘要，缺失/损坏/错范围不解除。新增只创建O_EXCL标记的标准库初始化入口、独立禁网络模板与强制quarantined的API/Worker/preflight覆盖，没有解除/批准/删除入口。启动前拒绝不代表实时围栏旧进程，实际资源重施与开放验收仍待，整项/169不虚勾；新模块进发布源码摘要，70表/0054不变。未运行初始化/CLI/启动/Compose、测试/类型检查/构建、预检/DB/迁移/恢复/擦除；设计第65节、运维/规格同步，不优化原快照实现。
  - 新增恢复核对实现（2026-10-19）：retention_tombstone_snapshot提供独立维护PG只读REPEATABLE READ全量有界快照导出，固定库/原session角色/public/origin、拒绝超级/RLS/表列写权限，4096事实/4MiB/8秒窗口，不生成或修复删除事实。新增标准库restore_tombstone_gate及独立禁网络/只读Compose，核对两份受保护快照/独立摘要、原备份/恢复目标/账本代次、行摘要、时效与读取变化；缺失/冲突/意外退出3，匹配只说明输入事实相符，访问/资源重施/真实性/最新性始终未证明。导出不接应用工厂，CLI不读环境/归档或连接DB；实际删除事实生成、独立账本配置、资源/外部副本重施及恢复启动屏障仍待，整项和169/302保持未完成。新文件明确暂存，未运行导出/CLI/Compose/数据库/测试/类型检查/构建/迁移或实际恢复/擦除；设计第64节、运维/规格同步，已实现部分不作优化。
- [ ] 21.8 测试非法主体、法律保留、重复申请、部分擦除失败、旧备份恢复与重新召回拒绝。
- [ ] 21.9 记录保留周期、擦除 SLA、批准/回滚限制和验证 Runbook；真实擦除另需明确授权。

## 22. W22 生产交付、备份恢复与条件容量建设

工程优先级：P1/P2；产品阶段：产品 P1 运维；HA P2 候选；模块：M02/M10/M11/M12。

依赖：W05、W20、W21。规格：[production-operations-and-retention](specs/production-operations-and-retention/spec.md)。覆盖：G34、G36。

完成标准：隔离环境可重复部署恢复与回滚；容量/HA 以批准负载证据启动。

- [ ] 22.1 确认发布环境、负载/SLO/RTO/RPO、租户配额和故障域，保留本地 Compose 用途不冒称生产方案。
- [x] 22.2 为 API/Worker/Web 提供可重复构建、运行与配置模板，使用既有 uv/pnpm 锁文件。
  - 实现证据：deploy/runtime 的后端/Web Dockerfile、Compose 与非秘密/受控环境示例，uv frozen/all-packages/no-dev/no-editable、pnpm@11.10.0/frozen-lockfile/原 web:build；批准的基础镜像 digest 为必填，Web 同源参数不携带秘密。明确 COPY/.dockerignore、非 root/只读/tmpfs、外部 DB/Redis、独立迁移 profile 与运行命令；只交付模板，不冒称逐字节重复、镜像构建/部署或生产验收。宿主 pnpm 版本查询有读取权限警告并回退 12.4.2，未据此改变项目固定版本或执行安装。
- [x] 22.3 定义迁移先后、单迁移执行者、就绪/存活、TLS/反向代理和 SSE buffering/超时配置。
  - 实现证据：migrate.py 在同 PostgreSQL 连接持 advisory lock、Alembic 复用连接，业务升级后官方 saver 初始化；拒绝空库/未知/0017 前基线，明确历史 0005 清空风险、旧进程先停止、0018–0040 顺序/API-Web 同步/旧 JWT 重登和禁止撤销语义回退。API live/ready、Worker 当前租约与进程边界及 60 秒停机余量明确；nginx 同源/loopback入口、外层固定域名 TLS 1.2/1.3 示例，双层无缓冲/缓存/gzip、10 秒心跳/75 秒超时与游标恢复。尚未执行迁移、容器、TLS/代理或故障验收；完整预检/smoke 仍待 22.4。
- [ ] 22.4 实现部署前配置/秘密/目标地址/Schema 校验和隔离 smoke，不输出连接串或凭据。
  - 已实现部分：deploy/runtime/preflight.py + 独立维护 profile，显式目标环境/禁止默认或占位秘密/关闭 bootstrap和Mock/provider 构造；PostgreSQL READ ONLY 事务核对代码/Alembic head、全部业务表/字段、独立 checkpoint 表；启用端点所有非删除版本的地址元数据按允许列表/DNS/私网复检（最多 1000 地址/单次 3 秒/整体 60 秒）、Redis ping。只输出固定错误码/计数/版本，不输出原异常、URL、连接串、密文或凭据，不写数据/调用模型。尚未执行预检或隔离 smoke；整项保持未完成，企业 provider/凭据内容/数据库约束/外层 TLS 也不以该脚本冒称验证。
  - 结构核对补齐：新增core.schema_structure，由现有preflight显式注入连接/Base.metadata/Alembic类型比较器，批量核对声明public表/字段类型、明确字符串长度、原生枚举标签、nullable及PK/普通UNIQUE/FK列/目标/动作/MATCH/NULL唯一性/时机。查实际validated、PG18+enforced、支撑索引valid/ready/live/unique/立即性、内部FK触发器启用及会话origin；仅只读系统目录，不读业务行/默认表达式或执行DDL。public有效search_path规则同时用于迁移，内置锁函数限定pg_catalog；原MIGRATION_LOCK_KEY数值保持并共用常量，preflight用共享事务try锁与原迁移独占锁互斥，忙即拒绝、结束释放，READ COMMITTED取锁后读新迁移快照；不能阻止绕开入口的直接DDL。声明表≤256/列≤10000/目录约束≤20000、SQL5秒/锁1秒/整体60秒，反射失败只固定码，不输出SQL/元数据原文。DNS固定子进程改-I/-B/空环境/关闭FD/cwd=/，不继承服务端秘密。封闭business_schema_structure_v1计数明确CHECK/部分与其他索引/业务触发器体/默认与生成/迁移独有保护/额外对象/权限及checkpoint细节未核对，局部passed不批准上线。无新依赖/表/迁移/API字段，既有镜像打包core；新文件明确暂存，设计28节/规格/模板/模块边界同步。未连接数据库、运行预检/测试/类型检查/构建/迁移/DNS或并发验收；实际smoke和未核对约束仍待，22.4保持未完成，进度163/302不变。
  - 入口与表定位复查补齐（2026-10-18）：current_schemas(true)必须恰为pg_catalog/public，拒绝仅false检查漏过的隐式pg_temp。preflight/migrate均先分派参数，帮助只打印说明、未知/混合参数不加载配置或连接依赖并固定退出2；无参数才进入实际操作，DNS仅保留原固定内部模式。人工中断固定退出130且不打印原始异常，迁移中断不证明未提交，不自动降级/重放。模板/Runbook/规格/设计同步，超时不承诺清理硬截止。仅源码与差异核对，按用户再次明确选择继续暂缓验证；未运行帮助/错误/中断/数据库路径或测试/构建，22.4与163/302保持未完成口径。
  - 会话执行配置一致性（2026-10-18）：抽取require_public_schema_context复用原路径规则及session_replication_role=origin，迁移在取锁/DDL前即检查，预检仍外层要求只读；不自动SET角色或启用触发器。复用既有SCHEMA_ENFORCEMENT_CONTEXT_UNSAFE，无新错误码/迁移/依赖，原锁key/模式不变。明确local在PG内部等同origin，固定只允许origin是模板配置约束，不混淆为local禁FK；官方边界已读，未连接数据库、执行角色/迁移/预检或测试/构建，22.4及163/302保持。
  - 历史保护专项结构核对（2026-10-18）：新增reference_history_schema接入现有只读共享维护锁事务，限定0049三表六个具名触发器/两个public函数；核对精确行/语句/事件、ALWAYS、无WHEN/列过滤/延迟/参数、正确函数OID、plpgsql/trigger/invoker/固定pg_catalog配置及固定迁移函数体SHA-256。只读PG系统目录，函数最多3条（正常2）、每体≤16KiB，触发器最多7条（正常6），缺失/停用/替换/漂移与不可读分别固定失败，不输出函数体、路径或原异常。schema.reference_history单独报告范围/计数，其他触发器/权限/历史数据/跨查询原子快照及未来DDL阻止均false，原通用triggers_verified=false保持；不调用函数、执行DDL或修复。源码纳入发布摘要、无新表/依赖/迁移；未执行预检、数据库、测试、类型检查、构建或故障验收，22.4/21.6和163/302保持未完成。
  - 具名CHECK状态补齐（2026-10-18）：目录查询扩至CHECK，ORM表级/列内声明须具名且同public表/名称/类型匹配，缺失/不符固定SCHEMA_CHECK_CONSTRAINT_MISMATCH；复用validated、PG18+enforced及约束时机检查与原20000条上限。只读状态、不读/执行表达式或业务行，不自动修复。报告升business_schema_structure_v2，新增具名数量与状态核对标志，表达式/部分索引等未知项仍false。静态源码发现50处具名CHECK且名称均见于迁移，不代表实际数据库具备；无新文件/依赖/表/迁移/HTTP契约，源码已有发布摘要覆盖。未运行测试、类型检查、构建、预检、数据库或迁移；完整约束与smoke仍待，22.4及163/302不勾选。
  - 声明唯一索引状态补齐（2026-10-18）：只读核对同public表/名称、键列顺序、NULL唯一性/部分标志与unique/valid/ready/live/immediate；缺失/漂移固定MISMATCH，状态沿KEY_INDEX_INVALID，不支持声明及重名明确拒绝，名称/目录各限1000。报告升business_schema_structure_v3并增具名索引计数/状态标志，完整定义与原部分/其他索引总标志仍false；不读谓词/表达式原文或业务行、不自动重建。静态检索8处声明/迁移名称，不代表数据库具备；无新文件/依赖/表/迁移/HTTP字段，源码已有摘要覆盖。未运行测试、类型检查、构建、预检、数据库或迁移，完整索引定义与smoke仍待，22.4和163/302不勾选。
  - checkpoint核心结构预检补齐（2026-10-19）：按`uv.lock`锁定的`langgraph-checkpoint-postgres==3.1.2`迁移形状只读核对checkpoint schema四表、23列类型/nullability、五个预期有/无默认值声明、四组主键及三条线程索引的valid/ready/live、非唯一性和精确单列定义（无表达式/predicate/include）；另核对包版本及迁移版本表0–9连续十条。缺失/漂移返回固定`CHECKPOINT_SCHEMA_MISMATCH`。报告给出核验到的迁移版本9及默认值存在性；默认表达式语义、权限/保护状态仍未验证，整体`checkpoint_structure_verified=false`。未读业务行、不改DDL/迁移/数据；未运行预检、数据库、测试、类型检查或构建，隔离smoke与未覆盖结构仍待，22.4不勾选。
- [x] 22.5 编制发布、非破坏性回滚、Run/Outbox 死信、模型/检索/工具故障和数据泄露响应 Runbook。
  - 实现证据：`docs/modules/observability/operations-runbook.md` 给出处理角色、事实/脱敏记录、发布与迁移顺序、旧进程隔离/优雅停机、保留扩展 schema/版本/撤销/回执的回滚、受控死信/attempt 恢复、模型/预算/索引/工具排查、审批/未知动作对账、Mock 客服接管及泄露响应；逐项关联现有接口和并发版本字段。区分文档交付与实际演练，未执行迁移/测试/部署/备份恢复；未批准 SLO/真实外部契约、备份和擦除实现不虚构完成。
- [ ] 22.6 配置业务库、checkpoint schema、知识/记忆文件与必要秘密引用的备份策略，Redis 通知可从事实重建。
  - 已实现部分（2026-10-18）：新增独立backup.compose.yml/backup.sh及三份配置示例，默认仅帮助，显式--create才导出专用数据库全部schema；一次custom归档保留业务/知识/动作/停止事实及同库checkpoint。独立非root维护身份/service/pgpass/CA，不注入应用秘密；固定TLS/只读连接选项、权限/来源声明、时限/文件上限、随机目录不覆盖，警告/失败保留未完成产物。只列归档目录核对迁移表/四张checkpoint表/PG主版本，记录大小/SHA-256和恢复引用清单前后摘要；manifest与完成标记flush，restore_verified=false，无恢复/调度/删除。Redis按事实重建，外部文件/角色扩展/provider密钥独立引用不自动复制。模板策略及源码已交付，尚缺批准的实际目标/镜像/存储/外部文件和密钥恢复引用/保留及RTO/RPO，整项不勾选；未执行脚本、Compose、备份/恢复、测试或构建。
  - W22.6离线产物核验入口已实现：backup_inspect.py仅标准库及独立禁网络/只读Compose，显式预期备份ID/目标别名/checkpoint/引用摘要；受控逐级路径/文件类型/权限、精确manifest/完成标记与实际大小/流式SHA-256、前后stat/条目核对及有限预算。只报告artifact_matched，恢复/来源真实性/可解压/引用语义/加密/外部文件均未验证，不改产物、不读连接配置或联网；新增文件明确暂存。此为22.6交付功能，未运行CLI、脚本、Compose、真实备份/恢复或测试，不能关闭22.7。
  - 恢复引用配置缺项检查已实现：同一入口显式--mode references和独立backup-references.compose.yml，仅只读挂载一份≤128KiB清单并按独立预期摘要核对；精确字段/类型、版本digest、秘密登记号、外部文件/记忆声明、法律保留状态及RTO/RPO格式，null或未配置返回固定字段缺项列表，矛盾/未知契约拒绝。configuration_complete只表示配置字段齐备，批准/引用目标/密钥恢复/加密/恢复/实际操作许可始终false，不解引用、不改备份脚本或历史产物；示例保持未配置。未运行命令、容器、备份恢复、测试或构建，实际策略输入仍待，22.6及163/302不变。
- [ ] 22.7 在隔离环境演练备份恢复、tombstone 重施、checkpoint/动作对账和恢复窗口，测量 RTO/RPO。
- [ ] 22.8 执行批准负载的阶梯/峰值/长任务测试，测量队列、DB 轮询、向量检索、存储与客户端 SLO。
- [ ] 22.9 按测量决定 SSE 通知优化、批处理/聚合/索引，保持事件游标恢复和统一指标范围。
- [ ] 22.10 HA 启动通过后实现副本/故障转移/隔离和 Worker 扩缩容；未批准保持 P2 候选。
- [ ] 22.11 验收单点故障、积压与配额公平性，避免恢复重复副作用或扩大租户范围。
- [ ] 22.12 生产远程部署、推送、破坏性回滚或真实数据操作单独确认授权；规划完成不等于已经部署。

## 23. W23 版本清单、评测发布门禁与灰度回滚

工程优先级：P1；产品阶段：产品 P0/P1 发布治理；模块：M04/M05/M07/M09/M11。

依赖：W00、W10、W19。规格：[agent-release-controls](specs/agent-release-controls/spec.md)。覆盖：G32。

完成标准：变更可比较、发布有门禁，灰度/回滚不原地改变旧会话或丢在途动作。

- [x] 23.1 扩展发布 manifest 关联 Prompt/图、模型/路由、工具/连接器、权限/审批、知识/检索及评测版本。
  - 实现证据：release_manifest_services 的严格 agent_release_manifest_v1，AgentVersion 发布同事务保存清单/摘要和最小审计；Agent 发布/草稿/授权修改统一 Agent 锁，目录按顺序共享锁复检，工具实现/实际 Schema/描述/最低风险/目录与绑定摘要、Prompt/图/源码/安装依赖、模型协议/生成配置/发布凭据 revision、grant/审批/非秘密配置均关联。知识明确当前 ACL 检索与逐 Run evidence 绑定、不虚构 corpus 版本；评测引用预留且明确 not_assessed/null/false，门禁与灰度实现见23.2–23.5。版本 API/Web 历史抽屉完整性检查与旧 null 展示，损坏清单隐藏且不能激活；0041 只加字段/成对约束，不回填历史且有记录禁止降级，迁移/测试/类型检查/构建未运行。
- [x] 23.2 将适用评测结果与版本摘要绑定，过期或不匹配结果不能批准新版本。
  - 实现证据：AgentReleaseEvaluation/0042 保存可信执行器 execution、固定 manifest、当前运行范围、批准策略/评测集/负载/执行器及严格报告摘要，数据库触发器拒绝覆盖/删除，有事实时拒绝降级。内部 record_release_evaluation 校验同租户/Agent/版本、执行时间、幂等执行 ID 和当前范围，不提供手工上传/通过接口。范围覆盖固定模型请求摘要、源码/工具/目录/凭据 revision、人员角色/部门/隔离项目权限，以及有界知识快照/ACL/索引/自然有效期；超限拒绝。最新报告损坏/过期/范围不符不能挑旧通过结果；W19 执行器及真实评测暂缓，当前没有任何通过事实，迁移未执行。
- [x] 23.3 实现功能/安全/质量/费用/延迟门禁与拒绝原因，禁止仅凭模型连通验证发布业务能力。
  - 实现证据：release_gate_services 按既有基线校验至少60样本、四主路径/Schema/引用/不伪造、六类零失败安全检查、P1 R4九阶段、七项质量阈值、完整实测USD平均费用批准上限和客户端首次正文/完整响应P95。缺失/失败/不适用分开，staging/production 无关闭开关并拒绝缺适用结果；local/dev/test 显式开发激活标记未验收，已知功能/安全/完整性失败仍拒绝。激活复用固定模型/工具当前校验，不改用最新模型/实现；固定模型最新探测须匹配当前凭据revision/配置，轮换后的旧通过、排队/失败不放行，旧无绑定证明仅开发未验收。Agent锁协调发布/授权/激活/停用，批准/拒绝追加最小审计。正式环境创建新会话再次检查，历史活动指针不能绕过；旧会话不重写。公开只读 readiness/SQL分页评测历史、OpenAPI 409包含检查结果，旧 activate=true 保留失败整体回滚。没有运行评测、测试、类型检查、构建或迁移，门禁实现不代表已验收。
- [x] 23.4 实现新会话按租户/主体/受控比例选择获准版本，旧会话保持固定版本。
  - 实现证据：release_rollout_services 固定租户/Agent/基线/更新候选/最多200个显式主体及策略摘要，主体哈希桶随同一灰度稳定；在Agent授权和行锁后只选择新会话版本，不授予权限、不重分旧会话。两版本须完整passed，任何环境都不使用开发豁免；策略未批准、巡检过期、固定引用失效时回到活动基线。初始最多10%、每次增加最多10个百分点、最高90%保留基线；全量须显式激活。0044保存会话灰度ID/轮次/组别/桶，成组非空约束及不可变触发器，不回填旧记录；未执行迁移或测试。
- [x] 23.5 增加灰度观测窗口、暂停/扩大条件与发布审计，不在 UI 隐式激活未评测版本。
  - 实现证据：版本化不可变策略、expected_revision、每次启动/恢复/扩大新增观察轮次；有界读取每组最多5001条以检测5000上限，只读费用/终态/结构化结果存在性/API首正文上界元数据，不读取正文。扩量须观察期、两组样本、无等待或取消、完整计量、绝对阈值和相对费用/耗时均通过；缺失不填零、不推断语义质量或客户端SLO。Worker有界轮转巡检，门禁/策略/基线变化、授权拒绝、观测失败或期满缺样本自动暂停，系统事件不伪造人工。策略默认未批准、无自动扩量；停止曾启动候选会永久撤回新写许可，激活同事务关闭灰度并按需撤回候选，原事实继续对账。两张表、追加式事件/不可变定义/停止不可复活及有历史拒绝降级已写0044；未运行评测、浏览器、测试、类型检查、构建或迁移。
- [x] 23.6 实现回滚停止新激活/新副作用而保留版本事实；在途审批/未知动作继续冻结与对账。
  - 实现证据：0043 为 AgentVersion 增加成对 withdrawn_at/by，回滚只撤回实际退出的活动版本，保留版本内容/会话/审批/动作/回执；数据库触发器禁止清除或覆盖撤回事实，有事实时拒绝降级。Agent锁内按版本ID先锁目标/前活动版本，门禁拒绝已撤回版本再激活，修复须新候选。统一 assert_action_release_allowed 覆盖原提案与执行来源版本：新提案/改参/申请批准/一次消费/领取/出站均复检；Mock目标同事务锁版本并复检动作/主体/参数/generation，和回滚串行，不把出站前检查外推成真实API原子保证。已运行/未知动作保留原generation并继续只读复核/有限对账，未消费审批由既有维护流程失效，不伪称回滚了已提交副作用。API/历史UI显示撤回事实，当前仍仅隔离Mock；迁移/测试/浏览器/类型检查/构建未执行。
- [x] 23.7 同步 Agent 管理的门禁结果、版本差异、灰度状态、禁用原因和明确人工操作入口。
  - 实现证据：固定清单/比较/发布门禁及实测阈值分页历史之外，历史版本“灰度发布”接通ReleaseRolloutDrawer、草稿策略表单和显式操作确认；显示SQL分页灰度/审计、固定版本/主体/摘要、当前门禁、两组计量/缺失/观测检查/巡检和禁用原因。表单change/submit校验、300ms Loading、陈旧响应隔离、错误重试、Toast、操作修订和停止撤回说明齐全；无手填通过、自动扩量或隐式激活入口。管理台保存候选activate=false，开发激活明确未验收。浏览器/测试/类型检查/构建未执行。
- [ ] 23.8 测试无评测发布、安全失败、版本摘要变化、灰度稳定性、历史会话和回滚在途动作。
- [x] 23.9 更新实际交付的 README/AGENTS/专题状态与回滚 Runbook，不提前标记未实现能力。
  - 文档交付证据：README/AGENTS/docs 架构、模块状态、数据模型、API 与 operations-runbook 同步至0044/56表，覆盖manifest/固定版本比较、追加评测绑定/当前范围/多维门禁/开发与正式环境边界、显式激活和回滚撤回/目标Mock串行。同步固定主体灰度、新会话选版、观察/系统暂停/人工扩量/追加审计和停止撤回；明确W19执行器及实测、测试23.8尚待，原清单/历史不补造，没有通过结果；迁移/测试/类型检查/构建未执行。本任务只完成实际交付文档，不宣称整个W23完成。

## 24. W24 条件模型路由与受控故障切换

工程优先级：P2；产品阶段：产品 P2 候选；模块：M04/M05/M09/M11。

依赖：W09、W19、W23。规格：[evaluated-model-routing](specs/evaluated-model-routing/spec.md)。覆盖：G37。

完成标准：启动证据通过才启用，新策略固定版本并记录每次真实模型，旧会话不漂移。

- [ ] 24.1 收集固定模型的质量/成本/容量/可用性瓶颈与目标收益，完成路由 go/no-go 决策。
- [x] 24.2 设计版本化路由策略和 Agent 配置升级，解析已验证候选模型版本集合，保留旧配置固定模型。
  - 设计证据（2026-10-19）：design第62节及evaluated-model-routing规格定义未来v3固定/路由联合、不可变策略/精确候选摘要、同租户版本关系/当前状态/最新探测和凭据revision解析、完整resolved集合与manifest v2、混合部署和旧会话/回滚边界。现有v2/空fallback/固定模型执行保持不变；这是设计交付，未实现新配置解析或路由运行，24.1及24.3–24.9保持未完成，无批准/模型探测/测试/构建。
- [ ] 24.3 实现能力、上下文、工具/结构化、数据地域与价格过滤，排除不合格候选。
- [ ] 24.4 实现每次调用的策略选择、预算预留和真实模型版本/原因记录，而非静默改 Run 主模型引用。
- [ ] 24.5 实现有限回退、熔断和恢复，撤销凭据/未验证模型不得作为备用。
- [ ] 24.6 处理流中失败的正文 replace/结果一致性，切换不重放已完成业务工具。
- [ ] 24.7 提供管理策略测试、费用/兼容提示与默认关闭开关，发布受 W23 门禁控制。
- [ ] 24.8 比较固定模型与路由的质量、安全、成本/延迟，收益不足保持候选。
- [ ] 24.9 测试历史会话、恢复 attempt、无候选、能力不符、额度不足和工具已产生副作用的回退。

## 25. W25 条件长期记忆治理

工程优先级：P2；产品阶段：产品 P2 候选；模块：M01/M03/M09/M12。

依赖：W09、W13、W19、W21、W23。规格：[governed-long-term-memory](specs/governed-long-term-memory/spec.md)。覆盖：G38。

完成标准：记忆有来源/确认/权限/期限，纠错删除可传播且不能覆盖业务事实。

- [ ] 25.1 确认跨会话记忆业务场景、收益/隐私基线与允许类型，未通过决策保持关闭。
- [x] 25.2 设计 MemoryRecord/Policy 的主体/租户、来源、确认、有效期、版本和撤销字段。
  - 设计证据（2026-10-19）：memory_contracts提供严格不可变MemoryPolicy/RecordScope/Record、本人消息来源、确认与撤销字段及纯政策绑定核对；固定租户/本人/Agent/原版本、修订/前ID与摘要、政策版本、正文摘要、来源和期限类，确认不可迁移到变更后的范围。空允许范围/缺失期限拒绝，候选/确认期限分开、撤回/替代/tombstone投影分开，批准/执行/召回恒false。完整字段/未来事务和当前边界见长期记忆方案第22节、design第63节与规格；不创建ORM/API/迁移或提取/召回，不改变运行配置，25.1及25.3以后仍待，未写或运行测试/类型检查/构建。
- [ ] 25.3 实现候选提取与用户/规则确认，模型推断不自动成为正式事实。
- [ ] 25.4 实现 ACL/有效期召回与上下文预算，区分记忆、正式知识和业务状态证据。
- [ ] 25.5 提供查看、确认、纠错、撤回和删除 API/界面与访问审计。
- [ ] 25.6 将记忆索引/缓存删除接入 W21 的擦除与备份恢复 tombstone。
- [ ] 25.7 实现过期/冲突/低可信记忆降级和按租户/版本禁用，禁用不删除产品事实。
- [ ] 25.8 评测错误偏好固化、跨用户/租户召回、删除后恢复和费用/质量收益。
- [ ] 25.9 通过门禁后按批准范围灰度，记录启用条件与维护责任。

## 26. W26 条件专业 Agent、独立互操作与额外渠道

工程优先级：P2；产品阶段：产品 P2 候选；模块：M01/M06/M09/M10/M11。

依赖：W08、W14、W19、W23。规格：[evaluated-specialist-orchestration](specs/evaluated-specialist-orchestration/spec.md)。覆盖：G39、G40。

完成标准：专业协作/新渠道有证据与边界，不产生无界委派、共享扩权或重复输入。

- [ ] 26.1 测量单 Agent 的上下文/专业质量/延迟瓶颈，明确知识/诊断/工单模块是否需要独立 Agent。
- [x] 26.2 定义子任务的输入/输出/证据契约、父子版本/Trace、租户/用户资源上下文和失败汇总。
  - 实现证据（2026-10-19）：specialist_task_contracts定义严格不可变input/output/aggregate v1、父主体/attempt/generation/父子固定版本与清单/Trace关联、只读资源和有界证据、共享预算范围声明/期限与未知用量。纯函数核对原摘要/版本/同资源证据/事实键覆盖，确定排序保留失败、取消、缺失和显式冲突，不投票、不确认业务成功；execution/write/预留许可恒false，不注册/执行Agent或修改单Agent主链路。design第61节与规格同步，26.1/26.3以后仍待，未写或运行测试、类型检查或构建。
- [ ] 26.3 实现子任务隔离上下文、父级共享预算与权限，默认只读并行，写动作统一进入网关。
- [ ] 26.4 实现最大子任务/委派深度/并行/超时与循环检测，失败或冲突明确升级。
- [ ] 26.5 评测专业化收益与费用/延迟退化，未达目标保持现有模块化单 Agent。
- [ ] 26.6 若确需独立远程 Agent，确定 A2A/MCP 职责与协议版本，实现身份、任务恢复/取消和互操作测试。
- [ ] 26.7 确认额外渠道/API 集成的业务范围与租户身份映射，未批准不连接消息系统。
- [ ] 26.8 获准渠道实施入站验签/幂等/身份映射、会话关联与受控进度返回，不默认发送外部消息。
- [ ] 26.9 验收跨域扩权、子任务冲突、委派循环、重复渠道事件、取消与人工接管。
- [ ] 26.10 灰度启用并同步组件/协议/能力目录、观测和维护责任，不默认拆独立微服务。

## 27. W27 外部知识来源、附件解析与条件自动更新

工程优先级：P1/P2；产品阶段：P1 批准来源；自动更新 P2 候选；模块：M01/M07/M10/M12。

依赖：W11、W12、W13、W20、W23。规格：[knowledge-source-ingestion](specs/knowledge-source-ingestion/spec.md)。覆盖：G23。

完成标准：来源与解析可追踪，增量导入生成待审核版本，自动更新不绕过发布。

- [ ] 27.1 选择首个获批准 CMS/文件来源并确认授权、增量 API、对象身份、内容范围与沙箱。
- [x] 27.2 定义 IngestionJob/游标/来源版本/解析摘要和失败状态，重复拉取不重复文档。
  - 实现证据：严格knowledge_ingestion_job_v1/page_v1与knowledge_ingestion_services、0047四表交付固定定义/摘要、本人创建幂等、owner/revision/generation/lease/累计预算、有限重试/取消/超时收敛、稳定对象绑定、草稿物化去重和不可变页回执。当前持久化适配明确限定本地精确清单的一页initial→complete，不保存原始游标或伪造远程秘密引用；远程continuation存储/来源删除仍待27.3及W20，未知版本不排序。当前会话/管理员/同租户来源/显式申请人、对象清单、负责人、来源与当前目标ACL映射及批准期限均检查；策略只能由可信服务端供给、默认None拒绝，没有API/模型自批入口。按来源锁和文档UUID锁序串行；新Job复用稳定文档与相同草稿意向，旧已被取代/撤回/删除候选冲突复核。同页先查已提交回执并核对候选摘要/当前目标权限，允许旧revision重放但不新物化；异参拒绝。草稿/绑定/物化/回执/审计/计数/检查点在同一保存点及外层业务事务，失败整页回滚，PG有限锁/语句等待和10秒页面窗口；提交前复检身份/时间/围栏。PG唯一约束、跨租户绑定与不可变触发器、终态不复活/有历史拒绝降级均已编写，未执行迁移或真实并发验收。源码纳入发布摘要，保留预览包含导入关联。当前64张ORM表/0047迁移源；本人任务管理API/UI见27.6，内部单次读取到草稿链路见27.3；运维文件提供者和人工创建/执行API/UI见27.3；实际来源批准配置、自动调度及27.9/27.10验证仍待，本轮未写/执行测试、构建、迁移或来源读取。
- [ ] 27.3 实现首个来源适配器与有限重试/大小/速率/出站控制，来源权限映射到知识治理范围。
  - 人工Worker入口（2026-10-19）：新增knowledge_ingestion_dispatch/0054及第70表knowledge_ingestion_execution_requests，默认关闭的本人POST/GET execution-request和Web API/types已接。每Job唯一，冻结原queued/零尝试修订、完整scope/原会话/代次，最多60秒且不超过原任务/来源/登录窗口，≤10秒剩余拒绝；仅当前独立读取+独立解析组合。Worker有界20候选/逐条SKIP LOCKED，提交claimed成功后才复用既有执行库，不读取旧排队Job或自动创建任务。领取永久消费，提交不明/崩溃/取消/过期不重派；queued到期拒绝，claimed到期unconfirmed，只查原Job/页回执，业务取消围栏沿用。PG ALWAYS与ORM保护固定范围/单向状态/领取/历史，有记录拒绝降级；最小审计/本人保留预览/发布源码摘要已接。新Compose覆盖只给Worker受保护批准文件和两专用socket，不给/source/容器启动权限；任务详情独立Worker意图面板/单独确认/查询已接，冻结原修订/范围/会话，请求序号隔离、共享忙状态和未知结果只GET；实际来源/配置及运行验收仍待，27.3保持未完成。遵循用户要求不优化已实现内容，本轮只实现此前缺失入口；未运行测试、类型检查、构建、迁移、容器或来源读取。
  - 已实现候选读取层：knowledge_markdown_source仅接受显式绝对目录和最多100个精确相对.md路径，固定清单/身份策略/适配器/解析契约摘要后才读取，不默认扫描或展开glob。固定标准库子进程逐级NOFOLLOW/目录描述符读取，拒绝符号链接/硬链接/特殊文件及同inode别名，读取前后核对文件事实；每文件1MiB、批次4MiB，读取与预览解析共享默认5秒、最多30秒预算，无自动重试/网络/应用环境继承。读取层只返回内存预览；27.2已另提供默认拒绝的Job持久化/草稿事务服务。knowledge_ingestion_runtime现连接内部单次执行：可信当前提供者固定scope/申请人/期限/精确清单与完整对象集合，读取前后和提交边界复检；短事务核对身份/来源/目标映射/围栏后释放全部锁才读一次，窗口不超过5秒和原许可剩余时间。当前执行才可记固定失败，领取不明不读取，页面提交不明只查原账本、不重读/重放。运维文件提供者与默认关闭的人工API/UI已另实现（见下条），无实际配置、默认目录或Worker/CLI执行入口，具体来源授权/ACL映射与OS隔离未确定，因此整项未完成；未读取文件或运行验收。
  - 人工接入实现：knowledge_ingestion_approval读取仓库外运维管理的单一local_markdown_approval_v1配置，固定本人/租户/来源/精确清单/实现及ACL摘要/期限；逐级NOFOLLOW、父目录可信所有者与不可组/其他写、单链接普通文件/受控所有者/无组或其他权限、128KiB、重复JSON/额外字段及读取变化拒绝。配置每边界重读，线程读取等待1秒，不构成独立OS沙箱。Settings默认knowledge_ingestion_enabled=false，开启须指定批准文件且只允许隔离local/dev/test，staging/production双层拒绝。人工GET /source-readiness返回本人单项封闭元数据（非列表），POST任务只接expected_scope_digest及UUID Idempotency-Key、固定600秒/一次尝试且不读知识；不足窗口create_available=false但已有任务可独立检查。GET /by-request按本人/租户/原请求键查询，404不证明在途未提交。POST /{id}/execute只接当前修订，queued/明确retry_wait及有效预算才执行一次，running含过期租约和终态不重领；未知创建/执行返回专属UNCERTAIN并只查账本。Web来源状态/期限不足禁用、创建确认/原键查询、详情另行确认执行、共享忙状态/禁重复/当前token与回执目标核对已接，未知不重发POST。无实际来源或批准文件配置，不启动Worker/CLI/自动调度或发布；源码进发布摘要，无新表/依赖/迁移，仍64表/0047。实际来源/ACL映射/隔离与27.9/27.10待，整项不勾选、进度仍163/302；未运行测试/类型检查/构建/浏览器/迁移/实际配置读取或导入，design第21节记录边界。
  - 创建恢复收尾：creationRequest.ts在POST前将最小UUID请求键/来源ID按tenant/user写入sessionStorage并回读；存储失败不发送，已有未确认或损坏记录不静默覆盖。关闭/刷新后恢复原请求查询，来源未配置/撤销或readiness错误也可查询，不恢复旧批准/文件数/期限；未知创建及404不清除、不另用新键创建。当前token/契约/来源及任务ID匹配后才清原记录，清除失败保留提示和查询入口，不把本地记录当已发送/已创建事实。仅同标签页恢复，不保证关闭标签或浏览器清理后保留，服务器本人授权/幂等及草稿去重继续独立生效。无新接口/表/迁移/依赖，源码及文档核对，未执行测试/类型检查/构建/浏览器或实际来源读取；总进度163/302不变。
  - 未知创建显式处置：0048新增追加式knowledge_ingestion_creation_stops，当前65张ORM表；本人GET /creation-requests/{key}区分unobserved/recorded/stopped，POST /creation-requests/{key}/stop要求严格confirm_stop=true。无原Job才保存停止事实，已有Job只返回，不取消；同主体锁和PostgreSQL双向触发器在READ COMMITTED下阻止同键任务/停止并存，其他隔离级别拒绝。摘要损坏拒绝，最小审计与停止同事务，保留关联预览计数但不授予删除。Web独立危险确认，核对原键/来源/契约及原任务或真实停止后才清会话定位，未知提交仅查询、不自动重发；来源不可用不阻止处理旧请求。没有实际来源配置或读取，未执行0048/测试/类型检查/构建/浏览器/并发验收，进度仍163/302，design第24节记录边界。
  - 读取期间目标并发保护：check_ingestion_execution记录固定对象集合/绑定、文档治理及最新候选/当前发布版元数据摘要，版本仅投影元数据不读正文；IO释放锁，commit在相同UUID文档锁序下先核对摘要再物化，缺失/变化明确停止本页，不基于新最新版本追加候选。当前有效本人执行可记录commit_conflict，其他提交不明继续只查原账本；旧页只读复用保留，不自动重读/恢复。启用配置与提供者要求PostgreSQL，服务在实际创建/领取/执行/提交/封锁事务再次要求PostgreSQL/READ COMMITTED，拒绝SQLite忽略行锁或其他隔离的旧快照；历史/取消/到期处置保留。无新表/接口/迁移/依赖，design第25节及模块/环境文档同步，未连接数据库、运行测试/类型检查/构建/迁移/实际来源或并发/性能验收，总进度163/302保持。
  - 人工执行范围与负责人收尾：任务元数据新增可空scope_digest，只在完整性通过时投影固定定义，不产生当前批准；Web执行须同时匹配当前readiness来源ID/范围摘要，确认发送前重查期限/次数/重试窗口，回执校验原范围，旧响应缺字段保守禁执行。来源当前active负责人用NOWAIT行锁保持到事务结束，IO仍释放锁、提交重查，避免负责人检查后停用及交叉锁等待，失败固定409。无新表/接口/迁移/依赖，API/Web需同步，设计第25节记录边界；本轮未运行测试、类型检查、构建、迁移、来源读取或并发验收，163/302保持。
  - 原请求独立查询：新增useIngestionRequestLookup/IngestionRequestLookupDialog，顶部按原UUID只读查询，来源未配置或sessionStorage丢失/损坏仍可用；UUID变更/提交校验、300ms Loading、AbortController/序号/当前token、错误重试/未观察到/停止/已生成状态及原详情重读均接入。复用原GET与回执校验，不创建/停止/执行或清除/替换待确认定位，不把unobserved/404/未知当未提交。Job投影creation_request_key仅原键已经是规范UUID时返回，旧任意非UUID键不透出/转换，坏记录为空；创建与查询校验Job内键，原有恢复仍严格匹配来源。无新表/接口/依赖/迁移，API/Web同步，新文件明确暂存；设计第26节记录边界。未执行测试/类型检查/构建/浏览器/迁移或实际来源读取，W27.3与总进度163/302保持。
  - 离线配置预检：新增knowledge_ingestion_preflight模块命令，必填绝对批准文件路径与独立核对的预期scope摘要；共用read_local_markdown_approval_file的逐层NOFOLLOW/所有者及权限/单链接/128KiB/读取稳定性/重复JSON/完整严格策略和当前实现清单绑定。固定结果码及退出码区分参数/文件/JSON/契约/范围/过期/不足窗口/无法确认，中断不重试；输出封闭JSON，路径/清单/人员/原文/异常不回显，不匹配隐藏实际范围。600秒创建窗口与服务/readiness共用常量；不实例化Settings或读.env/来源，不访问数据库/网络、启动读取/解析子进程、生成批准或任务，不提供执行许可；同步配置IO无硬截止保证。源码纳入发布清单、用法/边界文档同步，仅配置CLI不改变无CLI导入执行器边界；无新依赖/表/迁移，未运行命令/测试/类型检查/构建/迁移或实际配置/来源读取。design第27节记录；实际来源及27.3其余依赖仍待，总进度163/302不变。
  - 在线配置IO预算收尾：knowledge_ingestion_approval以进程级BoundedSemaphore(2)及独立daemon线程替换asyncio.to_thread，最多两个未结束的配置读取，不排队/占默认executor；原1秒等待保留，Future running/cancel桥接使请求超时/取消不释放实际IO占用，实际读取者finally归还。未开始且确认取消跳过读取，迟到结果不供其他请求或缓存，跨线程错误只传固定失败；额度满/启动失败立即沿原批准错误拒绝，默认关闭或禁用环境不启动线程。内核IO不可硬终止、上限按进程而非跨副本、离线预检同步读取不使用在线额度，实际文件保护与当前许可复检不变。设计21节及规格/模块/运行文档同步，无新文件/依赖/表/迁移/API字段；未启动线程、运行测试/类型检查/构建/迁移/实际配置或来源读取/故障验收，W27.3及总进度163/302不变。
  - 人工事务累计预算：readiness/创建、执行初读/领取、目标复检、失败记录及最终提交的外层事务统一INGESTION_TRANSACTION_TIMEOUT_SECONDS=10.0，覆盖连接池/累计SQL/配置边界查询/提交，原页面保存点复用常量。已领取阶段按原租约/Job/批准剩余时间裁剪，预览共用剩余时间函数并保持最多5秒；源IO不持数据库事务。失效来源停止不要求旧批准有效，仅按当前执行/Job期限和本人/围栏复检；readiness超时固定冲突，创建/领取/提交超时原事实未知只查询、失败记录超时不造终态，不重读/重放或重授许可。驱动取消/回滚/清理可额外耗时，不当硬SLA；设计20节/规格/模块/运维文档同步，源码已在发布摘要清单，无新文件/表/接口字段/依赖/迁移。未执行测试/类型检查/构建/数据库/来源读取/故障验收，27.3及163/302保持。
  - 本机API人工CLI（2026-10-18）：新增knowledge_ingestion_client/cli，复用既有readiness/原请求/Job/创建/执行/停止契约，不直连数据库或读取来源/批准文件。只接字面回环HTTP目标、有限非终端stdin JWT，禁DNS主机名/代理/重定向/压缩/连接重试；真实签名/会话/本人及来源批准由API复检。八操作分开、写命令须confirm，创建相关命令须本人预存规范UUID键，创建前查原请求并核对当前readiness/600秒窗口，已有Job只返回匹配历史；执行重读原Job/readiness，核对范围/revision/期限/尝试及初始单页，不重领running或终态。cancel/expire亦先查原Job/修订和服务器标志，不要求新来源许可；回执须分别为cancelled或带原到期/耗尽码的failed，原计数/创建键/范围不变。每命令最多三请求/一次POST，普通15秒/执行60秒/整命令90秒等待，stdin与清理无硬截止；写调用后任何非200/超时/中断/损坏回执固定outcome_uncertain/退出4，仅另行原键/Job查询，固定client_error_code与recovery_operation保留错误域/查询入口，输出管道失败也不重发；不自动重试、轮询、换键或保存令牌。响应64KiB/16层/4096节点、重复/非有限/未知根及嵌套版本/额外字段拒绝，回执绑定原目标，封闭报告不含名称/正文/URL/异常，客户端时间与API观察分开、退出0不证明业务成功。源码进发布清单，设计29节/规格/用法/模块/Runbook同步，新文件明确暂存；仍65表/0048、无新依赖或后台调度，默认关闭/正式环境禁止不变。未运行CLI、网络、API或来源，也未写/运行测试、类型检查、构建、迁移或浏览器验收；实际来源/批准和完整OS隔离仍待，27.3及163/302保持。
- [ ] 27.4 按实际需求逐项启用 Markdown/HTML/PDF/附件/OCR，先做依赖与安全评估，不一次引入全部解析栈。
  - 候选Markdown评估与实现：复用knowledge_content的正文规范化、SHA-256和章节锚点，无新增依赖；严格UTF-8/控制字符/大小校验，保守限制最多1000个#开头行。Markdown只作不可信文本，不渲染HTML、不执行脚本/front matter、不读取链接/图片/include；HTML/PDF/附件/OCR没有启用。design第14节记录依赖、路径/内容/权限和进程隔离边界；首个获准来源和实际类型需求尚未确定，本项保持未完成。
- [ ] 27.5 实现隔离解析、预览、消毒、原始来源/digest/锚点与草稿版本，禁止脚本/外链读取秘密。
  - 来源追溯界面（2026-10-19）：新增单候选provenance只读聚合及Web“来源追溯”入口，复用本人任务/完整页回执/物化/对象/版本摘要/候选最新性，比较与追溯共用文档锁内当前知识ACL/有效发布和原负责人/ACL映射检查。原始/规范摘要、对象/原任务范围/来源清单/固定读取与解析摘要、候选/原物化时间和完整持久化锚点可见；原始摘要仅历史回执、锚点标stored_candidate，不重新读取来源/正文或用当前源码替换历史。5秒有界只读事务、SQL先限锚点列、完整响应64KiB/1000锚点，超限或受限隐藏整份record；章节不单独分页。Web纯文本/空态/受限/错误重读、原会话/目标/序号核对、未知契约隐藏和翻页/刷新/治理写入清旧已接，不生成外链/批准/任务或存储秘密。新文件明确暂存，无新依赖/表/迁移，源码已在发布摘要；69表/0053。实际来源和完整隔离验收仍待，27.5保持未勾选；未写或运行测试、类型检查、构建、浏览器、数据库、迁移或实际来源读取。
  - 双配置离线预检（2026-10-19）：原knowledge_ingestion_preflight增加--reader-policy-file，隔离读取必填、旧adapter不适用且不读第二文件；复用运行时root0444/128KiB/逐级NOFOLLOW/前后事实及严格JSON/策略解析，核对完整scope、清单/物理根/两实现摘要，并重读双文件/当前源码变化拒绝。错配隐藏另一范围，报告增加匹配策略摘要/期限/checked，创建窗口按两份较早截止及600秒，专属固定错误区分缺失/不适用/文件/JSON/契约/范围/到期/变化；不读来源/环境或连接DB/socket/网络，不生成批准/Job、启动容器或授予执行许可。顺序复核不保证跨文件原子快照；新读取器源码变化需新批准，旧v2/v3字节摘要保持。无新文件/依赖/表/迁移，69表/0053、165/302；实际来源/配置与完整隔离验收仍待，27.5不勾选。未写/运行测试、类型检查、构建、预检命令、迁移、容器或实际配置/来源读取。
  - 同轮物理根绑定：应用批准文件和读取器独立策略同时必填严格source_root_device/source_root_inode；新isolated_selection_v1摘要绑定/source、精确清单及这两个整数，再进入scope.source_contract_digest，读取时目录描述符必须匹配。挂载换源须新范围/批准/Job，不能仅沿用/source逻辑名；旧adapter禁止物理根字段，旧v1摘要字节保持不变。只改代码与契约，未读取实际挂载或生成批准。
  - 独立来源读取路径（2026-10-19）：新增knowledge_reader_isolated与10006独立镜像/Compose，固定/source单独只读挂载、拒绝嵌套挂载和statvfs复检，原NOFOLLOW/单链接/普通文件/别名/文件变化/1MiB+4MiB限制保留。API仅挂socket，10001/10006双向SO_PEERCRED；4096字节请求只含范围/清单/两实现摘要、nonce和原剩余期限，路径来自固定root0444/128KiB独立策略，启动/读取前后核对范围/策略/期限，响应绑定原请求和全部摘要。新isolated_reader_v1只配当前隔离解析，批准提供者/来源服务/预览统一核对组合，清单摘要算法与一次帧/身份/资源内核复用；原v2/v3字节摘要保持，已有隔离源码变化须新scope/Job，不改历史或失败回退。父进程仍核对原始/规范摘要与候选，提交重新授权，来源IO不持DB事务。独立镜像/默认关闭/正式环境禁止与两端资源/单次/未知不重试边界保留，无新依赖/表/迁移，69表/0053、165/302。实际来源/两份批准与完整隔离/故障验收仍待，27.5不勾选；未写/运行测试、类型检查、构建、Compose检查、迁移、容器或实际策略/来源读取。
  - 独立身份解析路径（2026-10-19）：新增knowledge_parser_isolated、knowledge-parser.Dockerfile/Compose覆盖。10001应用经固定Unix socket向10005单次解析器只发有界base64正文，无来源路径/主体/DB/模型或凭据；双向SO_PEERCRED、10005:10001目录0770/端点0660、长度帧、nonce/原请求SHA/解析源码摘要绑定及当前源码复检。新local_markdown_isolated_v1需显式独立批准完整scope，批准提供者/来源服务/预览统一识别，读取前检查端点与源码，读取和IPC共用原deadline；失败不回退v3，原v3摘要/批准/任务/回执不自动改写。镜像只含标准库和三份固定源码，清空环境/-I/-S/-B、network none/只读根、CPU2/3秒/32FD/16进程/256MiB/core0前置约束；首认证连接前最多60秒，一次处理后退出，无自动重启或赋应用Docker权限。父进程继续核对原始/规范正文摘要、字节数/对象/锚点/批次限制，提交原授权不变；旧端点拒绝，清理仅自身同inode。源码进入发布摘要，无新依赖/表/迁移，69表/0053、165/302；实际来源、读取端OS隔离、完整隔离/故障验收仍待，27.5不勾选。未写或运行测试、类型检查、构建、Compose检查、迁移、容器或实际解析/来源读取。
  - 部分预览能力：内存结果绑定scope/清单/跨Job对象键、原始与规范正文摘要及不可变锚点，路径/正文不进入默认repr和固定错误。读取子进程不继承环境或额外文件描述符，但沿用同一OS用户，不声称UID/挂载/网络沙箱；预览正文规范化已分进程（见下条），写入领域校验保留，尚无完整OS隔离/HTML消毒或实际来源授权，人工API/UI见27.3。27.2草稿物化与内部单次读取执行库已连接，默认缺少可信批准提供者拒绝；固定失败不提交草稿，取消/租约变化或提交结果不明不伪造终态，未知提交只查回执。实际来源/批准配置、OS隔离和自动调度仍待；未读取来源文件或执行测试/构建/迁移，本项未完成。
  - 预览解析进程实现：knowledge_markdown_source将文件读取和正文规范化/摘要/章节解析分为两个固定-I/-S/-B进程，空环境/关闭额外FD/本机管道，读取与解析共享默认5秒/最多30秒的单调deadline，第二阶段不重置预算，实际Job还裁剪原许可剩余时间。解析通过受控SourceFileLoader只加载SHA核验的部署knowledge_content源码字节，不接受旧pyc或来源指定代码；复用现有解析函数，CPU软2/硬3秒、最多32FD（不提高继承软硬上限）、禁core/字节码写入，原1MiB/4MiB/1000章节边界和8MiB输出拒绝保留。父进程核对封闭响应/对象数/原始摘要与字节数/规范摘要/批次大小/锚点字段，不在预览重做规范化；知识草稿写入领域校验保持。进程异常/超时/取消不返回部分成功、不自动重试；解析输出超限映射output_too_large、源码不匹配source_unapproved、解析器不可用source_unavailable，其余parse_rejected，不泄露正文/路径/解释器异常。解析manifest升local_markdown_plaintext_v2并绑定程序/复用源码，旧批准及Job不静默升级，后续需新批准范围/任务；历史保留。无新依赖/表/迁移/文件，完整UID/挂载/网络/内存隔离与实际来源仍待，W27.5不勾选、总进度163/302不变。仅源码核对，未启动进程、实际读取来源或运行测试/类型检查/构建/迁移/故障验收；design第23节记录边界。
  - 管道上限收尾：父进程改为并行有限输入/分段输出/进程等待，最多64KiB一段，读取6,000,000字节与解析8MiB上限在接收期间触发，超限不先缓冲完整stdout。失败/取消回收兄弟协程并终止未退出进程，继续共享原截止时间，READ/PARSE输出超限统一映射output_too_large；不改变解析源码、批准契约或实际来源开关。源码与设计/规格/模块文档同步，未运行测试/进程/实际来源读取或验收，W27.5及总进度保持不变。
  - 程序资源与回收收尾：knowledge_markdown_source的固定读取/解析程序共用_PROCESS_LIMITS_PROGRAM，CPU软2/硬3秒/最多32FD/core0，不提高继承上限；读取manifest升local_markdown_reader_v2，解析因固定程序共用限制变化升local_markdown_plaintext_v3，旧批准/任务不自动沿用或改写，需新范围独立确认。父进程BoundedSemaphore(2)限制未回收程序、不排队；PROGRAM_LIMIT_REACHED映射已有rate_limited，由当前有效执行记失败，不自动重试。独立监督Task强引用/shield与stop Event使调用方重复取消不打断清理，启动前停止不启动、启动中停止取得句柄后不发输入；先关闭输入/尝试终止再回收协程/丢弃输出/wait，确认退出或明确未启动才归还额度，未知启动/回收保留占用，后台异常只取走不打印原文。不继续下一阶段或写Job，不把请求取消当业务取消，原共享deadline/输出上限/领域校验/当前许可保留。仅按进程的程序限额，不等于跨副本/整个Run内存/完整OS隔离或强制停机/内核IO硬保证；设计23节/规格/模块/运维与当前版本文档同步，源码已进发布清单，无新文件/依赖/表/接口字段/迁移。未启动程序、读取实际来源或执行测试/类型检查/构建/迁移/取消/并发验收，27.3/27.5和163/302保持。
- [x] 27.6 提供增量更新差异、冲突、待复核、负责人和过期提醒界面，审核后才更新正式索引。
  - 已实现任务管理部分：GET /v1/admin/knowledge/ingestion-jobs及/{id}只返回当前管理员本人申请的封闭元数据投影；先tenant/requested_by/状态/来源SQL过滤再count及created_at/id稳定分页，复用默认20/最大100分页。损坏定义/状态只返回unknown/integrity_failed并隐藏范围、计数及操作，不补造失败或当前时间。POST /{id}/cancel和/expire严格expected_revision，当前会话/角色和本人复检；显式取消围栏及到期/耗尽终态保留已有草稿/回执/审计，没有读取来源/重试/发布副作用。200及专属401/403/409使用no-store，登录失效固定401/Bearer，越权资源固定404；源码已接错误契约。Web /knowledge/ingestion-jobs从知识列表进入，状态筛选/服务端分页/刷新、详情抽屉、计数/失败/预算/期限、显式确认和操作Toast接通；300ms Loading/空态/权限/错误重试、请求序号/主体切换重置/当前token核对与未知契约禁写均已实现，失败只刷新查询、不重放写请求。本轮新增knowledge_ingestion_review与GET /{id}/drafts标准SQL count/分页（默认20最大100、created_at/id降序）及单项comparison有界聚合详情；只列本任务首次创建物化ID/时间，复用仅计数。比较核验页回执/对象/意向/版本摘要，文档锁下复检当前知识ACL/有效发布、最新候选和负责人/ACL映射，序列化前复检会话/自然到期；管理员/本人任务不授予正文，首次草稿无有效发布也受限。64KiB/每侧4000行线性共同首尾替换差异不截断、不渲染/读外链，不声称最小编辑/语义冲突；失败固定状态并隐藏范围。Web SQL分页/显式纯文本比较、未知契约隐藏、待审/负责人ID/已有复核时间与治理链接已接，刷新/翻页清旧正文、请求序号/主体重挂载/当前token/回执绑定。源码纳入发布摘要，无新表或依赖。负责人/日期配置、页面到期提示与显式候选撤回另见后两条，默认关闭的来源人工创建/执行入口已接（见27.3），实际来源配置仍待，27.6按当前本地单页界面实现计完成。未运行API/Web/类型检查/构建/迁移或浏览器验收，仍64表/0047。
  - 本轮复核安排实现：knowledge_ingestion_governance及GET/PATCH /{job_id}/drafts/{id}/governance，复用共同本人任务/回执/物化/最新候选校验，只返回控制面元数据；首次无发布/ACL草稿也可安排但不读正文。严格必填治理摘要/同租户active负责人/带时区日期或显式null，拒绝多字段/数字时间；摘要绑定文档updated_at/状态/发布/负责人/日期、候选审核/有效期/内容元数据/ACL，变化409。PG有限锁/语句、先UUID有序锁申请人/目标负责人再锁文档，写前/提交当前会话与管理员复检；复用知识更新与最小Job/物化/前后摘要审计，同值不重复变更，不改ACL/审核/索引。Web独立面板/设置弹窗，/governance/owners先tenant/active/转义姓名过滤再SQL count及display_name/id分页（默认20最大100），只投影姓名/ID，不读凭据或传联系方式/角色；日期即时校验与精度保留、未配置/未到期/已到期提示按服务端observed_at，保存绑定原token/范围、清旧差异并禁关闭/重复提交，失败只GET核对。页面提示不代表后台调度或外发消息，更换负责人需独立复核原导入映射；显式候选撤回见下条，实际来源链路仍待其他W27任务，27.6按当前本地单页界面实现计完成。无新表/依赖/迁移，源码进入发布摘要；未写/执行测试、类型检查、构建、迁移或浏览器验收。
  - 本轮显式处置：POST /{job_id}/drafts/{id}/withdraw只接受严格当前治理摘要及本人选择的三种原因；当前管理员/会话、本人任务/创建人/回执/物化/最新候选、当前正式版本/ACL/负责人/日期/审核范围均复检，变化409，已发布/删除/归档/旧候选拒绝。withdraw_knowledge_candidate专用退役不扩展通用_transition或原retire API；置retired并增加review_revision、更新文档updated_at，保留正文/审核事实/导入回执，不改正式发布指针/ACL/有效期/索引。知识撤回与Job/物化/原因/审核修订/前置摘要两类最小审计同API事务；GET只在未发布退役状态与唯一匹配审计证明时显示candidate_withdrawn，否则隐藏，提交证明不匹配整笔回滚，旧退役不补造。Web禁用条件、封闭原因选择/危险确认、绑定原token/范围、清旧比较/共享忙状态、回执契约/目标/终态核对及未知只GET查询均已接，不自动重放/恢复/批准。W27.6差异/冲突状态与保留正式版本撤回候选、待复核、负责人、页面到期提示按当前本地单页模式已实现，勾选不代表真实导入启用；实际来源批准/执行配置、远程/完整解析/跨任务对象管理及27.9/27.10和W28验收仍待。无新表/迁移/依赖，本轮未写或执行测试、类型检查、构建、迁移、浏览器验收或实际来源读取。
- [ ] 27.7 自动知识更新启动前确认收益、来源可信度、发布权限和误更新回退门禁。
- [ ] 27.8 获准后运行定时检测与候选草稿生成，默认不自动发布或修改 ACL。
- [ ] 27.9 测试中断恢复、来源删除、权限撤销、恶意附件、重复/乱序更新与成本限制。
- [ ] 27.10 验收来源故障、错误更新回退和审计，并同步维护频率及责任人。

## 28. W28 分阶段集成验收与文档收口

工程优先级：P1；产品阶段：按本次发布范围验收，P2 不阻塞 P0；模块：M01–M12。

依赖：W00、W19、W23。规格：[support-quality-evaluation](specs/support-quality-evaluation/spec.md)、[agent-release-controls](specs/agent-release-controls/spec.md)、[production-operations-and-retention](specs/production-operations-and-retention/spec.md)。覆盖：G01、G42。

完成标准：发布子集有完整证据，未启动/P2/阻塞项仍清楚保留，历史状态不被虚假更新。

- [x] 28.1 为本次交付子集生成需求/任务/规格/实现/测试映射，未选工作流保持未完成。
  - 本轮交付映射：G05/W01→api-contract-completion→三个列表服务/API/Web；G02/W02→enterprise-authentication-hardening→空登录输入及客户端产物；G09/W03→runtime-reliability/agent-health-computation→Worker 调度/心跳/停机；G10/W04→runtime-reliability→Outbox 模型/迁移/服务/API。各完成任务下列出代码证据；1.5、1.6 验收、3.7、3.8、4.3 验收、4.9 等测试仍未执行。
- [x] 28.2 检查历史 20 项唯一验收归属：通过实际证据才更新原 change；未完成项不在总规划重复计完成。
  - 核对证据：0.2/0.3及README两张逐项核对表覆盖知识四项与Dashboard十六项，明确源码已有/用例局部覆盖/旧契约待适配/真实验收缺口，以及11.1/11.2/11.6–11.8/12.10/18.8–18.23唯一映射；知识5.3生命周期与索引为同一原任务的两个验收子范围，须均满足才关闭原项。原两个change/tasks未修改，未将已有API桩/归档声明当本次通过结果；当前总规划中实现任务完成只说明代码交付，原20项测试/视觉/真实环境验收继续保留未勾选。本项仅完成归属核对，不宣称旧验收完成。
- [ ] 28.3 运行受影响 uv/pnpm 静态/类型/单元/API/Web/构建与迁移校验，区分原有失败和本次引入问题。
- [ ] 28.4 执行本次范围的真实隔离 PostgreSQL/Redis、多 Worker、SSE 和外部 Mock/沙箱链路验收。
- [ ] 28.5 核验功能、安全、质量、费用、延迟、恢复与数据治理门禁，安全失败阻断发布。
- [x] 28.6 对未确定外部契约与未通过 P2 启动决策保留阻塞原因/责任角色/候选任务，不伪造部署进度。
  - 交付证据：设计“待定决策”保留IdP、工单/日志、embedding、审批资源、Vault/KMS、容量恢复、留存擦除及P2启动所需资料/责任角色/精确任务ID；README W00–W28责任/证据/入口逐项补齐。当前明确用户暂缓测试/向量、真实目标/服务身份/沙箱未提供、留存/法律保留/容量与SLO未批准、W19无实测；路由/长期记忆/专业Agent/外部渠道/自动更新未通过go/no-go，继续保留候选与默认关闭。0.5仅既有内部Mock基线、21.2仅预览、23仅门禁/灰度实现，均不冒称企业批准/实际擦除/通过评测/生产部署。28.3–28.5/28.8及实际依赖任务保持未完成，角色未任命时不虚构人员。
- [x] 28.7 同步 README/AGENTS/docs 模块状态、接口路由、表清单、验证边界与目标图的现状说明。
  - 最新范围（2026-10-17）：3应用+1核心/12模块/65张ORM定义，迁移至0048未执行；当前会话/身份代次、关键词索引/证据/候选文件预览与有限导入持久化/默认关闭人工入口/未知创建封锁/读取目标摘要复检、运行/调用预算/来源证明、Mock诊断/动作/非本人审批/客服接管/受控补偿提案、观测/告警、清单/评测门禁/灰度、本人数据保留草稿/申请预览和交付模板对应代码/路由/文档已同步。外部契约、实际来源批准、完整解析隔离、W19可信评测/实际通过结果、测试/类型检查/构建/迁移/生产部署仍待；目标图不充当完成清单，后续交付继续维护。
- [ ] 28.8 运行相关 change 的 OpenSpec 严格校验，确认无断链、重复 ID、孤立需求或已勾选未验收任务。
- [ ] 28.9 仅在用户明确授权且范围实际完成后考虑提交/部署/归档；目前已授权实施，未授权提交、部署或归档。
