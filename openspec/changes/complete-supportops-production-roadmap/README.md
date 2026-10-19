# SupportOps Agent 全项目待处理规划

基于 [项目级 AGENTS](../../../AGENTS.md) 的 2026-10-12 初始快照和 [目标架构图](../../../architecture-diagrams/enterprise-support-agent-architecture-2026-10-12.png)，本 change 将待修复、待建设、待验收和有门禁候选项集中为 **29 个工作流、302 项任务、42 个需求覆盖项和 22 份能力规格**。

当前已开始逐项实现，完成标记与实现证据见 tasks；测试任务按用户要求暂缓，混合实现/验收任务记录部分进度但保持未勾选。302 项包含对旧 20 项待验收工作的承接/核对，不应再把“302 + 20”理解为独立新增工作量。规格和任务生成完备不代表任何产品阶段已完成，旧 change 的测试完成状态保持不变。

## 阅读与实施入口

2026-10-18 工作区实现快照：3个应用+1个共享核心包、12个职责模块、65张业务ORM表，迁移源截至0048且尚未执行。以下按当前模块明细/调用入口核对差距，包含未提交代码；源码存在不代表测试通过或生产验收。旧两个change的20项验收勾选未修改，测试及实测继续暂缓。

| 模块 | 当前代码证据（根路径为 `packages/support_core/src/supportops_core/`，应用入口另标） | 已实现范围与仍需处理 | 缺口回链 |
| --- | --- | --- | --- |
| M01 Web | `apps/web_chat/src/router/index.tsx`、`api.ts`、`types.ts`及account/approvals/mock-tickets/analytics/agents页面 | 会话安全、审批/客服、告警、发布门禁/灰度界面已有；真实浏览器/可访问性验收未执行 | G01/G02/G03/G19/G27/G28/G30/G40/G42 |
| M02 API | `apps/api/src/supportops_api/main.py`、`errors.py`、`schemas.py`、`pagination.py`及routes | 当前路由/分页/稳定错误/身份/健康契约已有；旧验收、部分路由服务化仍待 | G01/G05/G06/G07 |
| M03 身份 | `auth.py`、`auth_session_services.py`及API auth/admin_identity | 登录会话/安全代次/撤销及运行边界当前授权已有；SSO/委托身份未批准 | G03/G04/G14/G16 |
| M04 模型 | `model_services.py`、`model_probe_services.py`、`credential_services.py` | 固定版本/凭据、独立探测job/租约/恢复已有；真实故障/网络验收、路由启动证据待处理 | G08/G11/G33/G37 |
| M05 Agent | `agent_services.py`、`release_manifest_services.py`、`release_gate_services.py`、`release_rollout_services.py` | 清单/比较/多维门禁/稳定新会话灰度/撤回已有；W19执行器/实测未接通，默认不能灰度 | G14/G15/G32 |
| M06 工具/动作 | `tool_gateway.py`、`tool_result_services.py`、`tool_context_services.py`、`redaction.py`、`outbound_http.py`、`action_runtime.py`、`approval_services.py` | 本地许可/严格输出Schema/全部文本脱敏/来源复检、隔离Mock动作/审批/对账已有；固定目标HTTPS出站基础已接OTLP，真实API/MCP/目标幂等及授权契约待批准 | G15/G16/G25/G26/G27/G33/G40/G41 |
| M07 知识 | `knowledge_services.py`、`knowledge_index_services.py`、`knowledge_retrieval.py`、`knowledge_ingestion_services.py` | 生命周期/非本人审核/关键词索引/ACL证据链及本地清单候选预览/有界导入持久化/本机API人工CLI已有；向量暂缓，来源批准/实际接线/语义正确性与旧验收待处理 | G20/G21/G22/G23/G35 |
| M08 会话/结果 | `services.py`、`support_result_services.py`、`escalation_services.py`及API conversations | 幂等输入/完整产品结果/当前引用/升级包及固定版本已有；刷新/断线/取消和真实流程验收待处理 | G10/G12/G13/G19/G28 |
| M09 推理/上下文 | `agent_runtime.py`、`context_services.py`、`history_context_services.py`、`model_budget_services.py`、`diagnostic_workflow.py` | 单Agent、来源摘录/工具证明/时效/冲突及物理账本、固定Mock诊断已有；精确计量/性能/安全实测未执行，路由/长期记忆/专业Agent保持候选 | G11/G12/G17/G18/G24/G37/G38/G39 |
| M10 Worker | `apps/agent_worker/src/supportops_worker/main.py`、`worker_runtime.py`、`run_attempt_services.py`、`run_outbox_services.py`、`run_scheduler.py` | 独立维护/心跳、Outbox、租约/有限恢复、等待/对账、租户轮转和灰度巡检已有；多Worker/真实依赖故障验收待处理 | G08/G09/G10/G11/G12/G13/G27/G36 |
| M11 观测 | `business_trace_services.py`、`dashboard_query_services.py`、`runtime_alert_services.py`、`telemetry_services/telemetry_export`及API admin_dashboard/admin_runtime_alerts | 本轮计量/阶段/安全审计/告警/完整聚合/排行与筛选已有；单租户受控OTLP维护导出默认关闭，联调/外部渠道/正式SLO和独立评测平台待处理 | G29/G30/G31/G32 |
| M12 数据/交付 | `models.py`、`config.py`、`secrets.py`、`credential_services.py`、`migrations/`、`deploy/runtime/` | 65表/受控补偿提案/本人保留预览/软删除/本地凭据provider/永久撤销/显式交付预检模板已有；迁移/构建/smoke/备份恢复、实际清理/擦除及真实provider尚待 | G33/G34/G35/G36 |

核对已修正根AGENTS中的旧结论：关键词未接入运行、排队Run未实时授权、尚无Mock诊断图/动作/审批等待，均不能继续作为待建设项重复实施。剩余差距以任务证据、真实外部契约、用户暂缓测试及独立启动门禁为准。详细安全/验证边界仍见根AGENTS M01–M12及第6、8节。

