# 安全边界

- JWT 的 `sub` 是系统用户 UUID，用户停用后已有令牌失效。
- 所有资源查询强制租户过滤，管理接口不接受客户端传入租户或扩权角色。
- 模型密钥只写入 Secret Provider，API 和日志只返回 masked hint 与 revision。
- Agent 运行工具来自服务端注册目录，按版本绑定、租户和用户角色取交集。
- 日志、错误、Observation 和 provider 返回值执行字段白名单及脱敏。
- 隔离Mock已实现非本人持久化审批、参数冻结、一次消费及独立动作复核；真实高风险连接器仍未接入，不能仅依赖Prompt或审批配置字段取得许可。

## 固定凭据状态与受限解封

共同assert_credential_revision_active只查询修订ID、端点、provider、撤销时间，显式限制可信租户、credential_id/revision及父子未删除。返回不可变元数据，不含历史密文、提示、撤销说明或可延迟加载秘密的ORM实体；即使内部会话含include_deleted选项也不放宽状态查询。领取/发布/健康/探测等调用方继续只检查，内部返回类型已核对；此处不表示其他调用方已有凭据实体查询均已收窄。

resolve_credential_secret先核对端点和provider，再用单独SQL同时限制原修订ID、租户、credential_id/revision、端点/provider、未撤销及父子未删除才读取密文。读取不命中时拒绝，可只重查元数据保留撤销等既有错误码，不重读密文或换revision。provider普通异常、非字符串/空白返回统一MODEL_CREDENTIAL_UNSEAL_FAILED并隐藏原异常链，不打印诊断/密文/明文或自动重试；BaseException取消仍传播。成功解封后保留原最小访问审计和独立事务策略，返回密钥前再查当前元数据并匹配原ID/端点/provider；解封后遇到撤销可留下访问事实，不代表已返回秘密、调用或业务成功。

变更不提供跨数据库与SDK出站的原子撤销、全局凭据读取隔离或销毁恢复保证；后续调用/提交授权边界继续复检。无新文件/依赖/表/API/迁移，credential_services已在发布清单范围；未实际解封、读取凭据或执行测试、类型检查、构建、数据库和网络验收，W20.3仍等待企业provider/身份。

当前凭据和历史修订的encrypted_secret现统一采用ORM默认deferred/raiseload：默认实体查询不携密文，未显式加载时误读属性直接拒绝，不发起隐式补查。创建端点/连接配置和轮换各复用一次seal局部结果写入当前凭据及新revision，不从实体回读密文或读取旧材料；受控解封继续使用上述带固定范围与当前状态的显式列查询。这里扩大了默认实体查询的保护，显式可信SQL/undefer、此前已加载或赋值对象仍不由此隔离，也不替代角色/进程分权。

仅ORM加载策略改变，无列/约束或迁移变更；models/model_services加入新发布源码摘要，旧清单保持。源码已发现旧test_agent_management的修订实体密文直接读取需在恢复测试时按新内部契约适配，本轮不修改或运行测试、不为旧断言放宽生产读取。未运行类型检查、构建、数据库或实际seal/open，企业provider/身份与W20.3仍待。

解封后的CREDENTIAL_RESOLVED访问审计现统一采用单调2秒等待预算，覆盖独立会话建立/事务写入/提交及退出，PG独立事务语句1秒/锁250毫秒。退出后仍检查截止，驱动迟到完成不能放行返回秘密；原成功审计后固定范围/当前状态复核保留。普通审计异常/超时统一MODEL_CREDENTIAL_AUDIT_UNAVAILABLE并隐藏原SQL/参数/异常链，不自动再次解封或重放审计，外部取消传播。提交结果未知可能已保存访问事实，不能视为未解封或未记录，也不删除原事实重试。

API把审计不可确认映射为503/no-store，其他凭据拒绝保持403/no-store；Run/探测沿既有凭据失败分支保存该码且不自动恢复。无工厂核心兼容调用只限定原flush等待，由调用者管理事务，不SET其SQL限制或自行提交/回滚，也不能据此声称独立审计已提交。等待预算不保证驱动取消/回滚/关闭硬截止；无新文件/表/依赖/迁移，未实际解封、记录审计、连接数据库或运行测试/构建，企业provider与服务身份仍待。

