import type { FormEvent } from "react";
import { DepartmentTreeSelect } from "@/components/DepartmentTreeSelect";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { OrganizationUnit, OrganizationUnitUpdateInput } from "@/types";

/** 编辑部门弹窗。 */
export function EditDepartmentDialog({ target, form, error, busy, onOpenChange, onSubmit, onChange, onValidateName, onCancel }: { target: OrganizationUnit | null; form: OrganizationUnitUpdateInput | null; error: string | null; busy: boolean; onOpenChange: (open: boolean) => void; onSubmit: (event: FormEvent) => void; onChange: (form: OrganizationUnitUpdateInput) => void; onValidateName: (value: string) => void; onCancel: () => void }) {
  return <Dialog open={target !== null} onOpenChange={onOpenChange}><DialogContent>{target && form && <form onSubmit={onSubmit} noValidate><DialogHeader><DialogTitle>编辑部门</DialogTitle><DialogDescription>更新部门名称或上级部门。服务端会阻止跨租户或循环层级。</DialogDescription></DialogHeader><div className="mt-6 space-y-4"><DepartmentTreeSelect label="上级部门" placeholder="请选择上级部门" emptyLabel="无上级部门" excludedIds={new Set([target.id])} value={form.parent_id} onChange={(parent_id) => onChange({ ...form, parent_id })} /><FormField label="部门名称" htmlFor="edit-department-name" required error={error ?? undefined}><Input id="edit-department-name" value={form.name} onChange={(event) => { const value = event.target.value; onChange({ ...form, name: value }); onValidateName(value); }} placeholder="请输入部门名称" aria-invalid={Boolean(error)} aria-describedby={error ? "edit-department-name-error" : undefined} /></FormField></div><DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={onCancel}>取消</Button><Button type="submit" disabled={busy}>{busy ? "保存中…" : "保存修改"}</Button></DialogFooter></form>}</DialogContent></Dialog>;
}