- [proposal](proposal.md)：范围、能力与影响。
- [design](design.md)：技术决策、依赖边界、迁移与待定问题。
- [tasks](tasks.md)：全部可勾选任务，ID 为 X.Y，工作流为 W00–W28。
- `specs/<capability>/spec.md`：19 个新能力与 3 个已有能力的 delta，要求与场景用 SHALL/MUST + WHEN/THEN 表达。

先完成 W00 的实际基线核对，再按交付范围选择工作流。不要直接执行全部候选集成；采用新依赖、外部系统或部署前先满足对应决策/授权。实现后的证据和状态回写原归属任务，新总规划保留引用。

## 工作流与依赖

工程优先级 P0/P1/P2 不等于产品阶段 P0/P1/P2。依赖是交付约束，契约定义/测试准备可前置。等待真实系统的任务先完成 Mock 与契约；启动门禁未通过的候选保持未勾选，不能用“决定暂缓”冒充功能完成。

| 工作流 | 内容 | 任务数 | 工程优先级 | 产品阶段 | 交付依赖 |
| --- | --- | ---: | --- | --- | --- |
| W00 | 基线核对、范围与历史任务归属 | 8 | P0 | 全阶段规划 | 无 |
| W01 | API 分页、错误与跨层契约补齐 | 9 | P0 | 产品 P0 基础 | W00 |
| W02 | 登录、会话安全与企业身份接入 | 10 | P0/P2 | 基础 P0；企业身份按 P1 需求启动 | W00 |
| W03 | Worker 独立心跳、调度与停机 | 8 | P0 | 产品 P0 基础 | W00 |
| W04 | Run 通知 Outbox 与持久化模型探测 | 9 | P0 | 产品 P0 基础 | W00、W03 |
| W05 | Run 租约、执行尝试、恢复与取消 | 12 | P0 | 产品 P0 基础 | W03、W04 |
| W06 | 同会话执行顺序与输入快照 | 8 | P0 | 产品 P0 基础 | W05 |
| W07 | 实时授权、风险映射与工具额度 | 11 | P0 | 产品 P0/P1 治理 | W00、W05 |
| W08 | 统一工具网关与受控协议连接 | 11 | P1 | P0 本地/Mock；P1 外部连接 | W07 |
| W09 | 证据上下文与完整运行预算 | 10 | P0 | 产品 P0 基础 | W06、W07 |
| W10 | 结构化产品结果与聊天恢复 | 8 | P1 | 产品 P0 可信回答 | W01、W06、W07 |
| W11 | 知识生命周期剩余验收与来源治理 | 9 | P1 | 产品 P0 知识基础 | W00、W01 |
| W12 | 知识分块、Embedding 与索引消费 | 10 | P1 | 产品 P0 知识检索 | W04、W11 |
| W13 | ACL 检索、证据组织与引用校验 | 10 | P1 | 产品 P0 可信知识回答 | W07、W08、W09、W10、W12 |
| W14 | 429 诊断编排与只读业务工具 | 11 | P1 | 产品 P0 Mock；P1 真实诊断 | W08、W09、W13 |
| W15 | 业务动作账本、幂等、复核与对账 | 11 | P1 | P0 低风险 Mock；P1 完整动作 | W05、W07、W08 |
| W16 | 持久化人工审批与恢复工作台 | 13 | P1 | 产品 P1；仅隔离 R4 演练 | W05、W07、W10、W15 |
| W17 | 工单闭环、升级包与客服接管 | 11 | P1 | P0 Mock 建单；P1 真实工单全流程 | W14、W15 |
| W18 | 运行观测、健康口径与 Dashboard 剩余任务 | 23 | P1 | 产品 P0 基础观测；P1 完整验收 | W00、W03、W05 |
| W19 | 固定场景评测、真实故障与安全门禁 | 14 | P1 | 产品 P0/P1 发布；在线反馈 P2 候选 | W00 |
| W20 | 秘密提供者、短期凭据与连接器隔离 | 9 | P1 | 产品 P1 真实系统接入前 | W00、W07、W08 |
| W21 | 数据保留、清理与授权擦除 | 9 | P1 | 产品 P1 数据治理 | W00、W05、W20 |
| W22 | 生产交付、备份恢复与条件容量建设 | 12 | P1/P2 | 产品 P1 运维；HA P2 候选 | W05、W20、W21 |
| W23 | 版本清单、评测发布门禁与灰度回滚 | 9 | P1 | 产品 P0/P1 发布治理 | W00、W10、W19 |
| W24 | 条件模型路由与受控故障切换 | 9 | P2 | 产品 P2 候选 | W09、W19、W23 |
| W25 | 条件长期记忆治理 | 9 | P2 | 产品 P2 候选 | W09、W13、W19、W21、W23 |
| W26 | 条件专业 Agent、独立互操作与额外渠道 | 10 | P2 | 产品 P2 候选 | W08、W14、W19、W23 |
| W27 | 外部知识来源、附件解析与条件自动更新 | 10 | P1/P2 | P1 批准来源；自动更新 P2 候选 | W11、W12、W13、W20、W23 |
| W28 | 分阶段集成验收与文档收口 | 9 | P1 | 按本次发布范围验收，P2 不阻塞 P0 | W00、W19、W23 |

建议先交付 W03–W09 的运行与授权底座，并联推进 W01/W02/W11；知识依次 W12→W13，诊断 W14；低风险动作 W15 后接 W17 的 P0 Mock 建单，R4 必须等待 W16。W18/W19 在每次阶段交付执行验收，W20/W21/W22 是真实生产集成的基础，W23 管理版本门禁。W24–W27 中的路由、记忆、子 Agent/渠道、自动知识更新有独立启动条件，不阻塞 P0 验收。

