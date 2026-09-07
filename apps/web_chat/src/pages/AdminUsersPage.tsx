import { useEffect, useState, type FormEvent } from "react";
import {
  CircleAlert,
  KeyRound,
  LoaderCircle,
  Pencil,
  Plus,
  RefreshCw,
  UserCheck,
  UserX,
} from "lucide-react";

import {
  createUser,
  listUsers,
  resetUserPassword,
  updateUser,
} from "@/api";
import { DepartmentTreeSelect } from "@/components/DepartmentTreeSelect";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { FormField } from "@/components/FormField";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  getDepartmentNamePath,
  refreshOrganizationUnits,
  selectOrganizationUnits,
} from "@/store/organizationUnitsSlice";
import type {
  AdminUser,
  AdminUserCreateInput,
  AdminUserUpdateInput,
} from "@/types";

const EMPTY_CREATE_FORM: AdminUserCreateInput = {
  email: "",
  password: "",
  display_name: "",
  organization_unit_id: null,
  job_title: "",
  phone: "",
  roles: ["employee"],
};

/** 用户列表允许选择的每页数据量。 */
const USER_TABLE_PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const;

function editableUser(user: AdminUser): AdminUserUpdateInput {
  return {
    display_name: user.display_name,
    organization_unit_id: user.organization_unit_id,
    job_title: user.job_title,
    phone: user.phone,
    roles: [...user.roles],
    status: user.status,
  };
}

