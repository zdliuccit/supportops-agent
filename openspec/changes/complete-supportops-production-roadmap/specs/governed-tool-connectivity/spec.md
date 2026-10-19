## ADDED Requirements

### Requirement: 统一工具执行入口
所有本地、业务 API 和 MCP 工具 SHALL 经同一授权、Schema、限额、风险、审批、输出过滤与审计边界；Registry SHALL 只提供发现与治理元数据。

#### Scenario: 不同协议相同风险
- **WHEN** 本地工具与远程 MCP 工具都尝试执行同类高风险动作
- **THEN** 均受相同门禁，不因适配协议不同绕过控制

#### Scenario: 新来源工具
- **WHEN** MCP Server 返回未知或变更的描述/Schema
- **THEN** 先做来源/版本/allowlist 审核，未经授权不暴露给模型

### Requirement: 工具发现的独立治理控制面
系统 SHALL 提供同租户管理员的来源批准、失效、目录提交和固定描述解析。目录仅提供元数据，不自动创建ToolCatalogEntry、授予Agent绑定或执行许可，不接入业务调用链；MCP协议适配与连接管理界面独立交付。

#### Scenario: 精确来源和工具批准
- **WHEN** 管理员以当前来源修订批准来源指纹、工具全描述SHA-256 allowlist和带时区期限
- **THEN** 追加新的pending或disabled修订，初始expected_revision=0且默认disabled；同租户来源修订冲突拒绝，来源别名不接受URL/凭据。目录只有封闭对象输入/输出Schema及描述摘要均匹配的工具可进入当前快照，未知工具描述不存储/不暴露

#### Scenario: 不同来源存在同名工具
- **WHEN** 两个来源或租户返回相同远端工具名
- **THEN** 按固定租户/来源/远端名规范JSON的SHA-256生成mcp_稳定别名，目录同时固定原名、全描述及输入/输出Schema摘要，不能仅凭名称替换其他来源或历史实现

#### Scenario: listChanged或批准发生变化
- **WHEN** 当前批准指纹/策略匹配的listChanged或手工刷新追加pending修订，或管理员重新批准/停用来源
- **THEN** 旧record_id/catalog_digest绑定不再可解析；新目录只接受当前pending修订及同来源/策略，迟到旧目录409拒绝。获批工具缺失/描述变化/损坏目录追加rejected，旧快照不可覆盖或复活；须显式刷新或新批准后重新验证，自然到期同样隐藏绑定

#### Scenario: 不可信目录输入
- **WHEN** 目录超过128KiB/100工具/4096节点/16层，存在重复键或名称、非有限数、无效文本、Schema引用或未知契约字段
- **THEN** 固定目录拒绝码，不保存未知工具描述、远端诊断或部分可用结果；不解析引用、联网或执行Schema。批准最多50工具，固定描述解析仍复检当前管理员/会话/租户/期限与当前修订

#### Scenario: 治理历史与接口
- **WHEN** 来源配置、失效或目录结果写入
- **THEN** 同租户锁下追加唯一修订及最小审计，ORM禁止改写，0051的PG ALWAYS触发器拒绝改写/删除/截断并核对前驱；有历史拒绝降级。五个管理接口均no-store，当前来源GET为最多50项绑定的单项有界聚合，不作普通列表分页；没有MCP网络或工具执行，未知提交只GET原来源核对，不自动重发

### Requirement: 连接与出站安全
连接器 MUST 校验目标地址、重定向、响应大小/Schema 与超时，并使用明确受众和最小范围凭据；不得把平台用户 JWT 任意转发或允许模型指定秘密。

#### Scenario: 重定向到私网
- **WHEN** 外部工具地址解析或重定向指向禁止目标
- **THEN** 拒绝连接并审计，无敏感请求出站

#### Scenario: 工具输出含秘密
- **WHEN** 上游返回令牌或敏感字段
- **THEN** 在进入模型、SSE 和日志前过滤，并保留安全错误摘要

### Requirement: HTTP MCP凭据的受众与当前许可编排
HTTP MCP授权边界 SHALL 将可信配置中的单一resource受众、issuer、精确scope、租户/主体、来源修订和调用/attempt/generation/dispatch绑定到固定凭据provider/ID/revision；默认缺少可信broker或持久化审计提供者时拒绝，不透传平台JWT，不以摘要/自报claims代替当前许可。

