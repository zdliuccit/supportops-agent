# 可观测性与健康

运行时会记录 AgentRun 摘要、模型/工具 Observation、Token、成本、阶段耗时、TTFT、finish reason、错误事件和 provider request ID。Dashboard API 提供 summary、timeseries、runs、errors、trace 及系统级聚合。

健康状态由运行窗口、活动 Run、Agent 版本事实和 Worker lease 派生；健康快照不替代 Run 原始状态。租约过期或执行服务不可用时，系统显示 `unknown`/不可用原因，而不是把缺失数据当成健康。

生命周期（draft/active/disabled）、执行态和健康态分别展示：停用不会抹掉仍在收敛的执行事实，业务等待不代表模型正在执行。状态表的 active_run_count 为当前排队、有效执行、取消中的业务 Run 数；总览 status_counts.running 按同一许可条件统计有这些 Run 的 Agent 数，多次执行尝试或同 Agent 多个 Run 均不重复增加 Agent 数。当前执行不按历史时间窗截断；区间及用户/版本/模型/Run状态等维度用于确定匹配的 Agent 集合，再观察该集合当前的执行事实。等待审批/动作、过期或指针错配执行、通知死信不计入活跃执行；等待数量在健康原因中解释，缺快照的 GET 只报告 unknown，不重算或补造观测时间。

## 受控 OTLP 导出