### 工作流责任、证据与阶段入口（W00.4，2026-10-15）

工程优先级、产品阶段和依赖以上表为准。下表责任是职责分工，不假定已任命具体人员，也不构成企业/部署/数据擦除批准。维护者负责合并范围与交付，安全/数据/业务负责人承担对应决策；当前用户授权允许本地实施，测试验收继续暂缓。表中证据是交付所需材料，不代表全部已存在或已验证。

| 工作流 | 责任角色 | 交付证据 | 阶段入口/当前限制 |
| --- | --- | --- | --- |
| W00 | 项目维护者 | M01–M12源码快照、G缺口与旧任务回链、决策/职责清单 | 本地授权范围可开展；原验收只由实际证据关闭 |
| W01 | API/Web维护者 | SQL过滤/分页、错误Schema、前后端类型/交互及用例 | 契约同步升级；测试/窄屏键盘验收暂缓 |
| W02 | 身份维护者、企业身份管理员 | AuthSession/JWT/注销/代次、令牌存储；企业身份另需映射与回调证据 | 本地会话已实施；SSO/委托OAuth须批准IdP/subject/scope/沙箱，未选择Cookie不启用Cookie路径 |
| W03 | Worker/运维维护者 | 独立心跳、调度/背压/停机实现及真实运行事实 | 既有本地实现可推进；长任务/多Worker/停机验收暂缓 |
| W04 | Worker/API维护者 | 两类Outbox/job固定引用、领取/退避/死信/取消事实 | 保持Redis仅通知；重复与故障验收暂缓 |
| W05 | 运行时维护者、安全负责人 | owner/generation/lease、单有效attempt、产品提交/恢复/取消 | 只读已知恢复可实施，未知副作用先对账；真实PG竞争验收暂缓 |
| W06 | 运行时维护者 | input_sequence/会话许可、有限产品历史、独立checkpoint | 不读未来输入、不复用失败checkpoint；长对话/恢复验收暂缓 |
| W07 | 权限/运行时维护者 | 当前身份/grant/资源/目录与配额决策、拒绝审计 | 显式主体资源范围；管理员无隐式业务权；并发额度/撤权测试暂缓 |
| W08 | 连接器维护者、安全负责人 | 本地网关及固定实现/Schema；API/MCP目标契约和出站授权 | 本地/隔离Mock已开展；真实API/MCP须批准目标、协议、服务身份和沙箱 |
| W09 | 运行时/模型维护者 | 完整输入预算、物理账本、来源证明/裁剪/冲突与成本 | 保守计量可实施；精确tokenizer/供应商/并发验证暂缓，不把估算当实测 |
| W10 | 产品/API/Web维护者 | SupportResult、引用投影、SSE/恢复/取消与未知状态 | 实现贯通后同步客户端；浏览器/网络/迟到终态验收暂缓 |
| W11 | 知识维护者、独立知识审核人 | 生命周期/ACL/送审与批准/发布/回滚，旧四项验收回链 | 创建人/提交人不自审；真实浏览器/安全/并发验收暂缓 |
| W12 | 知识索引/Worker维护者 | 关键词分块/索引代次、Outbox消费/死信与恢复 | 用户选择关键词；embedding/向量版本/数据边界/费用未批准，保持不启用 |
| W13 | 检索/权限维护者 | 当前ACL/快照/evidence/citation及冲突降级事实 | 关键词证据链可实施；检索评测暂缓，混合/重排无收益证据不启用 |
| W14 | 支持业务/诊断维护者 | S01–S06固定只读Mock工作流、白名单事实、追问/冲突/升级 | Mock显式开关、production拒绝；真实日志/配额/故障API契约另批，场景验收暂缓 |
| W15 | 动作/连接器维护者、业务负责人 | 稳定业务键/参数、独立attempt/回执/复核/未知对账 | 隔离R2可实施；补偿须单独提案/授权，不能从提交响应推断成功 |
| W16 | 权限/审批维护者、指定非本人审批者 | 固定范围/期限/追加决定/一次消费、Run等待和恢复 | 显式人员/项目范围，R4仅隔离环境；完整审批安全验收暂缓 |
| W17 | 支持负责人、客服处理者、连接器维护者 | Mock工单/命令/接管/升级包与独立上下文授权 | Mock已有；真实工单须17.8目标契约与W20受限凭据，回调另需验签/租户映射 |
| W18 | 可观测性/API/Web维护者、运维负责人 | 真实调用账本/计时/Trace/告警、统一Dashboard范围 | 旧16项按本节归属；OTel外部目标未配，真实多Worker/负载/浏览器验收暂缓 |
| W19 | 质量/安全负责人、评测维护者 | 版本化场景/manifest/可信runner、真实评分及功能安全成本延迟报告 | 用户暂缓评测与故障测试；不得造通过结果或人工改pass；安全零容忍 |
| W20 | 基础设施/安全负责人、秘密服务维护者 | provider适配/服务身份/受众scope/撤销审计及隔离契约 | 本地provider保留；Vault/KMS/短期身份/隔离目标未批准，不接真实秘密服务 |
| W21 | 数据治理负责人、独立复核者/维护执行者 | 策略/本人申请/预览、未来独立批准/清理/传播/tombstone/恢复 | 21.2预览已交付；周期/法律保留/SLA/维护身份未确认，真实擦除需额外明确授权 |
| W22 | 运维/基础设施负责人、项目维护者 | 固定镜像/显式迁移/代理/预检、备份恢复及容量事实 | 模板可实现；环境/SLO/RTO/RPO未批准，部署/迁移/演练未执行，HA须测量与启动决策 |
| W23 | 发布维护者、质量/安全负责人 | manifest/适用实测绑定/门禁/灰度观察/追加审计及回滚冻结 | 实现已有；两版本完整实测+服务器批准策略后才启用灰度；测试暂缓，不可因开发激活冒称验收 |
| W24 | 模型负责人、运行时维护者 | 固定模型瓶颈/收益决策、版本化路由与对比评测 | 24.1 go/no-go未通过，路由/回退不启用，保留固定版本 |
| W25 | 产品/数据治理负责人、记忆维护者 | 场景收益/隐私决策、主体确认/ACL/撤回/删除传播与评测 | 25.1未通过，长期记忆不提取/写入/召回；不把短期摘录算长期记忆 |
| W26 | 产品/支持负责人、编排/集成维护者 | 专业化瓶颈/收益决策、隔离上下文/预算/委派/渠道身份契约 | 26.1/26.7未通过；保留单Agent，不启动外部Agent或消息渠道，外部消息另需授权 |
| W27 | 知识/来源负责人、解析/连接器维护者 | 首个批准来源/游标/版本差异/隔离解析/审核与撤权传播 | 27.1目标未批准；自动更新另需27.7收益和权限决策，不能自动发布或改ACL |
| W28 | 项目维护者、各发布范围的业务/质量/安全负责人 | 源码/文档/任务一致、唯一验收归属、实际检查/发布范围证据 | 文档持续同步；28.3–28.5/28.8验收暂缓，提交/远程部署/归档需单独授权 |

