import { useState } from "react";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { ReleaseRolloutCreate } from "@/types";

const fields = [
  { key: "initial_candidate_bps", label: "初始候选万分比（100为1%）", min: 1, max: 1000, integer: true },
  { key: "observation_seconds", label: "每轮观察时长（秒）", min: 300, max: 86400, integer: true },
  { key: "minimum_runs_per_version", label: "每版本最少终态样本数", min: 3, max: 5000, integer: true },
  { key: "max_failure_rate", label: "最大失败率（0至0.1）", min: 0, max: 0.1, integer: false },
  { key: "max_average_cost_usd", label: "平均费用上限（美元）", min: 0, max: 1000000, integer: false, positive: true },
  { key: "max_p95_api_first_text_ms", label: "API首正文P95上限（毫秒）", min: 1, max: 3000, integer: true },
  { key: "max_p95_complete_ms", label: "完整响应P95上限（毫秒）", min: 1, max: 10000, integer: true },
  { key: "max_cost_increase_ratio", label: "候选费用最高倍数（相对基线）", min: 1, max: 2, integer: false },
  { key: "max_latency_increase_ratio", label: "候选完整耗时最高倍数（相对基线）", min: 1, max: 2, integer: false },
] as const;
type FieldKey = typeof fields[number]["key"];
type FieldErrors = Partial<Record<FieldKey | "subjects", string>>;
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function numericError(field: typeof fields[number], text: string): string | undefined {
  const value = Number(text);
  if (!text.trim() || !Number.isFinite(value)) return `请输入${field.label}`;
  if (value < field.min || value > field.max || ("positive" in field && value <= 0) || (field.integer && !Number.isInteger(value)))
    return `范围${"positive" in field ? "大于0且不超过" : `${field.min}至`}${field.max}${field.integer ? "，须为整数" : ""}`;
}
function subjectsFrom(text: string): string[] { return text.split(/[\s,，]+/).filter(Boolean).map((item) => item.toLowerCase()); }
function subjectError(text: string): string | undefined {
  const values = subjectsFrom(text);
  if (values.length > 200 || new Set(values).size !== values.length || values.some((item) => !uuidPattern.test(item)))
    return "最多200个不重复的用户UUID；以逗号或换行分隔";
}

/** 仅保存固定范围草稿；填写门槛不批准策略或评测。 */
export function ReleaseRolloutCreateDialog({ candidateVersionId, busy, onClose, onCreate }: {
  candidateVersionId: string; busy: boolean; onClose: () => void;
  onCreate: (payload: ReleaseRolloutCreate) => Promise<void>;
}) {
  const [values, setValues] = useState<Partial<Record<FieldKey, string>>>({});
  const [subjects, setSubjects] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  async function submit() {
    const next: FieldErrors = {};
    for (const field of fields) { const error = numericError(field, values[field.key] ?? ""); if (error) next[field.key] = error; }
    const invalidSubjects = subjectError(subjects); if (invalidSubjects) next.subjects = invalidSubjects;
    setErrors(next); if (Object.keys(next).length || busy) return;
    await onCreate({ candidate_version_id: candidateVersionId, initial_candidate_bps: Number(values.initial_candidate_bps),
      subject_ids: subjectsFrom(subjects), policy: {
        schema_version: "release_rollout_policy_v1", observation_seconds: Number(values.observation_seconds),
        minimum_runs_per_version: Number(values.minimum_runs_per_version), max_failure_rate: Number(values.max_failure_rate),
        max_average_cost_usd: Number(values.max_average_cost_usd), max_p95_api_first_text_ms: Number(values.max_p95_api_first_text_ms),
        max_p95_complete_ms: Number(values.max_p95_complete_ms), max_cost_increase_ratio: Number(values.max_cost_increase_ratio),
        max_latency_increase_ratio: Number(values.max_latency_increase_ratio), first_text_source: "api_database_visibility_v1",
        max_metadata_rows_per_version: 5000, max_candidate_bps: 9000, max_step_bps: 1000,
        automatic_expansion: false, authorization_denial_tolerance: 0,
      } });
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent className="max-w-2xl" onEscapeKeyDown={(event) => { if (busy) event.preventDefault(); }}>
      <form noValidate className="flex min-h-0 flex-col overflow-hidden" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <DialogHeader><DialogTitle>保存灰度草稿</DialogTitle><DialogDescription>固定当前活动基线、候选和主体范围；策略摘要须另经服务器配置批准，两版本须有完整适用实测，之后才能显式启动。</DialogDescription></DialogHeader>
        <DialogBody className="space-y-4"><div className="grid gap-4 sm:grid-cols-2">{fields.map((field) => {
          const id = `rollout-${field.key}`;
          return <FormField key={field.key} htmlFor={id} label={field.label} required error={errors[field.key]}>
            <Input id={id} type="number" step={field.integer ? 1 : "any"} value={values[field.key] ?? ""} disabled={busy}
              placeholder={`请输入${field.label}`} aria-invalid={Boolean(errors[field.key])} aria-describedby={errors[field.key] ? `${id}-error` : undefined}
              onChange={(event) => { const text = event.target.value; setValues((old) => ({ ...old, [field.key]: text })); setErrors((old) => ({ ...old, [field.key]: numericError(field, text) })); }} />
          </FormField>;
        })}</div>
          <FormField htmlFor="rollout-subjects" label="限定用户UUID" error={errors.subjects} hint="留空表示当前租户获准使用该Agent的主体；不会增加Agent或业务权限。">
            <Textarea id="rollout-subjects" value={subjects} disabled={busy} placeholder="请输入限定用户UUID，以逗号或换行分隔"
              aria-invalid={Boolean(errors.subjects)} aria-describedby={errors.subjects ? "rollout-subjects-error" : undefined}
              onChange={(event) => { setSubjects(event.target.value); setErrors((old) => ({ ...old, subjects: subjectError(event.target.value) })); }} />
          </FormField>
          <p className="text-xs text-muted-foreground">初始最多10%，每次最多增加10个百分点，灰度最高90%。API首正文为服务器可见上界，不能证明客户端SLO或业务解决率。样本或完整计量不足会阻止扩量。</p>
        </DialogBody>
        <DialogFooter><Button variant="outline" onClick={onClose} disabled={busy}>取消</Button><Button type="submit" disabled={busy}>{busy ? "正在保存…" : "仅保存草稿"}</Button></DialogFooter>
      </form>
    </DialogContent>
  </Dialog>;
}
