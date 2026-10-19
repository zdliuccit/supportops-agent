import { useState, type FormEvent } from "react";

import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import type { ToolDiscoveryPolicy, ToolDiscoveryView } from "@/types";

type Fields = { sourceKey: string; fingerprint: string; validUntil: string; tools: string; enabled: boolean };

function validate(value: Fields) {
  const errors: Partial<Record<keyof Fields, string>> = {};
  if (!/^[a-z][a-z0-9_-]{0,63}$/.test(value.sourceKey)) errors.sourceKey = "使用小写字母开头的1–64位字母、数字、下划线或短横线。";
  if (!/^[0-9a-f]{64}$/.test(value.fingerprint)) errors.fingerprint = "请输入64位小写十六进制来源指纹。";
  if (!/(Z|[+-]\d{2}:\d{2})$/.test(value.validUntil) || !Number.isFinite(Date.parse(value.validUntil))) errors.validUntil = "请输入带时区的日期时间，例如2026-11-01T18:00:00+08:00。";
  else if (value.enabled && Date.parse(value.validUntil) <= Date.now()) errors.validUntil = "启用来源的批准期限必须晚于当前时间。";
  const allowed: ToolDiscoveryPolicy["allowed_tools"] = [];
  const lines = value.tools.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (const line of lines) {
    const match = /^([A-Za-z][A-Za-z0-9_.-]{0,63})\s+([0-9a-f]{64})$/.exec(line);
    if (!match || allowed.some((tool) => tool.name === match[1])) { errors.tools = "每行填写工具名和64位描述摘要，以空格分隔；名称不能重复。"; break; }
    allowed.push({ name: match[1], descriptor_digest: match[2] });
  }
  if (lines.length > 50) errors.tools = "最多批准50个工具。";
  if (value.enabled && lines.length === 0) errors.tools = "启用前至少批准一个工具。";
  return { errors, allowed };
}

export function SourcePolicyDialog({ source, busy, onClose, onSave }: {
  source: ToolDiscoveryView | null; busy: boolean; onClose: () => void;
  onSave: (sourceKey: string, expectedRevision: number, policy: ToolDiscoveryPolicy) => Promise<void>;
}) {
  const [fields, setFields] = useState<Fields>({ sourceKey: source?.source_key ?? "",
    fingerprint: source?.policy.source_fingerprint ?? "", validUntil: source?.policy.valid_until ?? "",
    tools: source?.policy.allowed_tools.map((tool) => `${tool.name} ${tool.descriptor_digest}`).join("\n") ?? "",
    enabled: source?.policy.enabled ?? false });
  const [touched, setTouched] = useState<Partial<Record<keyof Fields, boolean>>>({});
  const [submitted, setSubmitted] = useState(false);
  const { errors } = validate(fields);
  function change<K extends keyof Fields>(key: K, value: Fields[K]) {
    setFields((current) => ({ ...current, [key]: value })); setTouched((current) => ({ ...current, [key]: true }));
  }
  function error(key: keyof Fields) { return touched[key] || submitted ? errors[key] : undefined; }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (busy) return;
    setSubmitted(true);
    const checked = validate(fields);
    if (Object.keys(checked.errors).length) return;
    await onSave(fields.sourceKey, source?.revision ?? 0, { schema_version: "tool_source_policy_v1",
      source_fingerprint: fields.fingerprint, enabled: fields.enabled,
      valid_until: fields.validUntil, allowed_tools: checked.allowed });
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent>
      <form className="flex min-h-0 flex-1 flex-col" noValidate onSubmit={(event) => void submit(event)}>
        <DialogHeader><DialogTitle>{source ? "编辑来源批准" : "新增工具来源"}</DialogTitle>
          <DialogDescription>保存新批准将使原固定目录失效。启用仅允许目录验证，实际连接尚未接入。</DialogDescription></DialogHeader>
        <DialogBody className="space-y-5">
          <FormField label="来源别名" htmlFor="source-key" required error={error("sourceKey")}>
            <Input id="source-key" value={fields.sourceKey} disabled={busy || source !== null} placeholder="请输入来源别名" maxLength={64}
              onChange={(event) => change("sourceKey", event.target.value)} aria-invalid={Boolean(error("sourceKey"))} aria-describedby={error("sourceKey") ? "source-key-error" : undefined} />
          </FormField>
          <FormField label="来源指纹" htmlFor="source-fingerprint" required error={error("fingerprint")} hint="使用受控连接配置与来源身份的SHA-256指纹，不填写地址或凭据。">
            <Input id="source-fingerprint" value={fields.fingerprint} disabled={busy} placeholder="请输入来源指纹" maxLength={64}
              onChange={(event) => change("fingerprint", event.target.value)} aria-invalid={Boolean(error("fingerprint"))} aria-describedby={error("fingerprint") ? "source-fingerprint-error" : undefined} />
          </FormField>
          <FormField label="批准截止时间" htmlFor="source-expiry" required error={error("validUntil")} hint="时间必须包含Z或明确时区偏移；服务器会再次检查期限。">
            <Input id="source-expiry" value={fields.validUntil} disabled={busy} placeholder="请输入批准截止时间" maxLength={64}
              onChange={(event) => change("validUntil", event.target.value)} aria-invalid={Boolean(error("validUntil"))} aria-describedby={error("validUntil") ? "source-expiry-error" : undefined} />
          </FormField>
          <FormField label="批准工具与描述摘要" htmlFor="source-tools" error={error("tools")} hint="每行填写：远端工具名 SHA-256全描述摘要。最多50项，不自动批准新增或变更工具。">
            <Textarea id="source-tools" className="min-h-36 font-mono" value={fields.tools} disabled={busy} maxLength={6600} placeholder="请输入批准工具与描述摘要"
              onChange={(event) => change("tools", event.target.value)} aria-invalid={Boolean(error("tools"))} aria-describedby={error("tools") ? "source-tools-error" : undefined} />
          </FormField>
          <FormField label="启用来源批准" htmlFor="source-enabled" hint="停用将立即阻止原目录的后续解析；重新启用后必须重新验证目录。">
            <Switch id="source-enabled" checked={fields.enabled} disabled={busy} onCheckedChange={(checked) => change("enabled", checked)} />
          </FormField>
        </DialogBody>
        <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>取消</Button>
          <Button type="submit" disabled={busy}>{busy ? "保存中…" : "确认保存批准"}</Button></DialogFooter>
      </form>
    </DialogContent>
  </Dialog>;
}