### 首批429场景与Mock参考契约（W00.5，2026-10-15）

采用已归档 `2026-09-14-define-support-product-baseline/scenario-catalog.md` 中2026-09-14内部职责评审的S01–S06；该目录明确尚无目标企业真实工单，不扩充为企业实证。参考事实以 `diagnostic_mock_services` 的 `mock_429_v1` 与 `diagnostic_contracts` 的 `mock_429_workflow_v1` 为当前实现版本，不把余额、路由或请求数示例当真实状态。所有角色/资源来自服务器，首批仅本人服务器派生Mock项目；真实企业/日志/配额/路由/故障系统仍由14.10、17.8和W20独立批准。

| 场景/固定request_id | 隔离参考事实 | 唯一候选/允许结论 | 不足或冲突时 |
| --- | --- | --- | --- |
| S01 `mock-s01-rpm` | RPM 75/限额60；TPM预占3000/40000；并发1/4；账户正常，路由healthy，无事件 | rpm_exceeded，说明当前模拟RPM超限及降速/退避；不得改限额 | 无日志、字段不一致或知识声明缺失，保持未确认/人工复核 |
| S02 `mock-s02-tpm` | 输入12000+最大输出预占36000>TPM40000；RPM10/60，并发1/4，无故障 | tpm_reservation_exceeded；解释预占关系，建议合理输出预算 | 无可核对用量/规则或数据冲突，不宣称计量已证明 |
| S03 `mock-s03-concurrency` | 并发5>4，其他限额正常 | project_concurrency_exceeded；项目级节流/队列建议 | 不枚举其他用户正文，不自动调整项目额度 |
| S04 `mock-s04-account` | 现金100、赠送0、冻结0、套餐剩余Token=0、账户active，其他限额正常 | plan_quota_exhausted；本实现仅覆盖“有余额但套餐硬配额耗尽”分支 | 订单未生效、账户异常、资金纠纷没有Mock证明，不代替真实账务核验，不充值/退款/补偿 |
| S05 `mock-s05-upstream` | 同mock-model/mock-cn，route rate_limited，故障数1，两源同mock-upstream-429-v1 | upstream_rate_limited；关联模拟可见事件，不归因余额 | 模型/区域/事件ID不一致、不公开信息或缺依赖则未确认 |
| S06 `mock-s06-unknown` | 429 error_class=unknown，其他限额正常，无相关事件 | 无唯一候选，unconfirmed；缺请求ID或多ID先needs_information | 补请求ID/含时区时间/模型/区域，必要时推荐升级，不编造根因 |

五项核心只读能力为get_request_logs/get_current_user_profile/get_model_route_status/list_active_incidents/search_knowledge；严格DiagnosticQuery窗口成对带时区、>0且≤15分钟，不超过当前+5分钟；主体/项目只能本人派生范围，请求ID必须在本人截至本轮的有限用户历史出现。未配置ID返回unavailable，不能从模型参数生成范围；固定Request/实现/目录版本/调用摘要/观察时间绑定来源。工具Schema和字段白名单由 `diagnostic_mock_services.DIAGNOSTIC_FACT_FIELDS`、`tool_result_services`强制，数值/大小/状态不合法拒绝；完整合法JSON在预算内返回，超预算拒绝，不截断出非法JSON。旧基线“截断输出”的意图落实为有界字段和预算，不删除证据定位或通过污染文字扩权。

每个查询支持由当前真实用户输入选择的[ mock:empty ]/[ mock:timeout ]/[ mock:denied ]/[ mock:conflict ]模式（实际写法去掉方括号内两端空格，即`[mock:empty]`等），分别为not_found/无事实、unavailable/MOCK_DEPENDENCY_TIMEOUT、access_denied/MOCK_DOWNSTREAM_ACCESS_DENIED、显式conflict；模型参数不能切换模式。工作流固定10节点、最多4项业务查询+1项关键词查询，任一必要源不可用不确认。唯一跨源一致候选还须本轮获准有效知识中的 `supportops_429_rule_v1:<code>`声明和证据链，才可confirmed_mock；该标记只匹配服务端固定规则，不执行知识内容。诊断/升级推荐不代表创建工单；R2提案/执行走独立账本/授权/复核，资金/改权限等高风险禁止从此流程直接执行。Mock开关默认false，production配置拒绝启用；未运行场景评测或故障演练，14.11/W19仍未完成。

## 需求覆盖矩阵

下表保留2026-10-12生成规划时的原始差距与ID归属，表中“待建设/待修复”不是当前完成状态；2026-10-15模块快照见上表，当前完成/部分实现以tasks为准。“不支持任意代码/自由 SQL/自动建号扩权”等安全排除项见后文，不作为待补功能。

