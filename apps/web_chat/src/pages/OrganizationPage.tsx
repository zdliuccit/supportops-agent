import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Building2,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  LoaderCircle,
  Pencil,
  Plus,
  Save,
  Trash2,
  Users,
} from "lucide-react";

import {
  createOrganizationUnit,
  deleteOrganizationUnit,
  getCompany,
  listOrganizationUnits,
  updateCompany,
  updateOrganizationUnit,
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
  Company,
  OrganizationUnit,
  OrganizationUnitCreateInput,
  OrganizationUnitUpdateInput,
} from "@/types";

const EMPTY_CREATE_FORM: OrganizationUnitCreateInput = {
  name: "",
  code: "",
  parent_id: null,
  unit_type: "department",
  sort_order: 0,
};

function flatten(units: OrganizationUnit[]): OrganizationUnit[] {
  return units.flatMap((unit) => [unit, ...flatten(unit.children)]);
}

function editableUnit(unit: OrganizationUnit): OrganizationUnitUpdateInput {
  return {
    name: unit.name,
    code: unit.code,
    parent_id: unit.parent_id,
    unit_type: unit.unit_type,
    sort_order: unit.sort_order,
    status: unit.status,
  };
}

export function OrganizationPage() {
  const [company, setCompany] = useState<Company | null>(null);
  const [units, setUnits] = useState<OrganizationUnit[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [createForm, setCreateForm] = useState<OrganizationUnitCreateInput>(EMPTY_CREATE_FORM);
  const [createOpen, setCreateOpen] = useState(false);
  const [companyOpen, setCompanyOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<OrganizationUnit | null>(null);
  const [editForm, setEditForm] = useState<OrganizationUnitUpdateInput | null>(null);
  const flatUnits = useMemo(() => flatten(units), [units]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await withRefreshedToken(async (token) => ({
        company: await getCompany(token),
        tree: await listOrganizationUnits(token),
      }));
      setCompany(result.value.company);
      setUnits(result.value.tree.items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载组织架构失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), []);

  async function saveCompany(event: FormEvent) {
    event.preventDefault();
    if (!company || !company.slug) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await withRefreshedToken((token) =>
        updateCompany(token, {
          name: company.name,
          slug: company.slug ?? "",
          logo_url: company.logo_url,
          contact_email: company.contact_email,
        }),
      );
      setCompany(result.value);
      setNotice("公司资料已保存。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存公司资料失败");
    } finally {
      setBusy(false);
    }
  }

  async function addUnit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await withRefreshedToken((token) => createOrganizationUnit(token, createForm));
      setCreateForm(EMPTY_CREATE_FORM);
      setCreateOpen(false);
      await load();
      setNotice("组织单元已创建。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建组织失败");
    } finally {
      setBusy(false);
    }
  }

  function showEdit(unit: OrganizationUnit) {
    setEditTarget(unit);
    setEditForm(editableUnit(unit));
    setError(null);
    setNotice(null);
  }

  async function saveUnit(event: FormEvent) {
    event.preventDefault();
    if (!editTarget || !editForm) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await withRefreshedToken((token) =>
        updateOrganizationUnit(token, editTarget.id, editForm),
      );
      setEditTarget(null);
      setEditForm(null);
      await load();
      setNotice("组织资料已更新。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "更新组织失败");
    } finally {
      setBusy(false);
    }
  }

  async function removeUnit() {
    if (!editTarget) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await withRefreshedToken((token) => deleteOrganizationUnit(token, editTarget.id));
      setEditTarget(null);
      setEditForm(null);
      await load();
      setNotice("空组织单元已删除。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除组织失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="公司与组织架构" description="维护公司资料以及部门、小组的层级关系。" />
      {error && (
        <div role="alert" className="mb-5 flex gap-2 rounded-xl bg-red-50 p-4 text-sm text-red-700">
          <CircleAlert className="size-5" />
          {error}
        </div>
      )}
      {notice && (
        <div role="status" className="mb-5 flex gap-2 rounded-xl bg-emerald-50 p-4 text-sm text-emerald-700">
          <CircleCheck className="size-5" />
          {notice}
        </div>
      )}
      {loading ? (
        <div className="grid h-64 place-items-center"><LoaderCircle className="animate-spin text-emerald-500" /></div>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
          <section className="rounded-2xl bg-white p-6 shadow-sm">
            <div className="flex items-center gap-3">
              <span className="grid size-11 place-items-center rounded-xl bg-emerald-50 text-emerald-600"><Building2 /></span>
              <div><h2 className="font-semibold">组织结构</h2><p className="mt-1 text-xs text-[#919eab]">部门与小组按层级展示，点击编辑按钮调整资料。</p></div>
            </div>
            <div className="mt-6 space-y-2">
              {units.length === 0 ? (
                <div className="rounded-xl border border-dashed p-10 text-center text-sm text-[#919eab]">尚未创建组织单元</div>
              ) : units.map((unit) => (
                <TreeNode key={unit.id} unit={unit} depth={0} onEdit={showEdit} />
              ))}
            </div>
          </section>

          <div className="space-y-6">
            {company && <section className="rounded-2xl bg-white p-6 shadow-sm"><div className="flex items-center justify-between"><div><h2 className="font-semibold">公司资料</h2><p className="mt-1 text-xs text-[#919eab]">维护企业名称、简称、联系邮箱和 Logo。</p></div><span className="rounded-lg bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700">{company.status === "active" ? "正常" : company.status}</span></div><Button type="button" variant="outline" className="mt-5 w-full" onClick={() => setCompanyOpen(true)}><Pencil />编辑公司资料</Button></section>}
            <section className="rounded-2xl bg-white p-6 shadow-sm"><h2 className="font-semibold">组织操作</h2><p className="mt-1 text-xs text-[#919eab]">新增部门或小组，并设置上下级关系。</p><Button type="button" className="mt-5 w-full" onClick={() => setCreateOpen(true)}><Plus />新增组织</Button></section>
          </div>
        </div>
      )}

      <Dialog open={companyOpen} onOpenChange={(open) => { if (!busy) setCompanyOpen(open); }}>
        <DialogContent>
          {company && <form onSubmit={saveCompany}>
            <DialogHeader><DialogTitle>编辑公司资料</DialogTitle><DialogDescription>更新后的公司资料会同步显示在后台工作台。</DialogDescription></DialogHeader>
            <label className="mt-5 block text-sm">公司名称<Input className="mt-2" value={company.name} onChange={(event) => setCompany({ ...company, name: event.target.value })} required /></label>
            <label className="mt-4 block text-sm">公司简称<Input className="mt-2" pattern="[a-z0-9][a-z0-9-]*" value={company.slug ?? ""} onChange={(event) => setCompany({ ...company, slug: event.target.value.toLowerCase() })} required /></label>
            <label className="mt-4 block text-sm">联系邮箱<Input className="mt-2" type="email" value={company.contact_email ?? ""} onChange={(event) => setCompany({ ...company, contact_email: event.target.value || null })} /></label>
            <label className="mt-4 block text-sm">Logo URL<Input className="mt-2" type="url" value={company.logo_url ?? ""} onChange={(event) => setCompany({ ...company, logo_url: event.target.value || null })} /></label>
            <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={() => setCompanyOpen(false)} disabled={busy}>取消</Button><Button type="submit" disabled={busy}><Save />保存公司资料</Button></DialogFooter>
          </form>}
        </DialogContent>
      </Dialog>

      <Dialog open={createOpen} onOpenChange={(open) => { if (!busy) setCreateOpen(open); }}>
        <DialogContent>
          <form onSubmit={addUnit}>
            <DialogHeader><DialogTitle>新增组织</DialogTitle><DialogDescription>创建部门或小组，并设置其上级组织。</DialogDescription></DialogHeader>
            <label className="mt-5 block text-sm">名称<Input className="mt-2" value={createForm.name} onChange={(event) => setCreateForm({ ...createForm, name: event.target.value })} required /></label>
            <label className="mt-4 block text-sm">组织代码<Input className="mt-2" pattern="[A-Za-z0-9_-]+" value={createForm.code} onChange={(event) => setCreateForm({ ...createForm, code: event.target.value })} required /></label>
            <ParentSelect units={flatUnits} value={createForm.parent_id} onChange={(parentId) => setCreateForm({ ...createForm, parent_id: parentId })} />
            <label className="mt-4 block text-sm">类型<select className="mt-2 h-10 w-full rounded-lg border bg-white px-3 text-sm" value={createForm.unit_type} onChange={(event) => setCreateForm({ ...createForm, unit_type: event.target.value as OrganizationUnitCreateInput["unit_type"] })}><option value="department">部门</option><option value="team">小组</option></select></label>
            <label className="mt-4 block text-sm">排序<Input className="mt-2" type="number" value={createForm.sort_order} onChange={(event) => setCreateForm({ ...createForm, sort_order: Number(event.target.value) })} /></label>
            <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={() => setCreateOpen(false)} disabled={busy}>取消</Button><Button type="submit" disabled={busy}>创建组织</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={editTarget !== null} onOpenChange={(open) => { if (!open) { setEditTarget(null); setEditForm(null); } }}>
        <DialogContent>
          {editTarget && editForm && (
            <form onSubmit={saveUnit}>
              <DialogHeader><DialogTitle>编辑组织</DialogTitle><DialogDescription>更新名称、层级、类型、排序和状态。服务端会阻止跨租户或循环层级。</DialogDescription></DialogHeader>
              <div className="mt-6 space-y-4">
                <label className="block text-sm">名称<Input className="mt-2" value={editForm.name} onChange={(event) => setEditForm({ ...editForm, name: event.target.value })} required /></label>
                <label className="block text-sm">组织代码<Input className="mt-2" pattern="[A-Za-z0-9_-]+" value={editForm.code} onChange={(event) => setEditForm({ ...editForm, code: event.target.value })} required /></label>
                <ParentSelect units={flatUnits.filter((unit) => unit.id !== editTarget.id)} value={editForm.parent_id} onChange={(parentId) => setEditForm({ ...editForm, parent_id: parentId })} />
                <label className="block text-sm">类型<select className="mt-2 h-10 w-full rounded-lg border bg-white px-3 text-sm" value={editForm.unit_type} onChange={(event) => setEditForm({ ...editForm, unit_type: event.target.value as OrganizationUnitUpdateInput["unit_type"] })} disabled={editTarget.unit_type === "company"}><option value="company">公司</option><option value="department">部门</option><option value="team">小组</option></select></label>
                <label className="block text-sm">排序<Input className="mt-2" type="number" value={editForm.sort_order} onChange={(event) => setEditForm({ ...editForm, sort_order: Number(event.target.value) })} /></label>
                <label className="block text-sm">状态<select className="mt-2 h-10 w-full rounded-lg border bg-white px-3 text-sm" value={editForm.status} onChange={(event) => setEditForm({ ...editForm, status: event.target.value as OrganizationUnitUpdateInput["status"] })}><option value="active">已启用</option><option value="disabled">已停用</option></select></label>
              </div>
              <DialogFooter className="mt-6 justify-between">
                <Button type="button" variant="ghost" className="text-red-600 hover:bg-red-50 hover:text-red-700" onClick={() => void removeUnit()} disabled={busy || editTarget.unit_type === "company" || editTarget.children.length > 0 || editTarget.direct_user_count > 0}><Trash2 />删除空组织</Button>
                <div className="flex gap-2"><Button type="button" variant="outline" onClick={() => setEditTarget(null)}>取消</Button><Button type="submit" disabled={busy}>{busy ? "保存中…" : "保存修改"}</Button></div>
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

function TreeNode({ unit, depth, onEdit }: { unit: OrganizationUnit; depth: number; onEdit: (unit: OrganizationUnit) => void }) {
  return <div><div className="group flex items-center gap-3 rounded-xl px-3 py-3 hover:bg-slate-50" style={{ paddingLeft: `${12 + depth * 24}px` }}><ChevronRight className="size-4 text-[#919eab]" /><span className="grid size-9 place-items-center rounded-lg bg-slate-100"><Building2 className="size-4" /></span><div className="min-w-0 flex-1"><div className="font-medium">{unit.name}</div><div className="text-xs text-[#919eab]">{unit.code} · {unit.unit_type} · {unit.status === "active" ? "已启用" : "已停用"}</div></div><span className="flex items-center gap-1 text-xs text-[#637381]"><Users className="size-4" />{unit.direct_user_count}</span><Button size="icon" variant="ghost" className="opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100" onClick={() => onEdit(unit)} aria-label={`编辑 ${unit.name}`}><Pencil /></Button></div>{unit.children.map((child) => <TreeNode key={child.id} unit={child} depth={depth + 1} onEdit={onEdit} />)}</div>;
}

function ParentSelect({ units, value, onChange }: { units: OrganizationUnit[]; value: string | null; onChange: (value: string | null) => void }) {
  return <label className="mt-4 block text-sm">上级组织<select className="mt-2 h-10 w-full rounded-lg border bg-white px-3 text-sm" value={value ?? ""} onChange={(event) => onChange(event.target.value || null)}><option value="">无上级</option>{units.map((unit) => <option key={unit.id} value={unit.id}>{unit.name}</option>)}</select></label>;
}