创建/配置保存/轮换的写Key统一经过_seal_model_credential，API/Core/LocalEnvelope复用secrets的8–20000字符边界并去除首尾空白；编辑null/未提供保留原值，显式空字符串不忽略。输入无效返回422且不调用provider；普通密封异常和非字符串/空白/原样输入结果固定MODEL_CREDENTIAL_SEAL_FAILED并隐藏原异常链，不自动重试或用旧Key完成保存，取消继续传播。创建先密封后新增端点，轮换先取得结果再改凭据/revision，复用一次结果写当前与新历史，API原事务负责失败回滚配置变化；结构检查不证明任意provider加密正确，也不进行open验证。

CredentialServiceUnavailable将密封失败与既有审计不可用统一为HTTP503/no-store，其余凭据拒绝仍403/no-store。Schema边界和配置表单长度提示/错误aria关联同步，没有新增URL或响应字段；仅用于模型发现的请求Key不持久化，原契约不变。secrets加入新发布源码摘要，旧清单不回填，无新文件/依赖/表/迁移；未运行测试、类型检查、构建、浏览器、数据库或实际seal/open，企业provider/身份仍待。

轮换的FOR UPDATE查询使用populate_existing刷新同会话预加载的当前元数据，再从锁定后的revision递增，显式限制可信租户/端点及未软删除。配置保存使用轮换返回值，避免等锁期间其他事务已轮换而仍按旧revision追加历史。默认延迟映射不选择旧密文，当前值、新历史和审计沿原事务保存；不自动重试、覆盖历史或替换Run固定引用。未运行数据库竞争验收，不能把SQLite无行锁执行当作PG并发保证；企业provider与身份仍待。

凭据撤销面板按端点重挂载并固定登录会话，确认保留目标ID/revision与打开时的当前revision；已知修订变化须重新确认，不借后续登录令牌发送旧意图。延迟查询与迟到结果复核会话/scope/序号，提交使用同步围栏，回执匹配目标及撤销时间/人员才提示成功。失败/未知仅关闭旧确认并只读刷新，不自动重发POST；详情/两列表GET与撤销成功响应no-store，Web对应禁缓存，读不到记录不表示原撤销未发生。未新增持久化令牌或秘密字段，服务端身份/权限/不可覆盖决定仍是授权依据；未运行浏览器或故障验收。

## 历史凭据、证据和分块保护

W21.6新增`reference_history`并接入现有Session before_flush：历史凭据固定列、知识证据及分块实际列变化拒绝；只允许凭据首次完整撤销及单向软删除，旧值未加载时保守拒绝，不自动读取正文或解封凭据。轮换仍更新当前凭据并追加新revision，不覆盖旧密文。已存在的撤销时间/人员/说明及软删除时间不能清除或重写。

