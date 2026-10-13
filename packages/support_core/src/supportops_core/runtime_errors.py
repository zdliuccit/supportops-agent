"""稳定运行错误的可执行中文说明，避免返回供应商异常或原始数据。"""

RUNTIME_ERROR_MESSAGES = {
    "DIAGNOSTIC_RESULT_INVALID": "诊断事实与调用来源不一致，报告未获准保存，请联系管理员排查。",
    "DIAGNOSTIC_CONTEXT_INVALID": "诊断缺少可信输入边界，请联系管理员确认运行与迁移状态。",
    "MODEL_RESOURCE_UNAVAILABLE": "固定模型端点已停用或不可用，请联系管理员确认启用状态；不会自动换用其他模型。",
    "RUNTIME_AUTHORIZATION_DENIED": "当前用户、租户或 Agent 授权已失效，请联系管理员恢复授权后重新发送。",
    "TOOL_ACCESS_DENIED": "当前角色没有该工具权限，请联系管理员检查工具与 Agent 授权。",
    "TOOL_REVOKED": "该工具已被紧急撤销，请联系管理员确认撤销状态。",
    "TOOL_DISABLED": "该工具已停用，请检查工具目录后重新发送。",
    "TOOL_DISABLED_OR_REVOKED": "该工具已停用或紧急撤销，请联系管理员检查工具目录。",
    "TOOL_ACTION_WORKFLOW_UNAVAILABLE": "写操作尚未接通动作幂等与结果复核，当前不能执行。",
    "TOOL_CALL_QUOTA_EXCEEDED": "本轮工具总量或单工具额度已耗尽，请缩小问题范围。",
    "TENANT_TOOL_DAILY_QUOTA_EXCEEDED": "租户 UTC 每日工具额度已耗尽，请等待窗口重置或联系管理员。",
    "TOOL_PARALLEL_LIMIT_EXCEEDED": "工具并行许可已用尽，请降低并行数量。",
    "MOCK_DIAGNOSTICS_DISABLED": "隔离模拟诊断当前未启用；需要真实查询时请先接通获批业务连接器。",
    "TOOL_POLICY_CHANGED": "执行期间工具策略已变化，本次结果未获准使用。",
    "TOOL_IMPLEMENTATION_VERSION_UNAVAILABLE": "固定工具实现版本不可用，请明确选择可用实现并重新发布 Agent，历史版本不会静默升级。",
    "TOOL_APPROVAL_REQUIRED": "该操作需要有效人工审批，目前未取得执行凭证。",
    "TOOL_ACTION_WORKFLOW_REQUIRED": "写操作尚未接通动作幂等与结果复核流程，不能执行。",
    "TOOL_CALL_LIMIT_EXCEEDED": "本轮工具调用额度已耗尽，请缩小问题范围后重新发送。",
    "TOOL_OUTPUT_TOO_LARGE": "证据结果超过输出预算，请减少查询数量或缩小范围。",
    "KNOWLEDGE_EVIDENCE_INVALID": "回答引用不存在、已失效或当前无权读取，答案未保存为有效结果。",
    "AGENT_CONTEXT_BUDGET_EXCEEDED": "当前问题、系统配置及证据超过模型上下文预算，请缩小内容或调整配置。",
    "AGENT_MODEL_CALL_BUDGET_EXCEEDED": "本轮累计模型调用额度已耗尽，重试和恢复不会重置额度。",
    "AGENT_COST_BUDGET_EXCEEDED": "本轮累计费用及预留已达到预算，请减少范围或调整预算。",
    "AGENT_RUN_TIME_BUDGET_EXCEEDED": "累计执行时长预算已耗尽，请重新发送更聚焦的问题。",
    "MODEL_PRICING_UNKNOWN": "费用硬预算需要可信 USD 定价，请先补齐模型定价配置。",
    "TENANT_MODEL_CALL_QUOTA_EXCEEDED": "租户 UTC 每日模型调用额度已耗尽，请等待额度窗口重置或联系管理员。",
    "TENANT_MODEL_COST_QUOTA_EXCEEDED": "租户 UTC 每日模型费用额度已耗尽或存在未知费用，请联系管理员。",
    "MODEL_DEPENDENCY_TEMPORARILY_UNAVAILABLE": "模型依赖暂时不可用，有限恢复未能完成，请稍后重试。",
    "AGENT_RUN_TIMEOUT": "执行超时，请缩小问题范围后重试。",
}


def runtime_error_message(code: str | None) -> str | None:
    if code is None:
        return None
    return RUNTIME_ERROR_MESSAGES.get(code, f"执行未完成，请提供错误码 {code} 给管理员排查。")
