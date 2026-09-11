import type { ModelEndpointModel, ModelTestRun } from "@/types";

/** 当前模型配置表单支持的 API 协议。 */
export type Protocol = "responses" | "chat_completions";

/** 模型配置编辑弹窗中的单行模型草稿。 */
export interface EditableModel {
  /** 已保存模型的稳定 UUID；新模型为空。 */
  id?: string;
  /** 供应商实际识别的模型 ID。 */
  upstream_model_id: string;
  /** 以 JSON 文本编辑、提交时转换为对象的模型扩展参数。 */
  extension_options: string;
  /** 当前模型最近一次测试状态。 */
  test_status?: ModelEndpointModel["test_status"];
}

/** 测试状态的统一展示信息。 */
export const testStatusMeta: Record<
  ModelEndpointModel["test_status"],
  { label: string; className: string }
> = {
  untested: { label: "未测试", className: "text-[#919eab]" },
  running: { label: "测试中", className: "text-violet-600" },
  passed: { label: "测试通过", className: "text-emerald-600" },
  failed: { label: "测试失败", className: "text-red-600" },
  stale: { label: "测试已过期", className: "text-amber-600" },
  cancelled: { label: "已取消", className: "text-[#919eab]" },
};

/** 将服务端模型转换为编辑表单行。 */
export function toEditableModel(model: ModelEndpointModel): EditableModel {
  return {
    id: model.id,
    upstream_model_id: model.upstream_model_id,
    extension_options: JSON.stringify(model.extension_options ?? {}, null, 2),
    test_status: model.test_status,
  };
}

/** 解析扩展参数并确保顶层值是 JSON 对象。 */
export function parseExtensionOptions(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value.trim() || "{}");
  if (parsed === null || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("扩展对象必须是 JSON 对象");
  }
  return parsed as Record<string, unknown>;
}

/** 返回扩展参数的中文校验错误；合法时返回空字符串。 */
export function extensionOptionsError(value: string): string {
  try {
    parseExtensionOptions(value);
    return "";
  } catch (cause) {
    return cause instanceof SyntaxError ? "请输入有效的 JSON 对象" : "扩展对象必须是 JSON 对象";
  }
}

/** 将编辑表单行转换为服务端保存结构。 */
export function modelPayload(model: EditableModel) {
  return {
    id: model.id,
    upstream_model_id: model.upstream_model_id.trim(),
    extension_options: parseExtensionOptions(model.extension_options),
  };
}

/** 统一格式化测试耗时。 */
export function elapsed(value: number | null): string {
  if (value === null) return "—";
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}s`;
}

/** 测试弹窗使用的终态集合。 */
export const terminalTestStatuses = new Set<ModelTestRun["status"]>([
  "passed",
  "failed",
  "cancelled",
]);