`supportops_core.telemetry_export` 将数据库已结束的 Agent/模型/工具/检索/API/客户端观测映射为 OTLP/HTTP JSON，不新增依赖、表或后台调度。协议格式遵循 [OTLP 官方规范](https://opentelemetry.io/docs/specs/otlp/)；应用约定固定为 `supportops_otel_v1`，不冒用尚未采用的 GenAI semantic conventions 版本。数据库业务回执及审计仍是事实来源，导出不是业务授权或结果复核。

服务端默认 `SUPPORTOPS_TELEMETRY_EXPORT_ENABLED=false`。获准维护身份须同时注入单租户允许列表 `SUPPORTOPS_TELEMETRY_ALLOWED_TENANTS` 和独立至少32字节的 `SUPPORTOPS_TELEMETRY_HMAC_KEY`，不能复用 JWT/密封主密钥。配置由受控服务端提供，普通用户和平台角色没有跨租户导出入口。每次命令必须指定一个允许的租户、带时区的起止时间，窗口最多24小时且不能包含未来，按观测完成时间左闭右开查询；默认100条、最多200条。查询不加载消息、Run正文或凭据，PostgreSQL使用独立只读事务、5秒语句超时和整体时限；不会写入/清理业务记录。

允许字段为固定步骤类型/状态、时间和首输出耗时、假名化Run/attempt/Agent版本/模型版本/工具标识、工具实现/目录版本、已记录的模型Token/费用及可用性。tenant/trace/span与版本关联使用租户域隔离的HMAC，父关系复查同租户/Run/attempt，不沿用客户端correlation ID。工具ID再假名化；名称、供应商自由文本、provider request ID、原始错误码/异常、Prompt、回答、工具参数/结果、任意metadata均不透传。假名化保留关联能力，并非匿名数据；接收端仍需访问控制和批准留存。轮换HMAC密钥会改变关联标识，需记录切换范围。

模型叶子观测仅导出现有非负整数用量，缺失不填零；父Agent聚合不重复携带Token/费用。现有模型观测可能汇总多次调用，不能用导出span数推断物理调用次数或替代ModelCallLedger结算。客户端只保留自身耗时并标记不可信，API首正文为含轮询/重连的可见性上界，不跨时钟相减。未知kind/status、逆序时间、未结束观测跳过并报告数量；业务阶段仍通过Trace的`business_phases_v1`查看，不补造对应持久化span。

默认命令仅返回数量、字节数、payload摘要和下一页游标，不打印或保存payload，也不解析/访问接收端。以下为使用示例，本轮未运行；真实租户ID由获准维护者替换：

```bash
uv run --package supportops-core python -m supportops_core.telemetry_export \
  --tenant-id <approved-tenant-uuid> \
  --start 2026-10-14T10:00:00+08:00 --end 2026-10-14T11:00:00+08:00
```

实际传输另需批准目标并注入 `SUPPORTOPS_TELEMETRY_OTLP_ENDPOINT`（HTTPS完整路径，以`/v1/traces`结尾）、`SUPPORTOPS_TELEMETRY_ALLOWED_IPS`（全部DNS结果必须属于显式IP集合），按需注入`SUPPORTOPS_TELEMETRY_BEARER_TOKEN`，再显式加`--send`。内部Collector的私网IP也必须逐个批准；禁止link-local/保留/组播/未指定地址。请求固定连接已核准IP，使用原主机的Host/TLS校验，复用HTTPX官方[sni_hostname扩展](https://www.python-httpx.org/advanced/extensions/)，关闭环境代理/CA覆盖、重定向和自动重试；默认10秒整体网络时限。默认payload上限1MB（可配至4MB），超限整批拒绝，可缩小批次；响应限64KiB且拒绝压缩。配置、数据库、网络和接收端错误仅输出固定错误码，无连接串、令牌或响应正文。

命令输出`telemetry_export_report_v1`，区分preview、not_sent、acknowledged、partially_acknowledged、blocked与outcome_uncertain。`send_attempted`只表示进入唯一发送路径，可能尚未解析地址或发出字节；`receiver_response_confirmed`表示取得并核对当前批次数量的回执。预览和无可导出span的send批次没有接收证明，accepted/rejected保持null；`accepted_spans`只描述接收端声明接受数量，不保证后端永久存储或业务成功。

HTTP 200检查`partialSuccess.rejectedSpans`的非负int64与本批数量；重复JSON字段、非有限常量、坏计数/警告类型拒绝。接收端可在拒绝数0时发出警告，只投影`receiver_warning_present`，不打印原文；未知JSON字段继续忽略。部分成功退出2，next_cursor为空且cursor_advance_allowed=false，不把扫描末尾当作整批投递成功，也不自动重发。完整接收回执（包括0拒绝的警告）可推进扫描；这不保证没有迟到数据。

计数按[ProtoJSON整数表示](https://protobuf.dev/programming-guides/json/#representation-of-each-type)接受字符串/数字及整数值指数形式，null按未设置处理为0；用Decimal精确解析，不经过浮点舍入，拒绝小数值/负数/非有限数/超出int64或本批数量。数字文本本地限128字符，接收响应仍限64KiB，不声称实现通用Protobuf解析器。

进入发送路径后传输/超时/坏回执或中断固定outcome_uncertain、退出4，数量无回执时保持null，不给成功游标或自动重试；发送前失败退出1，发送前中断130。错误报告保留原requested_after、batch_last_cursor及载荷摘要等扫描定位，batch_last_cursor不是成功水位。输出管道故障/强制终止可能无法交付报告；进入发送后输出失败也退出4，不补发。预览的next_cursor只是查询定位，无HTTP接收证明。

分页同时传入`--after-finished-at`和`--after-id`并保持原租户、时间窗口与HMAC密钥；没有持久化投递水位或一致快照，迟到提交/补写父关系可能落在游标之前。部分成功先人工处理接收端问题及未接收范围，不能依据扫描游标跳过或重发整批；网络未知先核对接收端，可能已接收。需要覆盖迟到观测时人工重扫重叠窗口，同一观测保持稳定span ID，但接收端是否去重依实现。没有自动周期发送、可靠交付承诺或审计替代，命令、接收端联调、测试及安全/性能验收均未执行。

W08.8接入补充：发送复用[通用HTTPS出站边界](../security/overview.md)，DNS改为不继承应用环境、可超时终止的标准库子进程，结果有界且全量复检。内部Collector只允许逐个配置的RFC1918/ULA私网IP，回环地址也始终拒绝；不能以本地Collector为由打开全局私网策略。原`TELEMETRY_*`错误域保留，通用传输错误在命令边界映射，不输出上游文本。本次未实际发送或进行联调。

## 运行告警

Analytics 共用当前状态与历史调用范围：总览卡片、趋势、调用、错误、排行及状态通过同一 SQL 查询服务过滤。生命周期/健康使用当前读模型（缺失/stale 属 unknown）；固定版本、模型、原用户、当前末级部门和 Run 状态用于历史调用。显式`agent_id`先在当前管理员租户核对，外租户与未知ID统一404，同租户软删除历史仍可分析；未指定则查询当前租户系统范围。URL 保存带时区 start/end 和分页，刷新不移动时间区间，非法筛选保留错误并停止请求；等待状态可独立筛选。服务端桶终点和首尾裁切支持图表/可操作桶选择下钻，排行/状态入口保留有效范围。健康分布基于完整匹配 Agent 的 SQL 计数，不依据第一页；健康统计固定 24 小时、共享服务全局、用户/新增会话口径在界面与接口文档明确说明。W18.12已完成API/SQL范围、分页与隔离验收，使用受控历史/快照事实及完整迁移隔离PG；浏览器、多Worker与后续健康/完整统计矩阵仍待对应任务验收。

Worker 独立 `runtime_alerts` 周期任务从 Run、当前 attempt、模型观测、工具拒绝事件及动作元数据派生八类规则：预算耗尽、当前租约过期、恢复进行中、恢复需要处理、动作待对账、授权/凭据拒绝、模型首输出超阈值和模型观测缺失。扫描不加载问题、回答、工具参数、密封凭据或外部响应，不修改业务状态，也不发送外部通知。

Dashboard 概览沿用绿色主题与卡片，读取时展示页面级骨架；独立状态/调用/错误/排行列表使用局部加载遮罩并冻结刷新，三个分页列表加载期间收起分页控件，避免临时空总数改写原请求页码。指标缺失保留“—”和样本缺口，未知排行保留文字入口、不画零柱；空Agent与服务不可用/维护异常/待确认均有文字提示，失败持久展示可重试错误。窄屏筛选限于可用宽度、健康圆环/图例纵向排列，表格横向滚动保留在内容区域。W18.17已使用隔离API桩完成31项真实Chromium场景与相关47项回归、截图检查；真实后端/Worker及完整筛选/下钻/焦点验收仍由后续任务承担。

`runtime_alerts_v1` 按租户/规则/来源唯一保存最近事实；`runtime_alert_scan_states` 按 Run/动作分别使用 UUID 游标，每轮结束重新检查变化和迟到提交。每类每批默认 50 条（上限 200），默认周期 30 秒；近期终态检查 24 小时内创建的来源，仍活动的 Run 和待对账动作不受该窗口限制。每类游标有独立短事务和锁，多 Worker 共用游标；扫描失败可使维护状态降级，但不回滚已提交 Run/动作。告警是周期采样，批次间短暂状态不保证捕获，完整扫描时延取决于来源数量，不能当作实时审计替代。

租户管理员通过 `GET /v1/admin/runtime-alerts` 分页读取、按状态/级别/规则筛选；权限和条件在 SQL count/分页前应用。`POST /v1/admin/runtime-alerts/{id}/acknowledge` 接受严格整数 `expected_revision`，版本冲突返回 409，同一人员重复已阅幂等，并在同事务追加最小审计。已阅不关闭告警，不修改审批或业务回执；条件重新命中或来源 generation 变化时再次提示。条件不再命中只描述来源条件，不表示业务成功。

管理台入口 `/analytics/alerts`，提供独立筛选、标准分页、详情抽屉、关联计时/阶段/安全审计及已阅操作。来源 ID 仅供排查，关联接口仍单独校验当前租户权限。扫描缺失、失败、旧终态超出扫描窗口或扫描落后时明确显示未知/过期，空列表不宣称健康。

## 租约、留存与运行口径

| 项目 | 当前默认值与依据 | 边界 |
| --- | --- | --- |
| Worker 服务租约 | 心跳 5 秒，租约 15 秒；租约必须大于两倍心跳间隔 | 总览和健康协调共用到期时间、healthy/degraded状态与心跳最多超前5秒的SQL条件；共享进程事实，不伪造 per-Agent 进程租约，独立于执行许可 |
| Run attempt | 租约 60 秒，续租 10 秒，恢复最多 2 次 | 当前 generation/owner/会话许可及有效租约共同决定可执行；未知副作用不能直接重跑 |
| 独立动作 | 租约 60 秒、目标超时 5 秒；执行最多 2 次、自动对账最多 6 次、手工对账最多 10 次 | 超时保留 unknown；告警确认不增加额度或清零次数 |
| 健康快照 | Worker 默认每 30 秒重算，每 Agent 一条当前快照，覆盖旧读模型；状态 GET 只读，不重算 | `SUPPORTOPS_HEALTH_SNAPSHOT_STALE_SECONDS` 默认 90 秒；computed/observed 任一缺失、超期或比 API 当前时间超前超过 5 秒则 stale/unknown，错误率/P95 不作为当前有效指标展示。缺快照时间为空，不提供快照历史 TTL |
| checkpoint | 默认留存 30 天，清理周期 3600 秒 | 现有清理服务维护 checkpoint，不代表所有业务/审计数据已具备保留政策 |
| 健康统计 | 按创建时间取 `[observed_at - 24h, observed_at)` 的 completed/failed，取消排除；错误率至少3个终态样本，P95至少3个非负有效耗时样本；错误率 ≥50% 判降级 | P95 使用 nearest-rank，缺失/负耗时不凑样本数；活跃/等待另按全时段事实统计，不把历史失败率当告警 SLO |
| 待发布说明 | 当前草稿与活动版本差异、缺失或损坏时标记待发布，说明当前运行版本未更新 | 保留失败/服务不可用等主健康原因；草稿恢复后GET移除缓存的待发布说明，不重算或改写健康快照 |
| 首输出阈值 | `SUPPORTOPS_RUNTIME_ALERT_FIRST_OUTPUT_MS` 默认未设置，不启用超限规则 | 配置前应批准模型 SDK 首输出的毫秒阈值；示例 5000 不是已承诺生产 SLO，客户端时钟不参与判定 |
| 观测缺失 | completed 且结束后超过默认 60 秒宽限，再检查模型次数、首输出、usage、成本 | 无模型调用不要求模型字段；缺失保留未知，客户端未上报不自动报警 |
| 告警 stale | 最近成功来源观测超过默认 300 秒；扫描缺任一来源状态或最旧成功批次超过 300 秒则扫描 stale | 批次时间不代表整轮已完成；stale 不能当作条件解除或业务恢复 |
| 告警留存 | 当前没有自动删除或过期清理 | 扫描窗口限制观测范围，不是留存期限；0039 不补造旧告警或已阅历史，降级遇到历史会拒绝 |
| 小时聚合启用阈值 | 当前未启用，也未批准数值阈值；继续基于原始事实查询 | 需先取得隔离负载下 DB 查询 P95、行数/增长和预算证据，确认索引/分页仍不足，再批准容量与查询延迟阈值及启用方案；不可凭空填写实测阈值 |

当前业务表数量为54，新增迁移源截至0043尚未执行；0039 告警、0040 登录会话、0041发布清单、0042评测事实及0043撤回保护不因此获得生产验收。新功能未执行测试、类型检查或构建。OpenTelemetry 导出、外部告警渠道和生产容量/留存审批仍待后续任务。内部历史摘录不进入管理 attempts 正文，管理响应只保留来源元数据。
