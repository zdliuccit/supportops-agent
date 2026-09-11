import type { FormEvent } from "react";
import { DepartmentTreeSelect } from "@/components/DepartmentTreeSelect";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { AdminUserCreateInput } from "@/types";
import { SwitchField } from "./SwitchField";

type Props = {
  open: boolean;
  busy: boolean;
  form: AdminUserCreateInput;
  errors: Record<string, string>;
  onOpenChange: (open: boolean) => void;
  onSubmit: (event: FormEvent) => void;
  onChange: (form: AdminUserCreateInput) => void;
  onValidate: (field: "display_name" | "email" | "password", value: string) => void;
  onCancel: () => void;
};

export function CreateUserDialog({ open, busy, form, errors, onOpenChange, onSubmit, onChange, onValidate, onCancel }: Props) {
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-w-2xl"><form onSubmit={onSubmit} noValidate>
    <DialogHeader><DialogTitle>创建用户</DialogTitle><DialogDescription>用户创建后可立即使用邮箱密码登录。</DialogDescription></DialogHeader>
    <div className="mt-6 grid gap-4 md:grid-cols-2">
      <FormField label="姓名" htmlFor="create-user-name" required error={errors.display_name}><Input id="create-user-name" value={form.display_name} onChange={(event) => { const value = event.target.value; onChange({ ...form, display_name: value }); onValidate("display_name", value); }} placeholder="请输入姓名" aria-invalid={Boolean(errors.display_name)} aria-describedby={errors.display_name ? "create-user-name-error" : undefined} /></FormField>
      <FormField label="邮箱" htmlFor="create-user-email" required error={errors.email}><Input id="create-user-email" type="email" value={form.email} onChange={(event) => { const value = event.target.value; onChange({ ...form, email: value }); onValidate("email", value); }} placeholder="请输入邮箱地址" aria-invalid={Boolean(errors.email)} aria-describedby={errors.email ? "create-user-email-error" : undefined} /></FormField>
      <FormField label="初始密码" htmlFor="create-user-password" required error={errors.password}><Input id="create-user-password" type="password" value={form.password} onChange={(event) => { const value = event.target.value; onChange({ ...form, password: value }); onValidate("password", value); }} placeholder="请输入初始密码" aria-invalid={Boolean(errors.password)} aria-describedby={errors.password ? "create-user-password-error" : undefined} /></FormField>
      <FormField label="手机号" htmlFor="create-user-phone"><Input id="create-user-phone" value={form.phone} onChange={(event) => onChange({ ...form, phone: event.target.value })} placeholder="请输入手机号" /></FormField>
      <div className="md:col-span-2"><DepartmentTreeSelect value={form.organization_unit_id} onChange={(organization_unit_id) => onChange({ ...form, organization_unit_id })} /></div>
      <FormField label="职位" htmlFor="create-user-job"><Input id="create-user-job" value={form.job_title} onChange={(event) => onChange({ ...form, job_title: event.target.value })} placeholder="请输入职位" /></FormField>
      <div className="md:col-span-2"><SwitchField label="平台管理员权限" description="允许访问企业管理功能" checked={form.roles.includes("platform_admin")} onChange={(checked) => onChange({ ...form, roles: checked ? ["employee", "platform_admin"] : ["employee"] })} /></div>
    </div>
    <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={onCancel} disabled={busy}>取消</Button><Button type="submit" className="bg-[#1c252e] hover:bg-[#2b3742]" disabled={busy}>{busy ? "保存中…" : "创建账号"}</Button></DialogFooter>
  </form></DialogContent></Dialog>;
}
