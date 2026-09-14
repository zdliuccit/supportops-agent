import { useEffect, useState, type FormEvent } from "react";
import {
  CircleAlert,
  KeyRound,
  Pencil,
  Plus,
  UserCheck,
  UserX,
} from "lucide-react";

import {
  createUser,
  listUsers,
  resetUserPassword,
  updateUser,
} from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { DepartmentTreeSelect } from "@/components/DepartmentTreeSelect";
import { ListToolbar } from "@/components/ListToolbar";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
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

import { CreateUserDialog } from "./components/CreateUserDialog";
import { EditUserDialog } from "./components/EditUserDialog";
import { ResetPasswordDialog } from "./components/ResetPasswordDialog";

const EMPTY_CREATE_FORM: AdminUserCreateInput = {
  email: "",
  password: "",
  display_name: "",
  organization_unit_id: null,
  job_title: "",
  phone: "",
  roles: ["employee"],
};

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

export function UserManagementPage() {
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
  const [keywords, setKeywords] = useState("");
  const [departmentId, setDepartmentId] = useState<string | null>(null);

  async function load(pagination = tablePagination) {
    setLoading(true);
    setError(null);
    try {
      const result = await delayRequest(() => withRefreshedToken((token) => listUsers(token, {
        page: pagination.current,
        pageSize: pagination.pageSize,
        keywords: keywords.trim() || undefined,
        organizationUnitId: departmentId || undefined,
      })));
      setUsers(result.value.items);
      setTotalUsers(result.value.total);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载用户失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const nextPagination = { current: 1, pageSize: tablePagination.pageSize };
    setTablePagination(nextPagination);
    // 先显示列表 Loading，delayRequest 内部再等待 300ms 发起接口请求。
    void load(nextPagination);
  }, [keywords, departmentId]);

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

  function validateCreateField(field: "display_name" | "email" | "password", value: string) {
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
        <StatusBadge value={String(status)} label={status === "active" ? "已启用" : "已停用"} />
      ),
    },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      minWidth: 132,
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
        <section className="">
          <ListToolbar onRefresh={() => void load()} loading={loading} filters={<div className="flex flex-wrap items-center gap-3">
            <Input className="h-9 w-72" placeholder="搜索姓名、邮箱、职位、手机号或部门 ID" value={keywords} onChange={(event) => setKeywords(event.target.value)} aria-label="搜索员工或部门 ID" />
            <DepartmentTreeSelect value={departmentId} onChange={setDepartmentId} label="" showLabel={false} placeholder="全部部门" emptyLabel="全部部门" className="w-fit min-w-[280px]" />
          </div>} />
          <div className="relative min-h-[360px]"><AppTable
              columns={columns}
              dataSource={users}
              rowKey="id"
              pagination={{
                current: tablePagination.current,
                pageSize: tablePagination.pageSize,
                total: totalUsers,
                onChange: (current, pageSize) => {
                  const nextPagination = { current, pageSize };
                  setTablePagination(nextPagination);
                  void load(nextPagination);
                },
              }}
              ariaLabel="企业成员列表"
            />{loading && <ListLoadingOverlay label="正在加载用户列表…" />}</div>
        </section>

      </div>

      {/* 页面专属新增、编辑、重置交互统一放在当前功能目录的 components 中。 */}
      <CreateUserDialog open={createOpen} busy={busy} form={createForm} errors={formErrors} onOpenChange={(open) => { if (open) setCreateOpen(true); else closeCreateDialog(); }} onSubmit={submitCreate} onChange={setCreateForm} onValidate={validateCreateField} onCancel={closeCreateDialog} />
      <EditUserDialog target={editTarget} form={editForm} errors={formErrors} busy={busy} onOpenChange={(open) => { if (!open) closeEditDialog(); }} onSubmit={submitEdit} onChange={(value) => setEditForm(value)} onValidateName={(value) => setFieldValidation("display_name", value.trim() ? undefined : "请输入姓名")} onCancel={closeEditDialog} />
      <ResetPasswordDialog target={resetTarget} password={newPassword} error={formErrors.newPassword} busy={busy} onOpenChange={(open) => { if (!open) closeResetDialog(); }} onSubmit={submitPasswordReset} onPasswordChange={(value) => { setNewPassword(value); setFieldValidation("newPassword", !value ? "请输入新密码" : value.length < 10 ? "密码至少需要 10 个字符" : undefined); }} onCancel={closeResetDialog} />
    </>
  );
}
