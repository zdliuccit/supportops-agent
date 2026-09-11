import type { FormEvent } from "react";
import { DepartmentTreeSelect } from "@/components/DepartmentTreeSelect";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { OrganizationUnitCreateInput } from "@/types";

/** 新增部门弹窗，仅负责表单展示和事件转发。 */
export function CreateDepartmentDialog({ open, form, error, busy, onOpenChange, onSubmit, onChange, onValidateName, onCancel }: { open: boolean; form: OrganizationUnitCreateInput; error: string | null; busy: boolean; onOpenChange: (open: boolean) => void; onSubmit: (event: FormEvent) => void; onChange: (form: OrganizationUnitCreateInput) => void; onValidateName: (value: string) => void; onCancel: () => void }) {
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent><form onSubmit={onSubmit} noValidate><DialogHeader><DialogTitle>新增部门</DialogTitle><DialogDescription>填写部门名称并选择上级部门。</DialogDescription></DialogHeader><DepartmentTreeSelect className="mt-4" label="上级部门" placeholder="请选择上级部门" emptyLabel="无上级部门" value={form.parent_id} onChange={(parent_id) => onChange({ ...form, parent_id })} /><FormField label="部门名称" htmlFor="create-department-name" required error={error ?? undefined} className="mt-4"><Input id="create-department-name" value={form.name} onChange={(event) => onChange({ ...form, name: event.target.value })} onBlur={(event) => onValidateName(event.currentTarget.value)} placeholder="请输入部门名称" aria-invalid={Boolean(error)} aria-describedby={error ? "create-department-name-error" : undefined} /></FormField><DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={onCancel} disabled={busy}>取消</Button><Button type="submit" disabled={busy}>{busy ? "创建中…" : "创建部门"}</Button></DialogFooter></form></DialogContent></Dialog>;
}