| ID | 来源 | 待处理需求 | 范围/状态 | 工作流 |
| --- | --- | --- | --- | --- |
| G01 | M01–M12 / 验证边界 | 既有未验收项与当前代码核对 | 待验收；不重写旧勾选 | W00、W28 |
| G02 | M01 | 生产登录固定预填移除 | 待修复 | W02 |
| G03 | M01/M03 | 浏览器会话、存储、注销与刷新模式 | 基础加固；刷新按决策 | W02 |
| G04 | M03 | 企业 SSO、显式身份映射与委托 OAuth | 按客户需求启动 | W02 |
| G05 | M02 | 工具/来源/知识版本剩余分页与跨层契约 | 待补齐 | W01 |
| G06 | M02 | 受影响企业/Dashboard 查询逻辑服务化 | 增量维护；不全量重构 | W01 |
| G07 | M02/M11 | live/ready/业务依赖健康边界 | 待明确并验收 | W01 |
| G08 | M04 | 模型发现/探测的持久化任务与恢复 | 待建设 | W04 |
| G09 | M10/M11 | 独立心跳、活动数量、监督、停机 | 待修复 | W03 |
| G10 | M08/M10 | Run 通知 Outbox、投递/重试/死信 | 待建设；不等于知识 Outbox | W04 |
| G11 | M09/M10 | Run 租约/attempt/fencing、checkpoint 与产品恢复 | 待建设 | W05 |
| G12 | M08/M09 | 同会话串行、固定本轮输入与历史边界 | 待修复 | W06 |
| G13 | M08/M10 | 运行、等待、删除、副作用期间取消收敛 | 待补强 | W05 |
| G14 | M03/M05/M10 | 实时 active 用户/Agent grant/撤销 | 待建设 | W07 |
| G15 | M06/M09 | 目录角色/风险/启停/版本、单工具额度 | 待贯通 | W07 |
| G16 | M03/M06 | 资源 RBAC/ABAC、R0–R4 映射、租户配额 | P0 最小范围；P1 完整治理 | W07 |
| G17 | M09 | 历史裁剪、摘要、证据事实与可信边界 | 待建设 | W09 |
| G18 | M09/M11 | 完整输入计量、累计预算、预留/恢复/重试 | 待修复 | W09 |
| G19 | M08/M01 | 完整结构化回答、引用/建议/升级持久化 | 待建设 | W10 |
| G20 | M07 | ACL/审核/并发/审计/浏览器与迁移验收 | 既有 change 4 项未验收 | W11 |
| G21 | M07/M10 | 分块、embedding、索引消费/重建/失效/缓存 | 待建设 | W12 |
| G22 | M07/M09 | 权限检索、证据快照、引用校验与不足升级 | 待建设 | W13 |
| G23 | M07 | 外部 CMS/文件、附件解析与受控自动更新 | 来源按需；自动更新 P2 | W27 |
| G24 | M06/M09 | 429 显式工作流与 4–6 个只读业务工具 | P0 Mock；P1 真实契约 | W14 |
| G25 | M06 | 工具 Registry/Gateway/MCP/API 接入与健康 | 待建设 | W08 |
| G26 | M06/M10 | 动作账本、稳定幂等、回执、复核与未知对账 | P0 R2 Mock；P1 完整 | W15 |
| G27 | M01/M10 | 审批等待、非本人/参数/期限/一次性/恢复 | P1 沙箱验收 | W16 |
| G28 | M06/M08 | 工单查询/建单/全流程、升级包、客服接管 | P0 Mock；P1 选定真实系统 | W17 |
| G29 | M11 | 本轮 usage、实际版本、客户端首文本、阶段/OTel | 待补强；不伪造指标 | W18 |
| G30 | M11/M01 | 健康窗口/状态/过滤/下钻/排行和历史 Dashboard 验收 | 既有 change 16 项未验收 | W18 |
| G31 | 验证边界/M11 | 固定场景集、业务结果、质量/安全/真实故障评测 | 待建设；在线评测有门禁 | W19 |
| G32 | M05/M11 | 版本 manifest、评测门禁、灰度与回滚 | 待建设 | W23 |
| G33 | M12 | Vault/KMS provider、短期凭据、撤销与连接器隔离 | 生产接入前完成 | W20 |
| G34 | M12 | API/Worker/Web 交付、迁移、备份/恢复与 Runbook | 待建设；不代表远程部署授权 | W22 |
| G35 | M12 | 消息/checkpoint/知识/记忆/凭据/备份留存与擦除 | 按批准策略实施 | W21 |
| G36 | M10/M12 | 负载、SLO、背压、聚合/轮询优化、HA 与扩缩容 | 测量先行；HA 为 P2 | W22 |
| G37 | M04/M09 | 按能力/数据/成本选型与固定版本回退 | P2 候选 | W24 |
| G38 | M09 | 长期记忆提取/确认/ACL/召回/纠错/删除 | P2 候选 | W25 |
| G39 | M09 | 专业子 Agent、隔离上下文、预算/权限与有界协作 | P2 候选 | W26 |
| G40 | M01/M06 | 额外业务渠道/API、独立远程 Agent/A2A | 按需求与评测启动 | W26 |
| G41 | M06/安全/验证边界 | 出站限制、脱敏、Prompt Injection 与安全硬门禁 | 贯穿工具接入与发布验收 | W08、W19 |
| G42 | M01–M12 / 文档维护 | 状态/接口/表清单/验收证据与旧任务收口 | 每次实际交付同步 | W00、W28 |

## 十二个模块覆盖

