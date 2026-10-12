# SupportOps Agent 全项目待处理规划

基于 [项目级 AGENTS](../../../AGENTS.md) 的 2026-10-12 快照和 [目标架构图](../../../architecture-diagrams/enterprise-support-agent-architecture-2026-10-12.png)，本 change 将待修复、待建设、待验收和有门禁候选项集中为 **29 个工作流、302 项未勾选任务、42 个需求覆盖项和 22 份能力规格**。

本次只生成规划，业务代码与旧 change 状态保持不变。302 项包含对旧 20 项待验收工作的承接/核对，不应再把“302 + 20”理解为独立新增工作量。规格和任务生成完备不代表任何产品阶段已完成。

## 阅读与实施入口

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

## 需求覆盖矩阵

下表将 AGENTS 的所有功能差距和边界按可实施需求归并；“不支持任意代码/自由 SQL/自动建号扩权”等安全排除项见后文，不作为待补功能。

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

## 当前规划检查命令

```bash
openspec status --change complete-supportops-production-roadmap
openspec validate complete-supportops-production-roadmap --strict
```

以上验证只检查规划产物/格式，业务功能仍为未实施。实施可请求指定 Wxx 或明确阶段；不要把 artifact 全部 done 解读成 tasks 已勾选。
