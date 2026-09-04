import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  CircleAlert,
  CircleCheck,
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
  listOrganizationUnits,
  listUsers,
  resetUserPassword,
  updateUser,
} from "@/api";
import { PageHeader } from "@/components/PageHeader";
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
import { withRefreshedToken } from "@/lib/auth";
import type {
  AdminUser,
  AdminUserCreateInput,
  AdminUserUpdateInput,
  OrganizationUnit,
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

function flatten(units: OrganizationUnit[]): OrganizationUnit[] {
  return units.flatMap((unit) => [unit, ...flatten(unit.children)]);
}

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
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [units, setUnits] = useState<OrganizationUnit[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
  const [editTarget, setEditTarget] = useState<AdminUser | null>(null);
  const [editForm, setEditForm] = useState<AdminUserUpdateInput | null>(null);
  const [newPassword, setNewPassword] = useState("");
  const [createForm, setCreateForm] = useState<AdminUserCreateInput>(EMPTY_CREATE_FORM);
  const [createOpen, setCreateOpen] = useState(false);
  const flatUnits = useMemo(() => flatten(units), [units]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await withRefreshedToken(async (token) => ({
        users: await listUsers(token),
        units: await listOrganizationUnits(token),
      }));
      setUsers(result.value.users.items);
      setUnits(result.value.units.items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载用户失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), []);

  function showEdit(user: AdminUser) {
    setEditTarget(user);
    setEditForm(editableUser(user));
    setError(null);
    setNotice(null);
  }

  async function submitCreate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await withRefreshedToken((token) => createUser(token, createForm));
      setCreateForm(EMPTY_CREATE_FORM);
      setCreateOpen(false);
      await load();
      setNotice("用户已创建，可立即使用邮箱密码登录。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建用户失败");
    } finally {
      setBusy(false);
    }
  }

  async function submitEdit(event: FormEvent) {
    event.preventDefault();
    if (!editTarget || !editForm) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await withRefreshedToken((token) => updateUser(token, editTarget.id, editForm));
      setEditTarget(null);
      setEditForm(null);
      await load();
      setNotice("用户资料已更新。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新用户失败");
    } finally {
      setBusy(false);
    }
  }

  async function toggle(user: AdminUser) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await withRefreshedToken((token) =>
        updateUser(token, user.id, {
          ...editableUser(user),
          status: user.status === "active" ? "disabled" : "active",
        }),
      );
      await load();
      setNotice(user.status === "active" ? "用户已停用。" : "用户已启用。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新用户失败");
    } finally {
      setBusy(false);
    }
  }

  async function submitPasswordReset(event: FormEvent) {
    event.preventDefault();
    if (!resetTarget) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await withRefreshedToken((token) =>
        resetUserPassword(token, resetTarget.id, newPassword),
      );
      setResetTarget(null);
      setNewPassword("");
      setNotice("密码已重置，旧密码不再有效。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重置密码失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="用户管理" description="创建和维护企业成员账号、组织归属与系统权限。" />
      {error && (
        <div role="alert" className="mb-5 flex gap-2 rounded-xl border border-red-100 bg-red-50 p-4 text-sm text-red-700">
          <CircleAlert className="size-5" />
          {error}
        </div>
      )}
      {notice && (
        <div role="status" className="mb-5 flex gap-2 rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-sm text-emerald-700">
          <CircleCheck className="size-5" />
          {notice}
        </div>
      )}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section className="overflow-hidden rounded-2xl bg-white shadow-[0_1px_2px_rgba(0,0,0,.04)]">
          <div className="flex items-center justify-between border-b px-6 py-5">
            <div>
              <h2 className="font-semibold">企业成员</h2>
              <p className="mt-1 text-xs text-[#919eab]">共 {users.length} 个账号</p>
            </div>
            <Button variant="ghost" size="icon" onClick={() => void load()} aria-label="刷新">
              <RefreshCw className={loading ? "animate-spin" : ""} />
            </Button>
          </div>
          {loading ? (
            <div className="grid h-52 place-items-center"><LoaderCircle className="animate-spin text-emerald-500" /></div>
          ) : users.length === 0 ? (
            <div className="p-12 text-center text-sm text-[#919eab]">尚未创建用户</div>
          ) : (
            <div className="divide-y">
              {users.map((user) => (
                <article key={user.id} className="flex flex-wrap items-center gap-4 px-6 py-4 hover:bg-slate-50/70">
                  <span className="grid size-11 place-items-center rounded-full bg-emerald-50 font-semibold text-emerald-700">{user.display_name.slice(0, 1)}</span>
                  <div className="min-w-48 flex-1">
                    <div className="font-semibold">{user.display_name}</div>
                    <div className="mt-1 text-xs text-[#919eab]">{user.email} · {user.organization_unit_name || "未分配组织"}</div>
                  </div>
                  <div className="hidden min-w-32 sm:block">
                    <div className="text-sm">{user.job_title || "企业成员"}</div>
                    <div className="mt-1 text-xs text-[#919eab]">{user.roles.includes("platform_admin") ? "平台管理员" : "员工"}</div>
                  </div>
                  <span className={user.status === "active" ? "rounded-lg bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-700" : "rounded-lg bg-slate-100 px-2.5 py-1 text-xs font-semibold text-slate-500"}>{user.status === "active" ? "已启用" : "已停用"}</span>
                  <Button size="icon" variant="ghost" onClick={() => showEdit(user)} title="编辑用户" aria-label={`编辑 ${user.display_name}`}><Pencil /></Button>
                  <Button size="icon" variant="ghost" onClick={() => { setResetTarget(user); setError(null); setNotice(null); }} title="重置密码" aria-label={`重置 ${user.display_name} 的密码`}><KeyRound /></Button>
                  <Button size="icon" variant="ghost" onClick={() => void toggle(user)} disabled={busy} title={user.status === "active" ? "停用" : "启用"} aria-label={`${user.status === "active" ? "停用" : "启用"} ${user.display_name}`}>
                    {user.status === "active" ? <UserX className="text-red-500" /> : <UserCheck className="text-emerald-600" />}
                  </Button>
                </article>
              ))}
            </div>
          )}
        </section>

        <section className="h-fit rounded-2xl bg-white p-6 shadow-[0_1px_2px_rgba(0,0,0,.04)]">
          <h2 className="font-semibold">账号操作</h2>
          <p className="mt-1 text-xs text-[#919eab]">创建企业成员账号并设置组织和权限。</p>
          <Button type="button" className="mt-6 w-full bg-[#1c252e] hover:bg-[#2b3742]" onClick={() => { setCreateOpen(true); setError(null); setNotice(null); }}><Plus />创建用户</Button>
        </section>
      </div>

      <Dialog open={createOpen} onOpenChange={(open) => { if (!busy) setCreateOpen(open); }}>
        <DialogContent>
          <form onSubmit={submitCreate}>
            <DialogHeader><DialogTitle>创建用户</DialogTitle><DialogDescription>用户创建后可立即使用邮箱密码登录。</DialogDescription></DialogHeader>
            <div className="mt-6 space-y-4">
              <label className="block text-sm">姓名<Input className="mt-2" value={createForm.display_name} onChange={(event) => setCreateForm({ ...createForm, display_name: event.target.value })} required /></label>
              <label className="block text-sm">邮箱<Input className="mt-2" type="email" value={createForm.email} onChange={(event) => setCreateForm({ ...createForm, email: event.target.value })} required /></label>
              <label className="block text-sm">初始密码<Input className="mt-2" type="password" minLength={10} value={createForm.password} onChange={(event) => setCreateForm({ ...createForm, password: event.target.value })} required /></label>
              <OrganizationSelect units={flatUnits} value={createForm.organization_unit_id} onChange={(organizationUnitId) => setCreateForm({ ...createForm, organization_unit_id: organizationUnitId })} />
              <label className="block text-sm">职位<Input className="mt-2" value={createForm.job_title} onChange={(event) => setCreateForm({ ...createForm, job_title: event.target.value })} /></label>
              <label className="block text-sm">手机号<Input className="mt-2" value={createForm.phone} onChange={(event) => setCreateForm({ ...createForm, phone: event.target.value })} /></label>
              <AdminRoleCheckbox checked={createForm.roles.includes("platform_admin")} onChange={(checked) => setCreateForm({ ...createForm, roles: checked ? ["employee", "platform_admin"] : ["employee"] })} />
            </div>
            <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={() => setCreateOpen(false)} disabled={busy}>取消</Button><Button type="submit" className="bg-[#1c252e] hover:bg-[#2b3742]" disabled={busy}>{busy ? "保存中…" : "创建账号"}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={editTarget !== null} onOpenChange={(open) => { if (!open) { setEditTarget(null); setEditForm(null); } }}>
        <DialogContent>
          {editTarget && editForm && (
            <form onSubmit={submitEdit}>
              <DialogHeader><DialogTitle>编辑用户</DialogTitle><DialogDescription>登录邮箱 {editTarget.email} 不可修改；资料、组织和权限保存后立即生效。</DialogDescription></DialogHeader>
              <div className="mt-6 space-y-4">
                <label className="block text-sm">姓名<Input className="mt-2" value={editForm.display_name} onChange={(event) => setEditForm({ ...editForm, display_name: event.target.value })} required /></label>
                <OrganizationSelect units={flatUnits} value={editForm.organization_unit_id} onChange={(organizationUnitId) => setEditForm({ ...editForm, organization_unit_id: organizationUnitId })} />
                <label className="block text-sm">职位<Input className="mt-2" value={editForm.job_title} onChange={(event) => setEditForm({ ...editForm, job_title: event.target.value })} /></label>
                <label className="block text-sm">手机号<Input className="mt-2" value={editForm.phone} onChange={(event) => setEditForm({ ...editForm, phone: event.target.value })} /></label>
                <AdminRoleCheckbox checked={editForm.roles.includes("platform_admin")} onChange={(checked) => setEditForm({ ...editForm, roles: checked ? ["employee", "platform_admin"] : ["employee"] })} />
                <label className="block text-sm">状态<select className="mt-2 h-10 w-full rounded-lg border bg-white px-3 text-sm" value={editForm.status} onChange={(event) => setEditForm({ ...editForm, status: event.target.value as AdminUserUpdateInput["status"] })}><option value="active">已启用</option><option value="disabled">已停用</option></select></label>
              </div>
              <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={() => setEditTarget(null)}>取消</Button><Button type="submit" disabled={busy}>{busy ? "保存中…" : "保存修改"}</Button></DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={resetTarget !== null} onOpenChange={(open) => { if (!open) { setResetTarget(null); setNewPassword(""); } }}>
        <DialogContent>
          <form onSubmit={submitPasswordReset}>
            <DialogHeader><DialogTitle>重置用户密码</DialogTitle><DialogDescription>为 {resetTarget?.display_name} 设置新的登录密码，旧密码会立即失效。</DialogDescription></DialogHeader>
            <Input className="mt-6" type="password" minLength={10} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoFocus required />
            <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={() => setResetTarget(null)}>取消</Button><Button type="submit" disabled={busy || newPassword.length < 10}>确认重置</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}

function OrganizationSelect({ units, value, onChange }: { units: OrganizationUnit[]; value: string | null; onChange: (value: string | null) => void }) {
  return <label className="block text-sm">组织<select className="mt-2 h-10 w-full rounded-lg border bg-white px-3 text-sm" value={value ?? ""} onChange={(event) => onChange(event.target.value || null)}><option value="">未分配</option>{units.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select></label>;
}

function AdminRoleCheckbox({ checked, onChange }: { checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="flex items-center gap-2 rounded-xl bg-slate-50 p-3 text-sm"><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="accent-emerald-600" />授予平台管理员权限</label>;
}