export function AdminUsersPage() {
  const dispatch = useAppDispatch();
  const units = useAppSelector(selectOrganizationUnits);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
  const [editTarget, setEditTarget] = useState<AdminUser | null>(null);
  const [editForm, setEditForm] = useState<AdminUserUpdateInput | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [createForm, setCreateForm] = useState<AdminUserCreateInput>(EMPTY_CREATE_FORM);
  const [createOpen, setCreateOpen] = useState(false);
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const [tablePagination, setTablePagination] = useState({ current: 1, pageSize: 10 });
  const [totalUsers, setTotalUsers] = useState(0);

  async function load(pagination = tablePagination) {
    setLoading(true);
    setError(null);
    try {
      const result = await withRefreshedToken((token) => listUsers(token, {
        page: pagination.current,
        pageSize: pagination.pageSize,
      }));
      setUsers(result.value.items);
      setTotalUsers(result.value.total);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载用户失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), []);

  async function refreshDepartmentsAfterUserChange(successMessage: string) {
    const result = await dispatch(refreshOrganizationUnits({ force: true }));
    if (refreshOrganizationUnits.fulfilled.match(result)) {
      notify.success(successMessage);
      return;
    }
    notify.warning(`${successMessage.replace(/。$/, "")}，但部门人数刷新失败，请稍后重试。`);
  }

  function showEdit(user: AdminUser) {
    setEditTarget(user);
    setEditForm(editableUser(user));
    setFormErrors({});
  }

  function closeCreateDialog() {
    if (busy) return;
    setCreateOpen(false);
    setCreateForm(EMPTY_CREATE_FORM);
    setFormErrors({});
  }

  function closeEditDialog() {
    if (busy) return;
    setEditTarget(null);
    setEditForm(null);
    setFormErrors({});
  }

  function closeResetDialog() {
    if (busy) return;
    setResetTarget(null);
    setNewPassword("");
    setFormErrors({});
  }

  function setFieldValidation(field: string, message?: string) {
    setFormErrors((current) => {
      const next = { ...current };
      if (message) next[field] = message;
      else delete next[field];
      return next;
    });
  }

  function validateCreateField(field: "display_name" | "email" | "password") {
    const value = createForm[field];
    const message = field === "display_name"
      ? (value.trim() ? undefined : "请输入姓名")
      : field === "email"
        ? (!value.trim() ? "请输入邮箱地址" : !/^\S+@\S+\.\S+$/.test(value.trim()) ? "请输入有效的邮箱地址" : undefined)
        : (!value ? "请输入初始密码" : value.length < 10 ? "密码至少需要 10 个字符" : undefined);
    setFieldValidation(field, message);
  }

  async function submitCreate(event: FormEvent) {
    event.preventDefault();
    const errors: Record<string, string> = {};
    if (!createForm.display_name.trim()) errors.display_name = "请输入姓名";
    if (!createForm.email.trim()) errors.email = "请输入邮箱地址";
    else if (!/^\S+@\S+\.\S+$/.test(createForm.email.trim())) errors.email = "请输入有效的邮箱地址";
    if (!createForm.password) errors.password = "请输入初始密码";
    else if (createForm.password.length < 10) errors.password = "密码至少需要 10 个字符";
    setFormErrors(errors);
    if (Object.keys(errors).length > 0) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => createUser(token, createForm));
      setCreateForm(EMPTY_CREATE_FORM);
      setFormErrors({});
      setCreateOpen(false);
      await load();
      await refreshDepartmentsAfterUserChange("用户已创建，可立即使用邮箱密码登录。");
    } catch (cause) {
      notify.error(cause, "创建用户失败");
    } finally {
      setBusy(false);
    }
  }

  async function submitEdit(event: FormEvent) {
    event.preventDefault();
    if (!editTarget || !editForm) return;
    const errors: Record<string, string> = {};
    if (!editForm.display_name.trim()) errors.display_name = "请输入姓名";
    setFormErrors(errors);
    if (Object.keys(errors).length > 0) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => updateUser(token, editTarget.id, editForm));
      setEditTarget(null);
      setEditForm(null);
      setFormErrors({});
      await load();
      await refreshDepartmentsAfterUserChange("用户资料已更新。");
    } catch (cause) {
      notify.error(cause, "更新用户失败");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(user: AdminUser) {
    setBusy(true);
    try {
      await withRefreshedToken((token) =>
        updateUser(token, user.id, {
          ...editableUser(user),
          status: user.status === "active" ? "disabled" : "active",
        }),
      );
      await load();
      if (user.status === "active") {
        notify.warning("用户已停用。");
      } else {
        notify.success("用户已启用。");
      }
    } catch (cause) {
      notify.error(cause, "更新用户失败");
    } finally {
      setBusy(false);
    }
  }

  async function submitPasswordReset(event: FormEvent) {
    event.preventDefault();
    if (!resetTarget) return;
    const errors: Record<string, string> = {};
    if (!newPassword) errors.newPassword = "请输入新密码";
    else if (newPassword.length < 10) errors.newPassword = "密码至少需要 10 个字符";
    setFormErrors(errors);
    if (Object.keys(errors).length > 0) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) =>
        resetUserPassword(token, resetTarget.id, newPassword),
      );
      setResetTarget(null);
      setNewPassword("");
      setFormErrors({});
      notify.success("密码已重置，旧密码不再有效。");
    } catch (cause) {
      notify.error(cause, "重置密码失败");
    } finally {
      setBusy(false);
    }
  }

  const columns: AppTableColumn<AdminUser>[] = [
    {
      title: "用户",
      key: "user",
      width: "28%",
      render: (_, user) => (
        <div className="flex min-w-[220px] items-center gap-4">
          <span className="grid size-11 shrink-0 place-items-center rounded-full bg-emerald-50 font-semibold text-emerald-700">{user.display_name.slice(0, 1)}</span>
          <div className="min-w-0">
            <div className="truncate font-semibold">{user.display_name}</div>
            <div className="mt-1 truncate text-xs text-[#919eab]">{user.email}</div>
          </div>
        </div>
      ),
    },
    {
      title: "职位 / 角色",
      key: "job-role",
      width: "18%",
      render: (_, user) => (
        <div>
          <div className="text-sm">{user.job_title || "企业成员"}</div>
          <div className="mt-1 text-xs text-[#919eab]">{user.roles.includes("platform_admin") ? "平台管理员" : "员工"}</div>
        </div>
      ),
    },
    {
      title: "部门",
      key: "department",
      width: "25%",
      render: (_, user) => (
        <span className="block max-w-[240px] truncate text-sm text-[#637381]">
          {getDepartmentNamePath(units, user.organization_unit_id).join(" / ") || user.organization_unit_name || "未分配部门"}
        </span>
      ),
    },
    {
      title: "手机号",
      dataIndex: "phone",
      width: "12%",
      render: (phone) => <span className="text-sm text-[#637381]">{typeof phone === "string" && phone ? phone : "未设置"}</span>,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: "8%",
      render: (status) => (
        <span className={status === "active" ? "text-sm font-medium text-emerald-600" : "text-sm font-medium text-[#919eab]"}>
          {status === "active" ? "已启用" : "已停用"}
        </span>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 132,
      align: "right",
      render: (_, user) => (
        <div className="flex min-w-[132px] justify-end">
          <Button size="icon" variant="ghost" onClick={() => showEdit(user)} title="编辑用户" aria-label={`编辑 ${user.display_name}`}><Pencil /></Button>
          <Button size="icon" variant="ghost" onClick={() => { setFormErrors({}); setResetTarget(user); }} title="重置密码" aria-label={`重置 ${user.display_name} 的密码`}><KeyRound /></Button>
          <Button size="icon" variant="ghost" onClick={() => void toggle(user)} disabled={busy} title={user.status === "active" ? "停用" : "启用"} aria-label={`${user.status === "active" ? "停用" : "启用"} ${user.display_name}`}>
            {user.status === "active" ? <UserX className="text-red-500" /> : <UserCheck className="text-emerald-600" />}
          </Button>
        </div>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="用户管理"
        description="创建和维护企业成员账号、组织归属与系统权限。"
        actions={<Button type="button" onClick={() => { setFormErrors({}); setCreateOpen(true); }}><Plus />创建用户</Button>}
      />
      {error && (
        <Alert variant="destructive" className="mb-5">
          <CircleAlert className="size-5" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      <div>
        <section className="overflow-hidden rounded-2xl bg-white">
          <div className="flex items-center justify-between px-6 py-3">
            <h2 className="font-semibold">企业成员</h2>
            <Button variant="ghost" size="icon" onClick={() => void load()} aria-label="刷新">
              <RefreshCw className={loading ? "animate-spin" : ""} />
            </Button>
          </div>
          {loading ? (
            <div className="grid h-52 place-items-center"><LoaderCircle className="animate-spin text-emerald-500" /></div>
          ) : (
            <AppTable
              columns={columns}
              dataSource={users}
              rowKey="id"
              pagination={{
                current: tablePagination.current,
                pageSize: tablePagination.pageSize,
                total: totalUsers,
                pageSizeOptions: USER_TABLE_PAGE_SIZE_OPTIONS,
                onChange: (current, pageSize) => {
                  const nextPagination = { current, pageSize };
                  setTablePagination(nextPagination);
                  void load(nextPagination);
                },
              }}
              ariaLabel="企业成员列表"
            />
          )}
        </section>

      </div>

      <Dialog open={createOpen} onOpenChange={(open) => { if (open) setCreateOpen(true); else closeCreateDialog(); }}>
        <DialogContent className="max-w-2xl">
          <form onSubmit={submitCreate} noValidate>
            <DialogHeader><DialogTitle>创建用户</DialogTitle><DialogDescription>用户创建后可立即使用邮箱密码登录。</DialogDescription></DialogHeader>
            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <FormField label="姓名" htmlFor="create-user-name" required error={formErrors.display_name}><Input id="create-user-name" value={createForm.display_name} onChange={(event) => { setCreateForm({ ...createForm, display_name: event.target.value }); setFieldValidation("display_name"); }} onBlur={() => validateCreateField("display_name")} placeholder="请输入姓名" aria-invalid={Boolean(formErrors.display_name)} aria-describedby={formErrors.display_name ? "create-user-name-error" : undefined} /></FormField>
              <FormField label="邮箱" htmlFor="create-user-email" required error={formErrors.email}><Input id="create-user-email" type="email" value={createForm.email} onChange={(event) => { setCreateForm({ ...createForm, email: event.target.value }); setFieldValidation("email"); }} onBlur={() => validateCreateField("email")} placeholder="请输入邮箱地址" aria-invalid={Boolean(formErrors.email)} aria-describedby={formErrors.email ? "create-user-email-error" : undefined} /></FormField>
              <FormField label="初始密码" htmlFor="create-user-password" required error={formErrors.password}><Input id="create-user-password" type="password" value={createForm.password} onChange={(event) => { setCreateForm({ ...createForm, password: event.target.value }); setFieldValidation("password"); }} onBlur={() => validateCreateField("password")} placeholder="请输入初始密码" aria-invalid={Boolean(formErrors.password)} aria-describedby={formErrors.password ? "create-user-password-error" : undefined} /></FormField>
              <FormField label="手机号" htmlFor="create-user-phone"><Input id="create-user-phone" value={createForm.phone} onChange={(event) => setCreateForm({ ...createForm, phone: event.target.value })} placeholder="请输入手机号" /></FormField>
              <div className="md:col-span-2"><DepartmentTreeSelect value={createForm.organization_unit_id} onChange={(organizationUnitId) => setCreateForm({ ...createForm, organization_unit_id: organizationUnitId })} /></div>
              <FormField label="职位" htmlFor="create-user-job"><Input id="create-user-job" value={createForm.job_title} onChange={(event) => setCreateForm({ ...createForm, job_title: event.target.value })} placeholder="请输入职位" /></FormField>
              <div className="md:col-span-2">
                <SwitchField label="平台管理员权限" description="允许访问企业管理功能" checked={createForm.roles.includes("platform_admin")} onChange={(checked) => setCreateForm({ ...createForm, roles: checked ? ["employee", "platform_admin"] : ["employee"] })} />
              </div>
            </div>
            <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={closeCreateDialog} disabled={busy}>取消</Button><Button type="submit" className="bg-[#1c252e] hover:bg-[#2b3742]" disabled={busy}>{busy ? "保存中…" : "创建账号"}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={editTarget !== null} onOpenChange={(open) => { if (open) return; closeEditDialog(); }}>
        <DialogContent className="max-w-2xl">
          {editTarget && editForm && (
          <form onSubmit={submitEdit} noValidate>
              <DialogHeader><DialogTitle>编辑用户</DialogTitle><DialogDescription>登录邮箱 {editTarget.email} 不可修改；资料、组织和权限保存后立即生效。</DialogDescription></DialogHeader>
              <div className="mt-6 grid gap-4 md:grid-cols-2">
                <FormField label="姓名" htmlFor="edit-user-name" required error={formErrors.display_name}><Input id="edit-user-name" value={editForm.display_name} onChange={(event) => { setEditForm({ ...editForm, display_name: event.target.value }); setFieldValidation("display_name"); }} onBlur={(event) => setFieldValidation("display_name", event.currentTarget.value.trim() ? undefined : "请输入姓名")} placeholder="请输入姓名" aria-invalid={Boolean(formErrors.display_name)} aria-describedby={formErrors.display_name ? "edit-user-name-error" : undefined} /></FormField>
                <FormField label="职位" htmlFor="edit-user-job"><Input id="edit-user-job" value={editForm.job_title} onChange={(event) => setEditForm({ ...editForm, job_title: event.target.value })} placeholder="请输入职位" /></FormField>
                <FormField label="手机号" htmlFor="edit-user-phone"><Input id="edit-user-phone" value={editForm.phone} onChange={(event) => setEditForm({ ...editForm, phone: event.target.value })} placeholder="请输入手机号" /></FormField>
                <div className="md:col-span-2"><DepartmentTreeSelect value={editForm.organization_unit_id} onChange={(organizationUnitId) => setEditForm({ ...editForm, organization_unit_id: organizationUnitId })} /></div>
                <div className="grid gap-4 md:col-span-2 md:grid-cols-2">
                  <SwitchField label="账号状态" description={editForm.status === "active" ? "用户可以登录系统" : "用户已禁止登录"} checked={editForm.status === "active"} onChange={(checked) => setEditForm({ ...editForm, status: checked ? "active" : "disabled" })} />
                  <SwitchField label="平台管理员权限" description="允许访问企业管理功能" checked={editForm.roles.includes("platform_admin")} onChange={(checked) => setEditForm({ ...editForm, roles: checked ? ["employee", "platform_admin"] : ["employee"] })} />
                </div>
              </div>
              <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={closeEditDialog}>取消</Button><Button type="submit" disabled={busy}>{busy ? "保存中…" : "保存修改"}</Button></DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={resetTarget !== null} onOpenChange={(open) => { if (open) return; closeResetDialog(); }}>
        <DialogContent>
          <form onSubmit={submitPasswordReset} noValidate>
            <DialogHeader><DialogTitle>重置用户密码</DialogTitle><DialogDescription>为 {resetTarget?.display_name} 设置新的登录密码，旧密码会立即失效。</DialogDescription></DialogHeader>
            <FormField label="新密码" htmlFor="reset-user-password" required error={formErrors.newPassword}><Input id="reset-user-password" type="password" value={newPassword} onChange={(event) => { setNewPassword(event.target.value); setFieldValidation("newPassword"); }} onBlur={() => setFieldValidation("newPassword", !newPassword ? "请输入新密码" : newPassword.length < 10 ? "密码至少需要 10 个字符" : undefined)} placeholder="请输入新密码" autoFocus aria-invalid={Boolean(formErrors.newPassword)} aria-describedby={formErrors.newPassword ? "reset-user-password-error" : undefined} /></FormField>
            <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={closeResetDialog}>取消</Button><Button type="submit" disabled={busy}>确认重置</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

function SwitchField({ label, description, checked, onChange }: { label: string; description: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <div className="flex min-h-12 items-center justify-between gap-4 border-b border-[#eef1f4] py-2.5">
      <div className="min-w-0">
        <div className="text-sm font-medium text-[#1c252e]">{label}</div>
        <div className="mt-1 text-xs text-[#919eab]">{description}</div>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} aria-label={label} />
    </div>
  );
}
