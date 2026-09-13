import type { FormEvent } from "react";
import { DepartmentTreeSelect } from "@/components/DepartmentTreeSelect";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/AppDialog";
import { Input } from "@/components/ui/input";
import type { AdminUser, AdminUserUpdateInput } from "@/types";
import { SwitchField } from "./SwitchField";

type Props = { target: AdminUser | null; form: AdminUserUpdateInput | null; errors: Record<string, string>; busy: boolean; onOpenChange: (open: boolean) => void; onSubmit: (event: FormEvent) => void; onChange: (form: AdminUserUpdateInput) => void; onValidateName: (value: string) => void; onCancel: () => void };

export function EditUserDialog({ target, form, errors, busy, onOpenChange, onSubmit, onChange, onValidateName, onCancel }: Props) {
  return <Dialog open={target !== null} onOpenChange={onOpenChange}><DialogContent className="max-w-2xl">{target && form && <form onSubmit={onSubmit} noValidate>
    <DialogHeader><DialogTitle>编辑用户</DialogTitle><DialogDescription>登录邮箱 {target.email} 不可修改；资料、组织和权限保存后立即生效。</DialogDescription></DialogHeader>
    <DialogBody><div className="mt-6 grid gap-4 md:grid-cols-2">
      <FormField label="姓名" htmlFor="edit-user-name" required error={errors.display_name}><Input id="edit-user-name" value={form.display_name} onChange={(event) => { const value = event.target.value; onChange({ ...form, display_name: value }); onValidateName(value); }} placeholder="请输入姓名" aria-invalid={Boolean(errors.display_name)} aria-describedby={errors.display_name ? "edit-user-name-error" : undefined} /></FormField>
      <FormField label="职位" htmlFor="edit-user-job"><Input id="edit-user-job" value={form.job_title} onChange={(event) => onChange({ ...form, job_title: event.target.value })} placeholder="请输入职位" /></FormField>
      <FormField label="手机号" htmlFor="edit-user-phone"><Input id="edit-user-phone" value={form.phone} onChange={(event) => onChange({ ...form, phone: event.target.value })} placeholder="请输入手机号" /></FormField>
      <div className="md:col-span-2"><DepartmentTreeSelect value={form.organization_unit_id} onChange={(organization_unit_id) => onChange({ ...form, organization_unit_id })} /></div>
      <div className="grid gap-4 md:col-span-2 md:grid-cols-2"><SwitchField label="账号状态" description={form.status === "active" ? "用户可以登录系统" : "用户已禁止登录"} checked={form.status === "active"} onChange={(checked) => onChange({ ...form, status: checked ? "active" : "disabled" })} /><SwitchField label="平台管理员权限" description="允许访问企业管理功能" checked={form.roles.includes("platform_admin")} onChange={(checked) => onChange({ ...form, roles: checked ? ["employee", "platform_admin"] : ["employee"] })} /></div>
    </div></DialogBody>
    <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={onCancel}>取消</Button><Button type="submit" disabled={busy}>{busy ? "保存中…" : "保存修改"}</Button></DialogFooter>
  </form>}</DialogContent></Dialog>;
}