#### Scenario: 取密与范围变化
- **WHEN** 可信执行器请求一次HTTP MCP凭据
- **THEN** broker必须独立验证当前调用/主体/资源/审批及持久化一次派发，令牌签名或内省由获准提供者负责。编排核对单一受众及恰好批准的scope、原revision、令牌摘要/期限/撤销；取密后、访问审计等待后、适配器返回及完成审计后重新核对，变化拒绝，不刷新或静默换凭据

#### Scenario: 平台令牌或未配置提供者
- **WHEN** 没有broker/审计sink，或凭据是平台issuer/audience的JWT、格式损坏、过期或过宽范围
- **THEN** 固定码拒绝且不进入适配器；未验签JWT解析只用于额外拒绝，不产生认证或业务执行许可，没有使用平台Bearer、模型Key或默认本地密封作为OAuth凭据的回退路径

#### Scenario: 指定lease撤销未确认
- **WHEN** 以当前独立撤销权限请求撤销原lease
- **THEN** 先核对固定范围，broker撤销后必须回查原lease的真实撤销时间及其他不变元数据；已过期仍可另行授权撤销，已存在撤销事实不得覆盖。未知/超时/审计失败不自造成功或自动重发，也不以取消进程断言外部操作未执行

#### Scenario: 通用编排与真实接入边界
- **WHEN** 通用授权库已实现，但尚未配置首个HTTP MCP服务、可信broker/审计及独立执行器
- **THEN** 默认拒绝，无API/CLI/Agent网络入口，不宣称真实HTTP互通、签名校验或OS隔离已完成。持久化一次领取和认证IPC库不替代实际提供者/运行部署，固定可信适配器仍须复用出站与Schema/网关控制，8.7及20.5保持部分完成，运行验收继续独立待办

#### Scenario: 固定无会话HTTP调用
- **WHEN** 获准执行器显式使用stateless_json_v1客户端，且真实broker和审计通道均已配置
- **THEN** 固定服务器名称/版本、原描述及参数摘要后最多发送初始化/通知/目录/一次调用四个POST，复用固定HTTPS/DNS/IP/TLS策略，交换前后及DNS完成后复检许可；通知只接受202空正文，其他只接受200 JSON。SSE、session头、分页、未知能力/指令拒绝，不DELETE/重连/重试或从401改变受众/scope

#### Scenario: 结构化结果与本地审计
- **WHEN** 客户端取得结果或应用侧写入独立审计
- **THEN** 严格JSON树/固定输出Schema/文本镜像/脱敏与令牌遮蔽后返回不可信观察，不确认业务成功。DatabaseHttpMcpAuditSink独立事务只查原主体/租户/调用/参数及来源历史绑定后追加最小阶段元数据，不取密或读正文，不把历史绑定当当前许可；未知提交不重发。写入器只能位于broker侧，隔离执行器经认证IPC请求审计，不持有数据库工厂

### Requirement: 固定公钥JWT访问令牌验签
系统 SHALL 提供可选的封闭JWT访问令牌验证库，复用既有PyJWT与cryptography，仅使用可信部署批准的公钥和显式外部主体映射。验签不代替当前授权、consent、令牌签发/撤销或一次派发。

#### Scenario: 固定算法和可信公钥
- **WHEN** 获准上游返回JWT访问令牌并使用固定验签profile
- **THEN** 只接受RS256、at+jwt及唯一匹配kid，可信策略只允许1–8个RSA2048–4096位/指数65537公开JWK且ID/模数不重复；header仅alg/typ/kid，不接受jku/x5u/内联key/crit/私钥或未知字段，不联网发现/下载密钥或降级算法。规范base64url、有界严格JSON、重复字段/非有限数/未知claims检查先作拒绝，未验签claims不能生成许可

#### Scenario: 外部主体和精确范围
- **WHEN** 验证JWT签名、claims与原broker返回的lease元数据
- **THEN** PyJWT强制验签、issuer、单一字符串audience及显式外部sub，client_id必须匹配获准客户端；iat/exp/可选nbf严格整数、零宽限，jti非空，scope恰为批准集合且无重复/空项。issued/expires与原lease一致且不超当前验签批准/调用截止；平台JWT/过期/变更scope拒绝，不根据平台UUID、模型参数或未验签claims自动推导外部主体/客户许可

