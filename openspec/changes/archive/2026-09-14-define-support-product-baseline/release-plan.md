# P0/P1/P2 范围与依赖计划（候选基线）

## 阶段边界

| 阶段 | 进入条件 | 范围 | 明确排除 | 退出条件 |
| --- | --- | --- | --- | --- |
| P0 可用 MVP | 单产品域、单 Agent、Mock 业务系统和固定评测集可用 | 多轮会话、引用式知识问答、4–6 个受控只读工具、429 诊断、自动建单、SSE、Run 持久化、基础 Trace | 生产写操作、多 Agent、自由 SQL、无审批 R4、跨域自动知识更新 | 四条主路径端到端通过；安全不变量和基础降级验证通过 |
| P1 企业试运行 | P0 指标稳定，目标企业提供身份、只读业务系统和 staging | 企业认证、RBAC/ABAC、租户隔离、知识版本、完整工单、人工接管、R4 审批、脱敏审计、成本监控 | 无组织确认的生产写入、多渠道、多 Agent | 通过 P1 安全门禁；一个 R4 操作完成完整审批、幂等、复核闭环 |
| P2 规模化生产 | P1 真实质量、成本、容量和组织瓶颈证据 | 多渠道、模型路由、在线评测、自动知识更新、高可用、领域子 Agent | 仅凭路线图承诺而无瓶颈证据的能力 | 由独立 P2 change 定义并通过新的容量/可靠性门禁 |

## 后续 change 依赖

1. `establish-platform-foundation`
2. `add-conversation-run-runtime`
3. `add-knowledge-lifecycle`
4. `add-grounded-knowledge-retrieval`
5. `establish-tool-gateway`
6. `add-api-error-diagnostic-tools`
7. `orchestrate-support-agent-workflow`
8. `add-ticket-management`
9. `add-human-handoff`
10. `add-approval-action-workflow`
11. `establish-security-observability`
12. `establish-evaluation-release-gates`

前两项为主链路；知识生命周期与工具网关在运行时稳定后可并行设计，但进入 Agent 工作流前必须同时具备；工单、人工接管和审批依次建立闭环。安全、审计和评测要求从运行时 change 开始作为横切门禁，不得推迟到 P2。

## 里程碑与重新估算

- M0（基线批准）：场景、知识、角色/风险、范围和评测口径完成业务确认。
- M1（P0 设计冻结）：运行时、知识检索、工具网关和 429 诊断 change 均有可执行规格。
- M2（P0 验收）：四条主路径和功能/安全/质量/运维门禁通过。
- M3（P1 试运行准备）：企业身份、真实只读系统、staging、审计与工单契约就绪。
- M4（P1 试运行复盘）：基于真实质量、成本、容量和组织瓶颈决定是否启动 P2。

每个里程碑结束后重新估算后续任务；若外部系统、数据或安全评审未就绪，暂停进入下一阶段，不以日期替代退出条件。