| 模块 | 主要待办工作流 |
| --- | --- |
| M01 Web | W01/W02/W10/W11/W13/W16/W17/W18/W25/W26/W27：新增状态/引用/审批/客服 UI、历史验收与身份体验 |
| M02 API | W01/W04/W16/W18/W22：分页、状态/错误契约、任务接口、授权和就绪边界 |
| M03 身份组织 | W02/W07/W16/W20：会话/SSO 决策、实时主体和资源权限、审批范围、委托凭据 |
| M04 模型 | W04/W09/W20/W23/W24：探测恢复、预算、秘密、发布与条件路由 |
| M05 Agent 控制面 | W07/W23/W24/W26：调用授权、版本门禁、条件配置升级与专业能力 |
| M06 工具 | W07/W08/W14/W15/W16/W17/W20/W26：策略、协议、安全连接、业务动作 |
| M07 知识 | W01/W11/W12/W13/W21/W27：剩余验收、索引/ACL/引用、来源与数据治理 |
| M08 会话消息 | W04/W05/W06/W10/W15/W16/W17/W21：通知、恢复、上下文、完整结果、升级/审批与留存 |
| M09 Runtime | W05/W06/W07/W08/W09/W13/W14/W16/W24/W25/W26：执行/授权/证据与条件扩展 |
| M10 Worker | W03/W04/W05/W06/W12/W14/W15/W16/W18/W22：调度、故障恢复与动作流程 |
| M11 观测 | W18/W19/W22/W23/W24/W26：真实计量、Dashboard 验收、评测/负载/发布 |
| M12 基础设施 | W04/W05/W12/W15/W20/W21/W22：迁移、秘密、持久化、备份、擦除与运维 |

## 历史 20 项未勾选任务的唯一归属

截至快照，知识 change 为 25/29，系统 Dashboard 为 18/34。其他已完成 change 不重新复制实现；已有代码不能未经验证直接勾选旧任务。以下对应的是复核/缺口修复/验收任务，验收所有权仍在原 change。

| 原 change | 原 ID | 原待办范围 | 本规划任务 ID |
| --- | --- | --- | --- |
| [add-knowledge-lifecycle](../add-knowledge-lifecycle/tasks.md) | 5.2 | 跨租户、ACL、管理员正文与审计安全 | 11.1 |
| [add-knowledge-lifecycle](../add-knowledge-lifecycle/tasks.md) | 5.3 | 并发发布、事件、事务与索引失败 | 11.2、12.10 |
| [add-knowledge-lifecycle](../add-knowledge-lifecycle/tasks.md) | 5.5 | 知识浏览器主/异常路径、窄屏、可访问性 | 11.6、11.7 |
| [add-knowledge-lifecycle](../add-knowledge-lifecycle/tasks.md) | 5.6 | 检查/测试/构建/迁移/OpenSpec | 11.8 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 2.3 | 健康协调与有效租约 | 18.8 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 2.4 | 生命周期/执行/健康与无活动 | 18.9 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 2.5 | 窗口与最小样本 | 18.10 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 2.6 | 待发布/原因/时间/stale | 18.11 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 3.7 | 系统 API 隔离、权限、分页与范围 | 18.12 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 4.2 | 统一筛选与刷新 | 18.13 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 4.4 | 用户/会话/调用/费用/P50–P99 卡片 | 18.14 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 4.5 | 趋势与桶下钻 | 18.15 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 4.6 | 排行与分布 | 18.16 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 4.11 | 视觉与状态验收 | 18.17 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 5.1 | 多 Worker 租约与积压 | 18.18 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 5.2 | 健康派生异常分支 | 18.19 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 5.3 | 系统聚合与缺失 usage | 18.20 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 5.4 | 浏览器筛选/下钻/分页/Trace | 18.21 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 5.5 | 状态/抽屉/筛选/窄屏 | 18.22 |
| [add-system-agent-dashboard](../add-system-agent-dashboard/tasks.md) | 5.6 | 留存/告警/stale/聚合启用阈值 | 18.23 |

知识 5.3 的生命周期并发/事务由 W11 验收，索引失败在 W12 真正建立索引后验收，不以没有索引能力的旧用例证明索引正确。Dashboard 的历史 per-Agent lease 描述按已归档健康规范与真实 Run/service lease 解释，不能新增虚假“每个 Agent 一个进程”假设。

### 2026-10-15 历史知识任务核对（W00.2）

本表只记录源码覆盖和缺口，不运行/新增测试，也不调整旧change的四项勾选。既有用例中的断言不是本次通过证据。

| 原ID | 当前已有实现/用例 | 仍需处理及唯一归属 |
| --- | --- | --- |
| 5.2 | `packages/support_core/tests/test_knowledge_services.py::test_publish_snapshot_and_acl`只验证角色ACL正例；`tests/api/test_knowledge.py::test_mock_import_is_idempotent_and_tenant_scoped`含其他租户文档404，生命周期用例含清空/恢复ACL | 角色/用户/部门组合、跨租户详情/版本/快照/审计、管理员无正文许可、拒绝审计字段脱敏与撤权竞态尚无完整安全矩阵，归11.1；不能把单个角色正例当作已覆盖全部权限 |
| 5.3 | `knowledge_services`文档锁、revision、快照/Outbox同事务；三个核心服务用例涵盖发布快照/事件存在、重复内容/旧revision拒绝及回滚新版本。`knowledge_index_services`已有分批SKIP LOCKED/幂等收敛/有限重试和死信 | 单会话顺序用例不证明多连接并发单当前版本、事件重复/乱序、崩溃事务恢复，生命周期归11.2；新关键词索引的失败/重启/撤权收敛归12.10，不用原事件“存在”断言证明消费正确 |
| 5.5 | Web知识列表/详情/审核/发布/归档/索引面板存在；`apps/web_chat/e2e/routes.spec.ts`含知识列表进入详情并返回的API桩路径（用户原有未跟踪文件，未暂存） | 没有知识创建→版本→ACL→独立审核→发布→归档的真实浏览器闭环和异常矩阵，归11.6；窄屏、键盘/焦点、消毒/外链与权限交互归11.7；桩路由返回不证明后端行为 |
| 5.6 | `tests/postgres/test_schema_comments.py`使用Base.metadata.create_all检查PG注释；`test_soft_delete_migration.py`提供历史软删除迁移用例，`test_checkpoint.py`覆盖检查点隔离基础 | create_all不是0045及完整迁移链验收。检查/构建/迁移/OpenSpec严格校验归11.8和28.3/28.8；目前按用户要求暂缓，原5.6保留未完成 |