#### Scenario: 公钥轮换或主体映射变化
- **WHEN** VerifiedJwtHttpMcpCredentialBroker包装真实上游，当前可信验签策略与固定策略不一致或已过期
- **THEN** 取密前后及正常调用的当前许可复检拒绝，不重新取密、静默换key或缓存旧active作为事实。原lease当前状态读取不依赖旧公钥仍有效，独立撤销由上游当前撤销权限鉴定并回查首次真实撤销时间，不因旧验签策略过期阻止安全撤销；本地验签不自造revoked事实

#### Scenario: 尚未配置真实验签策略
- **WHEN** 只有验签库而无获准提供者、公钥、显式外部subject/client映射与当前策略解析器
- **THEN** 不提供默认key/provider或平台subject回退，不创建实际凭据/请求或启用产品链；错误只固定码且无原异常/claims/令牌。新源码进入发布清单，无新依赖/表/迁移；8.7/20.5仍部分完成，未运行的验签与安全测试不视为通过

### Requirement: HTTP MCP持久化一次领取与认证IPC
系统 SHALL 以不可改写范围的派发表及固定身份Unix socket分离应用数据库和HTTP执行器；摘要/UUID、目录ready或平台角色均不能单独授予凭据。未配置真实上游、未注册固定工具或未部署独立运行身份时不得启用产品调用。

#### Scenario: 创建固定派发许可
- **WHEN** 可信网关请求保存一次HTTP MCP派发
- **THEN** 先由独立上游核对当前资源/凭据许可，再在固定锁序及READ COMMITTED短事务中核对原租户/主体/Run/attempt/generation/有效租约/工具调用及参数摘要、当前Agent grant/固定版本/模型凭据/工具目录/来源修订及熔断。仅接受R0/R1且无业务动作的调用，一项ToolInvocation全局最多一份派发；期限最多300秒且不超原租约，不提供模型/API自报许可入口

#### Scenario: 领取提交或取密结果未知
- **WHEN** broker领取原派发，或在提交/取密/绑定/回传过程中取消、超时或丢响应
- **THEN** authorized到claimed必须独立提交完成才可调用上游claim；claim nonce/时间一旦存在不可覆盖，只有原nonce可一次绑定非秘密token状态/lease，明文不入库。未知保留claimed或bound，不清除领取、不重取、不返回缓存令牌或创建第二份许可；提供者侧未知lease须按原dispatch独立对账，不能从本地失败推断已撤销

#### Scenario: 固定身份和封闭命令
- **WHEN** 10004 UID执行器连接/run/supportops-mcp-broker/control.sock
- **THEN** 双向SO_PEERCRED核对broker10003和执行器10004，目录10003:10004/0770、socket10003:10004/0660须匹配，缺少支持或路径异常拒绝。每连接一次命令，nonce/operation/dispatch/摘要与响应完全绑定，最多32KiB严格JSON；只允许assert/claim/current/revoke/audit，不发送任意URL、命令、完整策略或凭据路径。broker从已保存范围解析原许可且最多4项并行，无队列/自动重连/重试

#### Scenario: 取密和审计通过认证通道
- **WHEN** 获准执行器使用IPC调用适配器
- **THEN** broker在事务外调用真实上游，核对受众/scope/平台JWT排除及令牌摘要，回传前再查当前许可和原lease。SecretStr只在认证socket发送时显式展开，不写stdout/文件/审计；执行器调用复用原HTTP协议/出站/输出边界。审计核对原dispatch/lease后在broker侧追加，失败拒绝确认，撤销只保存上游确认的首次真实撤销时间

#### Scenario: 数据结构及部署边界
- **WHEN** 应用0053或检查当前实现状态
- **THEN** 第69表http_mcp_dispatches保存固定非秘密引用/摘要/状态/一次领取和token元数据，ORM与PG ALWAYS保护固定范围、单向状态及历史，有记录拒绝降级；保留影响预览仅计关联，不赋擦除许可。新增模块进入后续源码清单，不回填旧发布历史；独立HTTP入口/模板不代表实际提供者、产品工具注册或OS网络隔离已配置/验收，不把未执行迁移或实现写成实测通过

### Requirement: 应用到独立执行器的认证派发桥接
系统 SHALL 提供显式应用侧桥接函数，复用已预留ToolInvocation、持久化派发、认证执行器通道和回执后的当前许可复检；不以模板或UUID自动注册远程工具或授予调用许可。