迁移0049另为`model_credential_revisions`、`knowledge_evidence`、`knowledge_chunks`配置行UPDATE/DELETE和语句TRUNCATE保护，固定public函数、pg_catalog搜索路径且ALWAYS启用；应用后覆盖PG批量和原始SQL，SQLite/未迁移环境不具备这层保证。实现依据[PostgreSQL触发器事件](https://www.postgresql.org/docs/17/sql-createtrigger.html)和[触发器启用方式](https://www.postgresql.org/docs/17/sql-altertable.html)；数据库所有者/超级用户仍能通过DDL移除保护，独立维护身份/权限隔离仍待，不把ALWAYS视为完整沙箱。

三表任意有历史时拒绝降级；升级不扫描、修复或认证旧内容。保护历史字节不赋予读取/执行许可，凭据撤销/软删除和证据当前ACL/有效期仍沿原规则拒绝，不替换固定引用。没有实际擦除、密钥销毁、tombstone生成/重施或恢复保证；未来获批擦除须单独设计可审计维护路径，不能临时禁用保护绕过。源码纳入发布清单，无新表/依赖/API；0049与测试/类型检查/构建均未执行，21.6保持部分完成。

W22.4另补具名历史保护目录核对：现有只读共享维护锁事务按[固定范围](../../../deploy/runtime/README.md#历史保护的专项目录核对)检查上述六个触发器和两个函数的形状/启用/OID绑定、invoker/固定配置及原迁移函数体摘要，目录读取有界，缺失/漂移/不可读固定拒绝，不执行函数、业务查询或修复。它只报告检查时的具名定义匹配，不验证全部触发器、运行角色权限或历史内容，也不阻止绕开维护入口的DDL；通用triggers_verified=false保持，未执行预检或故障验收。

## 证据上游的版本、快照与代次保护

W21.6进一步扩展普通ORM实体保护：KnowledgeVersion的正文、标题、摘要、章节锚点、变更说明、起始有效期与创建定位不可原地更新；审核字段沿原服务维护，发布人/时间仅首次完整记录，已发布版本退役只允许缩短有效期。KnowledgeSnapshot仅允许单向软删除，其余固定；KnowledgeIndexGeneration仅允许构建状态字段，快照/算法/摘要/编号等定位固定。所需旧值未加载即拒绝，不为检查加载正文；更新原文或重建索引分别创建新版本/代次，当前ACL/有效期/发布指针仍复检。

0050接续0049，为新三表增加ALWAYS行UPDATE/DELETE及TRUNCATE保护，TRUNCATE复用原拒绝函数，不改旧迁移或旧内容。降级先排他锁新三表再判空，有历史拒绝。预检当前契约升级为reference_history_schema_v2/named_0049_0050_guards，限定六表十二触发器三函数，按表核对行函数绑定及固定原始函数体摘要；函数最多4条、触发器13条、单体16KiB。此前0049/v1描述为前次交付，当前范围以此处及部署模板为准；其他触发器/权限/历史正确性仍未确认，所有者DDL不受此隔离。无新表/依赖/API；未执行迁移、预检、测试、构建或实际数据操作，独立擦除和恢复重施仍待。

## 本人预览的固定引用说明

数据申请新报告仅在既有tables.reason_code中追加固定引用说明，不返回资源ID、正文或密文。凭据修订按本人截止时间内Run实际保存的credential_id/revision组合统计；会话固定Agent/模型版本及其凭据、本人Run证据引用的快照/分块按集合计数，包含软删除历史，不枚举其他主体的引用。分块缺创建时间，按对应证据检索截止限定，不补造时间。已知非零引用增加FIXED_REFERENCES_PRESENT，法律保留/未覆盖依赖/不可擦除标记保留；在途Run/未确认动作仍独立当前计数，不与这些历史实体相加当可删数量。

页面只对所选knowledge/credentials展开可识别的共享引用说明，显示固定中文范围/数量/依赖，不透内部集合名；缺失/非法计数或旧未记录不当零。详情发送及接收复检当前token和请求ID，渲染也绑定当前token/申请；迟到或错配结果不展示，不重放请求。旧报告存储内容及摘要不改写、不重算；这些元数据不能证明当前凭据/知识可用、0049保护已部署或盘点完整，不新增批准、维护或擦除许可。API/Web同步，无新字段/表/路由/迁移/依赖，未执行数据库、测试、类型检查、构建或浏览器验收。

## 知识正文读取与拒绝审计

显式知识快照和本人Run引用展开在共同文档锁下检查当前ACL/版本/有效期并完成投影。引用定位在ACL前只读快照ID、文档、版本和摘要，实际分块/标题SQL也直接限制可信租户、当前发布指针、有效期及用户/角色/部门ACL；管理员角色不产生隐式正文许可。成功读取响应及知识专属403/引用404使用no-store，其他内部调用继续原事务策略。整体读等待5秒/PG语句4秒/锁1秒，数据库失败为既有KNOWLEDGE_CONFLICT，不能用旧正文兜底或伪记权限拒绝。

这两个已认证入口拒绝后，先结束原请求事务，再以独立短事务尝试既有KNOWLEDGE_ACCESS_DENIED审计。只存可信principal的actor/tenant、固定操作/结果/requested_only及租户域内请求目标/可选Run/关联值SHA-256，不存原始目标/Run/关联值、标题、正文、查询、令牌或原异常，也不查询另一租户来判断存在。关联摘要恰为64位；回滚后不访问可能已过期的User ORM实例。

审计等待2秒/PG语句1秒/锁250毫秒；写入失败只记录固定可用性告警，原读取仍拒绝，不提交原事务、自动重试或赋予权限。不是可靠Outbox或全部拒绝必达保证，身份依赖前401、工具网关和审核等其他拒绝继续各自契约。等待预算不承诺驱动清理硬截止；新增核心源码进入发布清单，无新表/迁移/依赖，未运行测试、数据库或并发/性能验收，W11.1仍未完成。

## 知识来源读取与解析隔离

可选`local_markdown_isolated_reader_v1`将来源文件IO交给独立10006身份，API仅通过专用认证Unix通道发送固定范围摘要/期限，不传目标路径或业务凭据。读取器只访问/source独立只读挂载，拒绝嵌套挂载，根目录设备/inode还须命中root:0444独立策略；该策略同时冻结scope、明确文件清单、读取/解析实现摘要和有效期，启动/读取前后重读一致。源路径验证与原v1选择摘要在两端复用，NOFOLLOW/单链接/普通文件/别名、每文件1MiB/批次4MiB和前后事实复检保留。不扫描目录、跟随链接、读取未批准路径或输出原文件名；源文件名仅在受保护配置和读取器内使用。

读取器10006、解析器10005各自只持专用socket卷，network none/只读根、空环境和有限CPU/内存/FD/进程，均不持DB/Redis/模型/密钥或应用启动权限。10001与两端分别SO_PEERCRED核对，结果绑定nonce/原请求SHA/全部范围摘要，客户端共享原deadline、未知不重试或回退。来源策略不代替当前用户/Job/ACL授权，父进程和原提交事务仍独立复检；旧v2/v3只在原批准路径保留，旧隔离源码摘要不匹配须新scope/Job，不静默升级。此处交付实现/模板，默认关闭及正式环境禁止不变，未配置来源/策略、启动容器或执行隔离/故障验收，不能据此声称完整OS隔离或真实导入已通过。

## 固定目标的 HTTPS 出站控制

`outbound_http.py` 提供 `OutboundHttpPolicy` 与 `request_fixed_json`，只用于服务端选定的固定目标。域名或路径允许列表为空时拒绝全部请求；只接受HTTPS，禁止URL内嵌凭据、query/fragment、反斜杠和控制字符。域名按IDNA规范化并精确匹配，无通配符；路径按`URL.raw_path`的ASCII编码精确匹配，不能切换到同域其他接口；端口须显式获准，默认仅443。调用接口不接受模型指定的请求头、代理、TLS设置、证书或重定向策略；目标URL和策略必须由获准适配器从服务器固定配置构造，不能直接转发工具参数中的URL。

每次请求重新解析全部DNS地址并逐个检查，选择一个获准IP固定连接，同时使用原主机的Host与TLS/SNI证书校验。默认只接受公网地址；内部企业目标只能额外列入确切RFC1918/ULA私网IP，不能打开全局私网开关。回环、link-local、保留、组播、未指定地址始终拒绝。可选`allowed_ips`进一步要求全部解析结果均在确切集合内，未提供时也不跳过地址类别检查；混合公网/禁止地址不会只挑安全地址后继续。

DNS在`python -I -S`标准库子进程执行，不继承应用环境/普通文件描述符，stdin/stderr关闭；结果最多32个地址/4096字节。请求的整体超时覆盖解析、连接、TLS、写入和读取，取消/超时会终止并回收仍在运行的解析子进程，避免线程解析无法协作取消。该子进程只负责DNS，不是连接器沙箱。传输关闭环境代理/CA覆盖、重定向、压缩和HTTPX自动重试，只支持GET/POST与有界JSON请求/响应；默认请求≤1MB、响应≤64KiB、整体10秒，调用方最多设置4MB/30秒。认证仅由受控调用方注入可选Bearer，不接受任意头透传；平台JWT不能作为下游业务凭据。

响应只返回HTTP状态与有界bytes，repr隐藏body；工具适配器仍须使用固定Schema/字段白名单/脱敏与网关来源证明，Content-Type并不证明内容合法。所有3xx直接返回固定拒绝码，不读取Location后重试。异常仅有`OUTBOUND_*`固定码和可选HTTP状态，不附带URL/原异常/响应正文。只读重试由获准适配器按原固定范围有界管理；写失败/取消/响应丢失不证明未执行，必须保留原动作幂等键并按W15对账，传输层不负责判定业务终态。

目前已接入OTLP维护发送，仍需显式租户/目标/IP配置和`--send`，没有实际联网；原模型SDK仍沿用其模型专属地址策略，不因新增工具传输层自动改变。真实API/MCP适配器、服务身份/短期凭据、部署层出站隔离和连接器沙箱待实现；未来适配器必须接入此边界并完成自身契约，不能仅有helper就宣称所有出站路径已受控。未运行安全/网络/故障/性能验收或新增测试。

## HTTP MCP的受众、scope和撤销编排

原派发查询是独立历史元数据入口：`mcp_http_dispatch_queries`明确按列投影，只读本人Run且原actor也须匹配，管理员身份无额外数据权。查询原派发不依赖已失效的原执行租约/Agent grant，从而允许本人检查原记录；该历史访问不恢复任何调用许可。SQL不取request_payload/token_state、令牌摘要、lease或领取nonce，API/Web成功读取禁缓存；聊天按Run/当前会话/请求序号和关闭生命周期隔离结果。bound/claimed/过期都不证明远端业务结果或撤销；GET不访问broker/目标，不取密/重发/撤销，也不把空记录当作未执行。

W8.7先提供`mcp_http_authorization_contracts.py`与`mcp_http_authorization.py`通用授权库，后续已补入下述无会话HTTP客户端、应用侧数据库审计、持久化一次领取broker和认证Unix IPC。当前没有获准HTTP MCP服务或真实OAuth provider、独立HTTP进程部署与产品执行接线；默认broker/审计通道仍None，直接拒绝，不从平台JWT、模型密钥或本地密封凭据回退。代码存在不代表真实服务已授权、可达或已通过验收。

可信策略固定HTTPS issuer/resource、来源record/策略摘要、租户/主体、调用/attempt/generation/dispatch、工具实现/描述摘要及凭据provider/ID/revision。单一受众必须精确匹配resource，scope最多16项/每项128字符且须恰等于批准集合，重复/额外scope拒绝；目标地址只接受明确规范形式，不根据401或远端描述自动改变批准。依据固定[MCP HTTP授权规范](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)及[RFC8707](https://www.rfc-editor.org/rfc/rfc8707.html)，但完整资源元数据发现、OAuth交互/同意与受控提供者仍待；这里不实现自动发现或默认获取全部scope。

broker协议要求独立复检当前主体/资源/调用/审批/围栏、原子消费持久化dispatch，并验证令牌签名/内省，UUID与摘要本身不是执行凭证。本轮没有用内存表或空实现代替这些要求。编排只接受broker返回的SecretStr及封闭元数据，检查SHA绑定、固定范围、10–300秒寿命（默认60）、至少5秒剩余预算、批准/调用截止及撤销。平台issuer/audience的JWT和损坏JWT额外拒绝；未验签claims不能用来证明令牌合法，签名/内省与目标服务器验收仍须由获准provider和服务完成。

`run_authorized_http_mcp_call`在取密后、审计等待后、固定可信适配器返回后及最后审计等待后复检当前许可与凭据状态，任一变化拒绝结果；一次claim、一次适配器回调，无自动刷新/换revision/重试/scope升级。主工作等待15秒、claim6秒、其他provider/审计2秒、适配器最多10秒且裁剪到令牌期限；工作失败后的未确认审计另外最多等待2秒，已知审计失败不再次尝试记录。子步骤共享总预算，任一步超时均拒绝，清理没有硬截止保证。回调使用独立身份固定适配器，并继续使用出站控制和输出过滤；通用库自身不证明OS隔离、HTTP MCP会话或网关许可已经接好。

撤销只定位原lease，先查询固定范围，再由broker以当前独立撤销权限处理并回查真实revoked_at；除该字段外元数据须不变，已有撤销时间不可覆盖。过期执行许可不阻止另行授权撤销，也不授予撤销权限；主工作等待10秒，失败后的未确认审计另外最多2秒，未知不自造撤销事实或重发。最小审计只含请求摘要、dispatch/lease、固定阶段/结果码，不含令牌/目标/scope原文；审计不可确认不放行。`call_started`仅表示进入回调边界，任何失败/取消不能推断外部未执行；`call_confirmed`也不是业务verified、产品提交或以后授权，写动作仍须W15对账。

`mcp_http_client.call_http_mcp_tool`把授权编排接到实际HTTP请求实现，限定stateless_json_v1：策略必填server_name/server_version，调用必须有arguments_digest，首次await前冻结参数和Schema，匹配原来源工具别名/描述/参数SHA。四个POST分别初始化、initialized通知、单页目录、一次工具调用；官方SDK1.30.0只提供协议类型，网络复用现有固定IP/TLS传输，不启用SDK的动态重定向、GET重连或原始协议日志。SDK及其已有jsonschema依赖延迟加载，未来HTTP执行镜像须显式安装现有mcp-isolated extra，默认API/Worker不增加SDK依赖。

每次交换前后复检，DNS完成后、连接固定IP前再复检；单交换3秒且仍受原适配器10秒/主工作15秒约束。MCP模式只添加固定Accept及协议头，无任意头；通知只收202空正文，其余只收200 JSON，含session ID、SSE、分页、未知能力/指令或目录描述不符即拒绝，无DELETE/重连/自动重试。这是受限JSON模式，不是通用Streamable HTTP支持；真实服务须批准该模式。策略的新字段和参数摘要进入请求SHA，旧内部请求须重新构造，不静默升级或复用旧lease。

参数最多32KiB、响应128KiB、结构化结果64KiB、16层4096节点，重复键/非有限值/坏UTF-8拒绝；固定封闭输入/输出Schema用Draft202012Validator本地校验，引用一律拒绝，不解析文件或网络引用。文本镜像如存在须规范JSON相同，bool和number不混同；输出共同脱敏并遮蔽本次令牌，定位字段/键需脱敏则整份拒绝，再校验Schema和大小。同步Schema校验没有CPU硬截止保证，真实启用仍需进程资源隔离。观察显式标为不可信、无执行许可/业务确认，仍须将来网关注册/过滤/来源证明和独立业务复核。

`DatabaseHttpMcpAuditSink`是应用侧独立持久化实现，通过现有AgentAuditEvent及新mcp_authorization_event阶段存储；原列为VARCHAR64非原生Enum，无DDL变化。只选原tenant/actor/Run/attempt/generation/invocation/工具实现/参数SHA及来源历史绑定元数据，包含停止/过期/软删除历史以记录失败，不赋当前执行许可；严格阶段/成功码/lease组合后追加最小关联，无URL/scope/令牌/正文/SQL原文。2秒事务/PG语句1秒锁250ms，提交不明或迟到不确认、不重试。新审计事件需新API/Worker同步支持；写入器不能拿给隔离连接器，后者经下述认证IPC请求审计，不能注入DB权限。

`mcp_http_broker`及0053新增第69表`http_mcp_dispatches`。可信网关按会话→Run→attempt→调用锁序保存固定非秘密范围，复检当前身份/grant/版本/模型凭据/目录/来源及租约，且须独立上游批准目标资源和凭据；仅R0/R1无业务动作。同invocation全局最多一份许可，范围/截止不可改写，最长300秒且不超原attempt租约。broker先独立提交claimed/一次nonce才取密，校验后只一次绑定非秘密token状态/摘要/lease，明文不入库。取密/提交/回传未知永久保留claimed或bound，不重取、不缓存令牌；提供者侧未知lease需按原dispatch独立对账。原lease的首次真实撤销时间单向保存，历史禁止删除/截断、有记录拒绝降级；保留预览只计本人关联，不授予擦除许可。

`mcp_http_broker_ipc`提供固定`/run/supportops-mcp-broker/control.sock`的客户端/服务端及`call_http_mcp_tool_via_ipc`。Linux SO_PEERCRED双向核对broker10003/执行器10004；目录须10003:10004/0770，socket须10003:10004/0660，不覆盖已有socket或退回路径鉴权。每连接一条32KiB严格JSON命令，nonce/operation/dispatch/摘要匹配，只允许assert/claim/current/revoke/audit，broker从DB解析已保存范围，不接受任意URL/命令/完整policy/凭据路径。最多4并行，无队列/重连/重试。SecretStr仅在认证socket发送处显式展开，返回前再次核对当前许可/原lease，执行器不导入DB；审计在broker侧核对原dispatch/lease后持久化。子请求超时或通道丢失只表示未确认，不以断开连接推断提供者未取密/撤销。

`mcp_http_executor_contracts/runtime/cli`把原IPC调用适配接成固定10004 UID/GID的单次进程入口。读取输入前检查Linux、-I/-B、精确清空环境、无额外继承FD、NoNewPrivs/零有效能力/seccomp模式及有限CPU/地址空间/FD/进程/core；输入输出必须管道/socket。只接受root0444非秘密策略及root0755固定目录，拒绝链接、读取中变动、超32KiB或过期；完整原授权策略/平台标识/最多32个规范精确IP固定，私网仅逐个批准RFC1918/ULA。从固定resource生成唯一域名/端口/路径，不接受输入扩大出站许可。该短命进程冻结一次策略，文件替换不冒称即时撤销，broker当前授权检查继续独立生效。

stdin最多192KiB/3秒一条紧凑换行JSON，部署策略/调用/描述/参数SHA及原授权范围必须匹配才调用原IPC/HTTP；不等待EOF或消费第二次调用，不接受token/命令/凭据路径。stdout最多128KiB/2秒非阻塞写入，保留原nonce/dispatch/摘要、脱敏不可信观察或固定码，execution_permit/business_status_confirmed恒false。工作等待20秒、25秒SIGALRM退出保护；SIGTERM/SIGINT、强制退出或输出未知不重领/重发/清除派发，已有claimed/bound保留，内核不可中断I/O没有硬实时保证。日志禁用、原始stderr丢弃，秘密仍只在认证socket/内存。

独立Dockerfile/显式profile Compose使用10004、只读根/有限tmpfs、cap-drop/no-new-privileges及资源上限，无应用env_file/业务卷/端口/TTY/自动重启，logging=none避免观察持久化到容器日志；仅挂非秘密root策略与专用broker socket。镜像、策略、socket卷和外部批准出口网络必须维护者显式提供，不创建宽泛网络或给予API/Worker Docker权限；网络名不是防火墙证明。模板及前置检查不认证过滤器内容/全部挂载/精确OS出口，也没有替代真实provider/签名或内省或产品固定工具注册。

`mcp_http_jwt`补入可选固定公钥JWT访问令牌验证，复用锁文件PyJWT2.13.0和既有cryptography，不修改平台HS256登录认证。仅RS256/at+jwt及维护者批准的1–8个RSA2048–4096位/指数65537公开JWK，header只alg/typ/kid，动态jku/x5u/内联key/crit/未知kid拒绝，不下载或自动轮换key。规范base64url/最多8KiB、header1KiB/payload6KiB、严格重复/非有限/深度节点及封闭claims先作拒绝；真正信任前强制固定算法公钥验签及issuer/单一字符串aud/显式external_subject、client_id、精确scope、严格整数iat/exp/nbf、jti和零宽限时间检查，时间与原lease/批准截止一致。不从平台UUID或模型输入推导外部sub，不把未验签claims或SHA当作许可。

`VerifiedJwtHttpMcpCredentialBroker`必须显式注入真实上游、固定验证policy与可信当前策略解析器，取密前后和每次正常调用许可边界复核完整公钥/外部subject/client映射及期限；变化拒绝，不缓存令牌/旧active或静默换key。`create_jwt_verified_http_mcp_broker`固定构造Durable→VerifiedJwt→真实上游，返回原认证IPC可用的持久化broker，确保先提交一次领取后取密验签。验签拒绝/未知不重领或清除claimed。原lease的current读取及独立撤销不依赖旧公钥仍有效，撤销仍由上游当前权限及真实首次撤销时间确认。该库不签发令牌、实现consent/通用内省/动态JWKS，也不提供全局jti重放检测或取代目标系统授权；未知扩展claims/多aud/其他算法profile目前均拒绝。

新增模块、outbound_http与enums纳入后续发布清单，旧清单不回填；当前69表/0053，无新依赖/公共HTTP接口、实际公钥/外部主体映射/获批策略或运行部署。W8.7/W20.5仍待实际服务、真实provider/签发撤销/主体映射、broker启动/精确OS网络隔离及产品工具注册；现有配置/静态工具不接受远端别名，不能靠保存派发表或模板绕过。按用户要求未写或运行测试、类型检查、构建、Compose检查、容器、迁移、数据库、真实验签/取密/撤销或网络验收。

## 工具响应进入模型前的过滤

HTTP执行桥接补充：`mcp_http_executor_ipc`为10001应用身份到10004执行器提供固定`/run/supportops-mcp-executor/dispatch.sock`，独立于10003 broker凭据socket；目录10004:10001/0770、端点0660及双向SO_PEERCRED，最多一次连接/写入、23秒等待，nonce/dispatch/request/source/descriptor全部匹配。执行器`serve-once`首个认证连接后停止监听，复用原策略/IPC取密/HTTP路径；坏帧也不接受第二次请求，退出只清理自身同inode端点。部署需10004附加10001组及独立可写派发socket卷，broker卷保持只读，不赋应用Docker权限/业务令牌。

`dispatch_authorized_http_mcp_tool`接好已有ToolInvocation/同factory的持久化broker、一次派发和结果后验：冻结→上游资源授权/平台门禁→保存原许可→只发送authorized→核对回执/时间/固定输出Schema及预算→DB当前许可/原绑定lease/同dispatch与lease的持久化call_confirmed→上游当前lease/授权复检。应用不取业务令牌，执行器不取DB；缺jsonschema依赖也拒绝，不关闭校验。只返回不可信观察，尚未接入Worker/Agent产品链及统一模型来源证明，不自动发布/注册工具。未知或取消保留原派发，不能靠回执失败重领/重发，未运行测试/容器或网络验收。

所有已注册工具返回均通过`normalize_tool_result`，身份摘要也有独立封闭Schema。未知工具、额外字段、非字符串消息正文或调用ID不符直接拒绝；不从SDK文本块拼出正文，不保留工具指定名称、artifact、额外kwargs或响应metadata。JSON先检查重复字段、有限数值、对象根、16层/4096节点上限，再使用[Pydantic严格JSON校验](https://docs.pydantic.dev/latest/concepts/strict_mode/)；UUID/时间保留其JSON表示，不把字符串数字强转为数值。运输层`success/error`必须与已知领域返回状态对应，查询成功/提案取得不代表业务动作已完成。

通过Schema后统一遍历全部文本字段，包括guidance、标题和诊断观察，复用`redact_sensitive_text`。完整Authorization/Proxy-Authorization、Bearer/Basic、Cookie/Set-Cookie值、引号转义秘密字段、API密钥样式及多行PEM私钥均在相应模式下遮蔽；网关还在进程内传入本轮模型密钥、JWT和密封主密钥进行精确替换，不将这组秘密写入账本、信封或模型参数。此处是已知值/模式过滤，不是任意敏感内容识别。共用脱敏改进也应用于原有审批理由/升级内容调用方。

任何来源`*_id/*_digest/*_key`若需要脱敏则整份拒绝，不能替换定位后声称仍为原来源。知识`content_is_excerpt`继续表示上游预算摘录；本次网关另写`content_is_redacted`表示正文是否被改写，旧未记录字段为null。`content_digest`始终对应原始分块，脱敏后的文本不能声称逐字完整原文。保留片段原有空白，脱敏后再次校验Schema和完整序列化字节预算；字段或整个结果超限拒绝，不截断JSON或静默丢弃证据。验证异常移除原输入异常链，错误仅提供固定业务码/说明。

过滤后的同一份结果进入调用账本摘要及`model_tool_data_v1`信封，实际ToolMessage仍按同租户/Run/attempt/调用记录核对；目录/角色/来源权限和时效复检、已用来源并集及冲突停止规则继续生效。不可信文本不成为系统指令或执行许可，过滤不取代服务端授权、审批、独立复核，也不声称通用Prompt Injection语义检测。真实API/MCP工具尚未注册，必须补充获准的固定输出Schema后才可启用，不能直接返回原始上游日志。当前仅完成代码/契约核对，未编写或运行测试、浏览器、性能或安全验收。