明确的旧契约差异：`tests/api/test_knowledge.py`仍用versions响应顶层数组，并在submit-review后直接publish；当前已改为分页items且必须独立批准。核心服务发布用例也缺独立review决定。登录fixture及浏览器localStorage令牌与当前AuthSession/sessionStorage契约需按W02适配。这些是静态阅读发现的待适配点，未执行用例，不能报告为本次运行失败或通过；测试恢复后在上述原归属中处理。

### 2026-10-15 历史Dashboard任务核对（W00.3）

已归档 `2026-09-14-complete-observability-release-readiness/tasks.md`包含全部完成声明，但当前旧Dashboard change仍有16项未勾选；归档声明不能代替本工作区实测。以 `openspec/specs/agent-health-computation`、`system-agent-dashboard-completion`、`dashboard-data-contract`与当前源码共同核对，记录重复承诺而不重写旧状态。

| 原ID | 当前判定与代码证据 | 后续归属 |
| --- | --- | --- |
| 2.3 | 已有健康协调：`health_services.refresh_agent_runtime_health/refresh_all_agent_runtime_health`及Worker独立维护；共享服务lease+Run/attempt许可，非每Agent一个进程。重复实现承诺，真实竞争验收仍待 | 实现18.8；故障18.18/18.19 |
| 2.4 | 生命周期/执行许可/健康分别投影，等待释放模型Worker；有效attempt/会话许可及queued按业务Run计数，无活动/缺证据为unknown。已有实现，仍待状态矩阵验收 | 18.9 |
| 2.5 | 24小时历史、至少3个有效样本、取消排除，活跃请求另查全时段；nearest-rank P95。窗口实现存在，不重复实现 | 18.10；验收18.19/18.20 |
| 2.6 | API只读快照computed_at/observed_at，90秒stale、超前5秒/缺失unknown；pending_publish按当前草稿/活动摘要，Web说明。已有实现 | 18.11；交互18.22 |
| 3.7 | `tests/api/test_dashboard.py`有空租户端点、错误详情跨租户404、单Agent范围/已知零usage基础；不覆盖全部系统API管理员拒绝/非法分页/多租户指标一致性 | 18.12 |
| 4.2 | `DashboardFilters/useDashboardScope`、`dashboard_filters/dashboard_query_services`统一筛选/URL/刷新/分页，已有实现 | 18.13；浏览器18.21 |
| 4.4 | SQL共用计量、14项卡片/P50/P95/P99、已知零/缺失分离与来源/样本数，已有实现 | 18.14；范围验收18.20 |
| 4.5 | `bucket_start/bucket_end`裁切固定范围，趋势桶下钻保留筛选，已有实现 | 18.15；浏览器18.21 |
| 4.6 | 八项排行/稳定Top10、完整范围健康分布、缺失不画零柱，已有实现 | 18.16；指标18.20/浏览器18.21 |
| 4.11 | 页面/组件有主题/Loading/空态/错误/响应式处理；现有Analytics桩E2E仅含390px标题/单指标检查，不能证明完整视觉或键盘验收 | 18.17 |
| 5.1 | `test_worker.py::test_runtime_health_uses_service_lease_and_persists_reason`用单服务记录模拟过期/样本不足；不是多真实Worker租约/积压/停机聚合 | 18.18 |
| 5.2 | 上述健康用例只有过期/样本不足基础；无活动、当前attempt失效、等待/连续失败/待发布等完整矩阵仍缺 | 18.19 |
| 5.3 | Dashboard三个API用例覆盖部分空租户/范围/已知零；全部停用、多并发Run去重、未知usage/费用和统计边界矩阵仍缺 | 18.20 |
| 5.4 | `apps/web_chat/e2e/analytics.spec.ts`有导航/旧路由、运行Trace、错误筛选/分页/抽屉；全为API桩，无真实Worker/数据库及完整筛选/桶下钻链路 | 18.21 |
| 5.5 | Analytics桩E2E有390px标题/指标，未覆盖完整状态颜色/未知、焦点、抽屉、刷新/筛选保持或真实窄屏视觉 | 18.22 |
| 5.6 | `docs/modules/observability/overview.md`已记录租约/快照/告警/stale/留存边界；缺批准负载下的小时聚合启用数值门槛，不能虚构测量 | 18.23 |

待适配而非本次运行失败：API排行用例仍只断言旧四个排行键，当前为八指标；Analytics E2E仍向旧localStorage键写令牌，且summary/timeseries/rankings/身份桩未覆盖最新字段与sessionStorage契约。真实PG `test_checkpoint.py`仅基础检查点往返/隔离，不证明多Worker恢复与产品提交一致。后续测试恢复时按18.12/18.18–18.22及W02更新fixture/断言；原16项保持未勾选，已归档完成声明不作为自动关闭依据。

## 不纳入功能待办的安全排除项

- 任意上传/执行用户工具代码、自由生成生产 SQL、草稿任意构造运行图、模型自主发布知识或修改 ACL。
- 外部 subject 静默建号、客户端指定租户/角色扩权、平台管理员自动绕过 Agent grant、以审批凭证扩权。
- 无审批真实 R4、未知写结果盲重试、无限上下文/工具/子任务、用 Prompt 或工具描述替代服务端策略。
- 为满足架构图强制拆微服务/多 Agent、强制引入独立向量库/新队列/新工作流引擎。
- 默默更新历史会话固定版本/凭据引用、清除业务审计、关闭软删除或类型/安全校验。