#### Scenario: 发送已批准派发
- **WHEN** 当前10001应用身份使用dispatch_authorized_http_mcp_tool
- **THEN** 先冻结封闭请求，复用原独立资源许可及平台当前门禁保存固定派发，只有尚未领取authorized才可发送；通过固定dispatch.sock双向SO_PEERCRED核对10001/10004，目录10004:10001/0770及端点0660匹配，一次连接/写入，无自动重连/重发。数据只有调用/描述/参数与摘要，不含令牌、数据库或任意命令

#### Scenario: 单次执行器处理
- **WHEN** 10004执行器serve-once接到首个认证10001连接
- **THEN** 立即停止监听，坏帧也不接受第二次调用；20秒处理/2秒输出并受23秒服务等待和25秒进程退出保护。复用原固定策略、取密/当前授权/HTTP及输出过滤，回执保持不可信/无业务确认；退出仅清理自身同inode socket，不删除未知旧端点或数据库事实

#### Scenario: 应用复核观察
- **WHEN** 独立执行器返回observed
- **THEN** 应用匹配nonce/dispatch/request/source/descriptor及本次时间区间，复检固定输出Schema/64KiB预算、当前数据库许可和原已绑定未撤销lease，以及同dispatch/request/lease的持久化call_confirmed审计；再次核对上游当前lease/授权才返回不可信观察。缺依赖/审计/当前许可或任何未知均拒绝，不当业务verified或写入模型产品答案

#### Scenario: 接线与部署边界
- **WHEN** 桥接库及独立socket卷模板已存在
- **THEN** 不代表Worker/Agent已注册远程工具或拥有Docker权限，真实provider/公钥/映射、监督启动、精确出口仍须获批接入。应用不持业务令牌，执行器仍不持DB；缺配置拒绝，未知只查原派发，不重领/重发/创建第二份许可，未运行测试/容器/网络不能宣称通过

### Requirement: 本人HTTP MCP原派发历史查询
系统 SHALL 提供本人Run下的原派发分页和单项只读查询，不把本地生命周期当作远端业务结果或再次执行许可。

#### Scenario: 本人范围与敏感字段排除
- **WHEN** 当前已认证用户查询GET /v1/runs/{run_id}/http-mcp-dispatches或其/{dispatch_id}
- **THEN** 同时限定租户、本人会话、原actor与Run，错Run/他人/跨租户统一404 NOT_FOUND，管理员不绕过；原Agent grant/执行租约失效不禁止本人历史元数据访问。SQL只选固定引用/状态/时间，不读取request_payload/token_state/令牌摘要/lease/claim nonce，不回传URL/scope/外部主体或正文；成功读取no-store

#### Scenario: 标准分页与原状态保留
- **WHEN** 查询分页或过期派发
- **THEN** 使用公共page/page_size默认1/20最大100，权限过滤先于SQL count/offset/limit，created_at/id倒序，越界空页；返回observed_at及独立is_expired，不将过期变成revoked，不延长原期限。claimed不证明取密完成，bound不证明远端执行或业务成功，business_status_confirmed/execution_permit恒false，空记录不证明未执行；GET不访问broker或目标、不取密/重发/撤销

#### Scenario: 聊天手动查询与迟到响应
- **WHEN** 用户从结构化结果或最新失败轮次打开抽屉、刷新、翻页或查询单项
- **THEN** 每次使用当前登录会话和原Run查询，按token/Run/序号丢弃关闭/卸载/目标变化后的迟到结果；列表先显示Loading并等待delayRequest后请求，错误及未知契约隐藏记录，手动刷新可恢复。只读界面不提供自动重发、撤销或业务成功确认

### Requirement: 独立HTTP执行器的单次进程边界
系统 SHALL 提供默认不启动的固定身份HTTP执行入口和独立运行模板；执行器不读取Settings/数据库/模型凭据，只通过认证IPC获取短期业务令牌。模板和进程前置检查不得被表述为真实服务批准或完整OS网络隔离验收。

#### Scenario: 执行身份或资源前提不符
- **WHEN** 单次入口处于非Linux、非10004 UID/GID、含非允许环境、未启用-I/-B、继承额外描述符，或权限/资源前提不符
- **THEN** 读取策略/输入和加载可选SDK前拒绝。检查NoNewPrivs、有效/许可/ambient能力为零、seccomp过滤模式及有限CPU/地址空间/句柄/进程/core限制；stdin/stdout仅管道或socket，不输出原异常。检查仅覆盖所列前提，不证明挂载、过滤器内容或外部出口规则已审计

