# 可观测性与健康

运行时会记录 AgentRun 摘要、模型/工具 Observation、Token、成本、阶段耗时、TTFT、finish reason、错误事件和 provider request ID。Dashboard API 提供 summary、timeseries、runs、errors、trace 及系统级聚合。

健康状态由运行窗口、活动 Run、Agent 版本事实和 Worker lease 派生；健康快照不替代 Run 原始状态。租约过期或执行服务不可用时，系统显示 `unknown`/不可用原因，而不是把缺失数据当成健康。