这些是现有不变量而非缺失需求；相应拒绝路径在 W07/W08/W19 验证。

## 验收与阶段交付

- 产品 P0：单域、单 Agent、Mock 工具；知识问答、429 诊断、证据不足建单、依赖失败降级四条主路径。
- 产品 P1：批准的真实系统、客服接管、审批和完整治理；至少一个隔离 R4 操作覆盖冻结/非本人批准/拒绝/改参/过期/重放/恢复/复核。真实退款等业务集成需另行批准。
- 产品 P2：必须有 P1 瓶颈和收益证据，候选功能默认关闭；决策记录与实际实现完成分别追踪。
- 阈值沿用 [发布门禁](../../specs/support-release-gates/spec.md) 与 [评测基线](../archive/2026-09-14-define-support-product-baseline/evaluation-baseline.md)；内部基线不能代替脱敏真实企业场景复核。
- 每个工作流有完成标准；最后 W28 按发布子集核对代码、测试、迁移、安全、恢复、文档及历史任务证据。不以 SQLite/FakeRedis/路由桩测试代替真实并发与业务结果验收。

### W19.6 既有目标的版本与适用范围

沿用基线 **0.1.0 / 2026-09-14**，批准证据为归档的[基线评审记录](../archive/2026-09-14-define-support-product-baseline/review-record.md)。项目作者分别承担产品、业务、安全和工程职责批准 **P0 内部单域、单 Agent、Mock 范围**；这不是目标企业对 P1 的批准，不授权真实 R4。原[评测基线](../archive/2026-09-14-define-support-product-baseline/evaluation-baseline.md)标题保留“候选”历史，公式与初始目标已同步[主规格](../../specs/support-release-gates/spec.md)；目标数值不表示已有实测达标。

| 指标 | 沿用目标与分母 | 适用范围 |
| --- | --- | --- |
| 自动解决率 | ≥60%；无需人工且用户/标注确认解决数 ÷ 适用场景总数 | 批准的标准问题；等待审批、工具返回或模型自述成功不能替代解决确认 |
| 意图分类准确率 | ≥95%；意图、风险和路由均正确数 ÷ 已标注样本数 | 固定场景的意图/风险/路由标注 |
| 知识正确率 | ≥85%；关键事实与批准参考一致数 ÷ 需要知识回答数 | 批准知识快照与参考答案，不将无知识场景算作正确样本 |
| 引用覆盖率 | 100%；有可定位有效引用的有依据事实数 ÷ 全部有依据事实数 | 知识与其他有依据事实；有效性包含实际引用定位和权限约束 |
| 工具参数合法率 | ≥99%；通过 Schema、权限和资源校验调用数 ÷ 全部工具调用数 | 适用工具的实际调用；拒绝和失败不能静默剔除 |
| 工具成功率 | ≥98%；有效业务结果且未触发重试/降级调用数 ÷ 调用总数 | 固定工具契约；submitted/unknown、文本声称成功不作为目标完成 |
| 升级上下文完整率 | ≥95%；字段完整且客服无需重复基础排查的包数 ÷ 升级包总数 | 批准的人工升级场景，包含权限内可读上下文 |
| 首次有效文本延迟 | P95 ≤3秒 | 服务端收到请求并通过鉴权至客户端首有效正文；不能用 SDK 首 Token 或 API 首次读正文代替 |
| 普通问题完整响应延迟 | P95 ≤10秒 | 同一起点至客户端收到 run.completed 或等价终态；业务等待单独记录，不伪造普通响应样本 |
| 安全硬门禁 | 跨租户访问、越权、无有效批准的 R4、批准篡改、严重泄露及注入绕过均为零容忍 | 发布范围内全部主体/工具/恢复路径；适用安全场景须实际执行且无失败，不接受未测即零 |

P0 使用隔离 local/dev、S01–S20 每场景正例/边界/失败至少各一，共不少于60个样本；P1 必须先由目标企业业务、安全、系统和产品/工程负责人确认脱敏工单、正式规则、参考事实与接入范围，再在接近生产的隔离 staging 复核，并覆盖完整 R4 申请、冻结、非本人批准、拒绝、改参重审、过期、一次消费、超时对账及复核。P2 启用仍需 P1 瓶颈与收益批准，预定义子任务契约不扩大此批准。

每次报告固定数据集/负载/执行器、Prompt/图/源码/Schema/依赖、模型/工具/知识/权限及策略摘要；负载记录并发用户、每人速率、请求大小、模型、工具命中率与持续时间。取消、断网、外部故障和人工操作分开标记，不静默删除。SDK、API、浏览器计时分别保留来源与缺失值，不跨未校准时钟推算精确耗时；当前门禁的 client_visible_text_v1 证据须符合批准口径，来源名称本身不证明可比。成本要求每样本完整 USD 用量；成本上限仍需独立批准，配置为空不是零费用或允许无限费用。

非安全目标调整必须形成正式版本决策，记录指标版本、适用数据集/负载/阶段、实际值、差距、根因、补救、责任人、到期日，由产品与技术/工程负责人共同批准并保留旧基线。没有批准不改阈值；本次没有新增豁免，也没有改变现有门禁。跨租户、审批绕过和严重泄露等安全硬门禁不得豁免、降级风险或关闭检查。本文完成目标适用范围记录；评测 runner、样本与实测仍归 W19 其他任务，未执行。

## 当前规划检查命令

```bash
openspec status --change complete-supportops-production-roadmap
openspec validate complete-supportops-production-roadmap --strict
```

以上验证只检查规划产物/格式，不证明业务实现或测试通过。实施可指定 Wxx 或明确阶段；不要把 artifact 全部 done 解读成 tasks 已勾选。首批实现包含 W01 三个列表分页、W02 登录预填移除、W03 心跳/监督/停机与 W04 通知 Outbox；公平调度、执行尝试恢复、模型探测 job 及测试验收仍待处理。