#### Scenario: 可信部署策略与输入不符
- **WHEN** 执行器读取固定/etc/supportops-mcp/http-policy.json或接收单次请求
- **THEN** 策略只接受root拥有/0444普通单链接文件及root0755目录，拒绝符号链接、读取中变动/超32KiB/过期/未知字段；固定原授权策略、平台标识和最多32个精确IP，私网仅批准RFC1918/ULA，其他非法地址拒绝。请求不携令牌/命令/凭据路径/任意出站规则，必须匹配部署策略SHA、原调用SHA及描述/参数SHA，之后仍由broker独立核对数据库当前许可

#### Scenario: 单帧调用与背压
- **WHEN** 可信调用方经stdin发送一条换行结束的紧凑JSON帧
- **THEN** 最多192KiB、3秒输入等待，重复键/非有限数/超过16层或4096节点拒绝，首帧结束即处理而不等待EOF；不消费第二次调用。同一进程最多调用一次原IPC适配器，stdout最多128KiB/2秒非阻塞输出，仅有nonce/dispatch/摘要及脱敏不可信观察或固定失败码，秘密和原始SDK错误不输出，观察不能授予执行许可或确认业务成功

#### Scenario: 中断、清理或输出丢失
- **WHEN** SIGTERM/SIGINT、20秒工作等待耗尽、25秒进程退出保护触发，或stdout发生背压/断开
- **THEN** 不重发HTTP、重领令牌或清除原dispatch，退出/取消不能证明远端未执行。已知关联保留原nonce/dispatch/摘要，输出不明或强制退出只由调用方按原派发只读核对，不自动创建新许可；计时/CPU限制不承诺内核不可中断I/O的硬截止

#### Scenario: 独立运行模板与启用范围
- **WHEN** 维护者审阅独立HTTP执行Dockerfile/Compose
- **THEN** 模板不合并默认应用编排，须显式profile及获批镜像、root只读策略、专用broker socket卷与已配置精确出口规则的独立网络，空值拒绝；不继承应用env_file/业务卷/公开端口，使用只读根、cap-drop/no-new-privileges、资源限制、无日志存储/TTY/自动重启。不自动创建宽泛网络、策略或凭据，API/Worker不取得Docker权限；真实provider/OS出口和产品注册未完成时不启用调用，运行验收继续暂缓

### Requirement: 固定隔离Mock的MCP协议适配
系统 SHALL 提供仅用于随包隔离Mock的官方SDK客户端/Server实现，不允许通过该入口调用真实业务工具。依赖与协议版本必须固定，普通API/Worker不得因导入核心包自动启动MCP或要求安装该可选运行时。

#### Scenario: 固定SDK与握手
- **WHEN** 显式调用隔离MCP入口
- **THEN** 要求mcp-isolated可选组的官方mcp==1.30.0，initialize协商必须为2025-11-25，服务名/版本及能力必须匹配固定Mock。未知版本/能力拒绝，不自动降级或切换2.x协议；现有依赖版本保持不变

#### Scenario: 隔离运行前提
- **WHEN** 入口处于普通应用进程、非Linux/10002 UID/GID、含非允许环境、存在非lo网卡或进程权限不符
- **THEN** 在加载SDK/启动Mock前固定码拒绝。独立容器配置使用network none、只读根、无应用卷/端口/env_file、清空环境、cap-drop与no-new-privileges和有限资源；前置检查不替代实际OS隔离验收

#### Scenario: 固定目录和只读调用
- **WHEN** 执行catalog或read-status
- **THEN** 只启动随包固定stdio Mock，不接受URL、命令、令牌或任意工具参数；单页目录只有get_isolated_service_status、封闭空对象输入和常量模拟状态输出，描述/Schema必须精确匹配。catalog不调用工具，read-status最多调用一次，结构化结果与文本镜像一致；额外通知含listChanged使本次观察失效，不重试或沿用旧目录

#### Scenario: 有界结果与未知完成
- **WHEN** 握手/目录/只读结果不符、等待耗尽、中断或输出失败
- **THEN** 只输出固定错误码，结果不确认且不自动重做；正文只返回规范目录和模拟观察，业务执行许可恒false。单请求等待3秒、协议工作及清理等待预算10秒，SDK/内核清理不承诺硬截止。固定Mock来源不等于对任意第三方stdio的解析前帧限制或沙箱验收

