import { useCallback, useRef, useState } from "react";
import { ApiError, createKnowledgeDocument, listKnowledgeSources, listUsers } from "@/api";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { FormField } from "@/components/FormField";
import { SearchableSelect } from "@/components/SearchableSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import type { KnowledgeDocumentCreateInput } from "@/types";
import { useKnowledgeBusyDialogFocus } from "../lib/useKnowledgeDialogFocus";

type Field = keyof KnowledgeDocumentCreateInput;
const labels: Record<Field, string> = { source_id: "知识来源", external_key: "文档标识", owner_user_id: "负责人", title: "初始标题", content_markdown: "初始正文" };
const initial: KnowledgeDocumentCreateInput = { source_id: "", external_key: "", owner_user_id: "", title: "", content_markdown: "" };
function validate(field: Field, value: string) {
  if (!value.trim()) return `请选择或填写${labels[field]}`;
  if (field === "external_key" && value.trim().length > 200) return "文档标识最多200个字符";
  if (field === "title" && value.trim().length > 500) return "标题最多500个字符";
  return "";
}

/** 弹窗按打开周期挂载；失败保留输入，未知提交不自动重放。 */
export function KnowledgeCreateDialog({ onClose, onCreated, onCloseAutoFocus }: { onClose: () => void; onCreated: (id: string) => void; onCloseAutoFocus?: (event: Event) => void }) {
  const [values, setValues] = useState(initial);
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({});
  const [busy, setBusy] = useState(false);
  const contentRef = useKnowledgeBusyDialogFocus(busy);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const submitting = useRef(false);
  const loadSources = useCallback(async (keywords: string, pageSize: number) => {
    const result = await withRefreshedToken(token => listKnowledgeSources(token, { keywords, pageSize, page: 1 }));
    return result.value.items.map(source => ({ value: source.id, label: source.name }));
  }, []);
  const loadOwners = useCallback(async (keywords: string, pageSize: number) => {
    const result = await withRefreshedToken(token => listUsers(token, { keywords, pageSize, page: 1 }));
    return result.value.items.map(user => ({ value: user.id, label: user.display_name, description: user.email }));
  }, []);
  function change(field: Field, value: string) {
    setValues(current => ({ ...current, [field]: value }));
    setErrors(current => ({ ...current, [field]: validate(field, value) }));
  }
  async function save() {
    if (submitting.current || unconfirmed) return;
    const nextErrors = Object.fromEntries((Object.keys(values) as Field[]).map(field => [field, validate(field, values[field])]));
    setErrors(nextErrors);
    if (Object.values(nextErrors).some(Boolean)) return;
    submitting.current = true;
    setBusy(true);
    try {
      const result = await withRefreshedToken(token => createKnowledgeDocument(token, {
        ...values, external_key: values.external_key.trim(), title: values.title.trim(),
      }));
      notify.success("知识草稿已创建，请配置访问范围后审核发布。");
      onCreated(result.value.id);
    } catch (cause) {
      notify.error(cause, "创建知识失败");
      if (!(cause instanceof ApiError) || cause.status >= 500) setUnconfirmed(true);
    } finally { submitting.current = false; setBusy(false); }
  }
  const fieldProps = (field: Field) => ({ id: `knowledge-create-${field}`, "aria-invalid": Boolean(errors[field]), "aria-describedby": errors[field] ? `knowledge-create-${field}-error` : undefined });
  return <Dialog open onOpenChange={open => { if (!open && !submitting.current) onClose(); }}>
    <DialogContent ref={contentRef} aria-busy={busy} className="max-w-xl" closeDisabled={busy} onCloseAutoFocus={onCloseAutoFocus} onEscapeKeyDown={event => { if (submitting.current) event.preventDefault(); }}>
      <DialogHeader><DialogTitle>创建知识</DialogTitle><DialogDescription>只创建草稿。默认无正文授权，负责人和管理员也需要显式访问范围；发布需要独立审核。</DialogDescription></DialogHeader>
      <DialogBody>
        <form id="knowledge-create-form" noValidate className="space-y-4" onSubmit={event => { event.preventDefault(); void save(); }}>
          <FormField label={labels.source_id} htmlFor="knowledge-create-source_id" required error={errors.source_id} hint="来源需由管理员事先登记；没有匹配来源时不能创建。">
            <SearchableSelect id="knowledge-create-source_id" ariaLabel="知识来源" ariaInvalid={Boolean(errors.source_id)} ariaDescribedBy={errors.source_id ? "knowledge-create-source_id-error" : undefined} value={values.source_id} onValueChange={value => change("source_id", value)} loadOptions={loadSources} placeholder="请选择知识来源" searchPlaceholder="请输入来源名称" disabled={busy || unconfirmed} />
          </FormField>
          <FormField label={labels.external_key} htmlFor="knowledge-create-external_key" required error={errors.external_key}>
            <Input {...fieldProps("external_key")} value={values.external_key} onChange={event => change("external_key", event.target.value)} placeholder="请输入文档标识" disabled={busy || unconfirmed} />
          </FormField>
          <FormField label={labels.owner_user_id} htmlFor="knowledge-create-owner_user_id" required error={errors.owner_user_id}>
            <SearchableSelect id="knowledge-create-owner_user_id" ariaLabel="负责人" ariaInvalid={Boolean(errors.owner_user_id)} ariaDescribedBy={errors.owner_user_id ? "knowledge-create-owner_user_id-error" : undefined} value={values.owner_user_id} onValueChange={value => change("owner_user_id", value)} loadOptions={loadOwners} placeholder="请选择负责人" searchPlaceholder="请输入负责人姓名" disabled={busy || unconfirmed} />
          </FormField>
          <FormField label={labels.title} htmlFor="knowledge-create-title" required error={errors.title}>
            <Input {...fieldProps("title")} value={values.title} onChange={event => change("title", event.target.value)} placeholder="请输入初始标题" disabled={busy || unconfirmed} />
          </FormField>
          <FormField label={labels.content_markdown} htmlFor="knowledge-create-content_markdown" required error={errors.content_markdown}>
            <Textarea {...fieldProps("content_markdown")} value={values.content_markdown} onChange={event => change("content_markdown", event.target.value)} placeholder="请输入初始正文" className="min-h-36" disabled={busy || unconfirmed} />
          </FormField>
        </form>
        {unconfirmed && <p role="alert" className="mt-4 text-sm text-red-600">创建结果未确认。请关闭弹窗，在列表搜索原文档标识并核对，勿重复创建。</p>}
      </DialogBody>
      <DialogFooter><Button variant="outline" disabled={busy} onClick={onClose}>取消</Button><Button type="submit" form="knowledge-create-form" disabled={busy || unconfirmed}>{busy ? "创建中…" : "创建草稿"}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
