# 知识生命周期

当前已实现租户级知识来源、文档、不可变版本、ACL、审核/发布/失效和有效快照读取。Mock 导入入口用于 P0 基线数据，并阻止 Mock 来源进入 production。

当前有效快照尚未接入 Agent 的向量检索、Rerank、引用生成和证据不足升级链路。

代码入口：`apps/api/src/supportops_api/routes/admin_knowledge.py`、`packages/support_core/src/supportops_core/knowledge_services.py`。