#### Scenario: 产品与验收边界
- **WHEN** 隔离入口返回协议观察
- **THEN** 不写来源治理库、不自动批准来源、不注册Agent工具、不获得ToolInvocation/认证IPC/业务执行许可，不将Mock状态写成产品连接健康；8.5实现与运行验收分开，未运行时保持该混合任务未完成

### Requirement: 来源管理界面与当前修订分页
系统 SHALL 向同租户当前管理员提供来源管理界面与标准分页读取；目录状态和真实协议连接状态必须独立展示，目录ready不得作为连接健康或执行许可。

#### Scenario: 分页读取来源
- **WHEN** 管理员查询GET /v1/admin/tool-discovery/sources?page=1&page_size=20
- **THEN** 在SQL中先限定租户并按来源选择最新修订，再count/offset/limit，以source_key/id稳定排序，返回items/total/page/page_size/pages；越界页为空，不读取全部历史后内存分页。page最小1，page_size默认20最大100，复用公共分页依赖；响应no-store

#### Scenario: 管理批准与启停
- **WHEN** 管理员在/agent-management/tool-sources保存来源指纹、明确时区期限、最多50项描述摘要allowlist或启停配置
- **THEN** 使用服务器完整policy与expected_revision显式确认保存，不从当前ready工具反推批准集合；新批准使旧目录失效。页面区分加载/空态/错误，未知契约禁止写入，异步保存阻止关闭和重复提交

#### Scenario: 查询固定定义与未知提交
- **WHEN** 管理员查看定义、切换目标或写入响应丢失
- **THEN** 固定描述绑定当前来源/record_id/catalog_digest，目标变化或会话变化丢弃迟到结果，纯文本展示定义。写入未知关闭旧确认、暂停写入并只GET原来源核对，查询结果不宣称原写入成功；恢复后的新写入须重新确认

#### Scenario: 真实业务连接尚未接入
- **WHEN** 当前提供者只有固定隔离Mock，或隔离探测未配置
- **THEN** connection_status固定not_connected、execution_enabled固定false；独立Mock观察不得冒称真实业务健康，未配置时拒绝排队，不提供手工上传通过或直接设healthy的入口，不把Registry放入执行链。真实业务连接及运行验收独立待办

### Requirement: 隔离Mock连接控制
系统 SHALL 提供默认关闭的持久化隔离探测、人工熔断、显式恢复与取消接口及管理界面。该提供者仅允许local/dev/test环境中的固定无参数只读Mock，业务执行许可始终为false。

#### Scenario: 当前来源与管理员许可
- **WHEN** 管理员显式确认提交探测或人工熔断
- **THEN** 同租户锁下同时核对expected_source_record_id与连接expected_revision，来源/策略/当前会话/许可代次及固定实现不符拒绝；每来源最多一项queued/running，截止最多60秒。任务只保存固定引用，不存JWT或业务凭据，不接受外部自报通过

#### Scenario: 独立身份派发与迟到结果
- **WHEN** Worker领取一次探测任务
- **THEN** 提交owner/generation/最多20秒租约后在事务外走固定Unix socket，SO_PEERCRED双向核对10001/10002身份，仅传任务UUID与read_status。返回后重新核对nonce、固定来源/描述/Schema/模拟观察、当前管理员会话与租约；未知或过期不通过，不自动重发，取消和人工熔断围栏旧结果

#### Scenario: 熔断恢复与观察过期
- **WHEN** 连续3次探测失败或管理员人工熔断，之后请求恢复
- **THEN** open阻止固定描述解析，至少30秒冷却后显式半开探测，仅新结果通过才closed。健康观察最多60秒且不超过批准期限，过期stale、来源变化unknown，旧范围半开投影open；配置关闭收敛原任务，不以旧通过恢复

#### Scenario: 追加历史与页面未知结果
- **WHEN** 连接事件写入或页面操作响应未知
- **THEN** 0052与ORM保护连续追加事件及一次任务状态，拒绝历史改删/截断和有历史降级。页面按当前来源版本与token隔离响应，忙时禁关闭/重复提交，未知仅GET原状态/任务，显式核对后新操作须重新确认；不轮询、不重发、不将Mock通过当业务执行许可

### Requirement: 连接器健康与失效
平台 SHALL 记录连接状态、能力与版本，提供测试/停用/熔断/恢复路径和有限重试；写重试由动作对账策略决定。

#### Scenario: 停用外部连接
- **WHEN** 管理员撤销连接或其凭据
- **THEN** 排队及后续工具调用拒绝，既有审计保持可追踪
