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

#### Scenario: 真实连接尚未接入
- **WHEN** 当前只有来源治理元数据而没有协议执行器
- **THEN** connection_status固定not_connected、execution_enabled固定false，界面显示连接未接入；不提供伪探测通过或伪熔断恢复，不把Registry放入执行链。8.10保持部分完成，真实连接及验收独立待办

### Requirement: 连接器健康与失效
平台 SHALL 记录连接状态、能力与版本，提供测试/停用/熔断/恢复路径和有限重试；写重试由动作对账策略决定。

#### Scenario: 停用外部连接
- **WHEN** 管理员撤销连接或其凭据
- **THEN** 排队及后续工具调用拒绝，既有审计保持可追踪
