import { useEffect, useState, type FormEvent } from "react";
import {
  Building2,
  ChevronRight,
  CircleAlert,
  LoaderCircle,
  Pencil,
  Plus,
  Trash2,
  Users,
} from "lucide-react";

import {
  createOrganizationUnit,
  deleteOrganizationUnit,
  updateOrganizationUnit,
} from "@/api";
import { DepartmentTreeSelect } from "@/components/DepartmentTreeSelect";
import { FormField } from "@/components/FormField";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  refreshOrganizationUnits,
  selectOrganizationUnits,
  selectOrganizationUnitsError,
  selectOrganizationUnitsStatus,
} from "@/store/organizationUnitsSlice";
import type {
  OrganizationUnit,
  OrganizationUnitCreateInput,
  OrganizationUnitUpdateInput,
} from "@/types";

const EMPTY_CREATE_FORM: OrganizationUnitCreateInput = {
  name: "",
  parent_id: null,
};

/** 从服务端组织对象提取允许编辑的字段。 */
function editableUnit(unit: OrganizationUnit): OrganizationUnitUpdateInput {
  return {
    name: unit.name,
    parent_id: unit.parent_id,
  };
}

/** 企业部门层级的独立管理页面。 */
export function DepartmentManagementPage() {
  const dispatch = useAppDispatch();
  const units = useAppSelector(selectOrganizationUnits);
  const departmentStatus = useAppSelector(selectOrganizationUnitsStatus);
  const error = useAppSelector(selectOrganizationUnitsError);
  const [busy, setBusy] = useState(false);
  const [entryRefreshComplete, setEntryRefreshComplete] = useState(false);
  const [createForm, setCreateForm] = useState<OrganizationUnitCreateInput>(EMPTY_CREATE_FORM);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<OrganizationUnit | null>(null);
  const [editForm, setEditForm] = useState<OrganizationUnitUpdateInput | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<OrganizationUnit | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const loading = !entryRefreshComplete || departmentStatus === "loading";

  // 每次进入部门管理页都强制刷新，旧缓存只用于避免其他页面重复请求。
  useEffect(() => {
    let active = true;
    void dispatch(refreshOrganizationUnits()).then(() => {
      if (active) setEntryRefreshComplete(true);
    });
    return () => {
      active = false;
    };
  }, [dispatch]);

  async function refreshAfterMutation(successMessage: string) {
    const result = await dispatch(refreshOrganizationUnits({ force: true }));
    if (refreshOrganizationUnits.fulfilled.match(result)) {
      notify.success(successMessage);
      return;
    }
    notify.warning(`${successMessage.replace(/。$/, "")}，但刷新全局部门数据失败，请重试。`);
  }

  async function addUnit(event: FormEvent) {
    event.preventDefault();
    if (!createForm.name.trim()) {
      setFormError("请输入部门名称");
      return;
    }
    setFormError(null);
    setBusy(true);
    try {
      await withRefreshedToken((token) => createOrganizationUnit(token, createForm));
      setCreateForm(EMPTY_CREATE_FORM);
      setFormError(null);
      setCreateOpen(false);
      await refreshAfterMutation("部门已创建。");
    } catch (cause) {
      notify.error(cause, "创建部门失败");
    } finally {
      setBusy(false);
    }
  }

  function showEdit(unit: OrganizationUnit) {
    setEditTarget(unit);
    setEditForm(editableUnit(unit));
    setFormError(null);
  }

  function closeCreateDialog() {
    if (busy) return;
    setCreateOpen(false);
    setCreateForm(EMPTY_CREATE_FORM);
    setFormError(null);
  }

  function closeEditDialog() {
    if (busy) return;
    setEditTarget(null);
    setEditForm(null);
    setFormError(null);
  }

  async function saveUnit(event: FormEvent) {
    event.preventDefault();
    if (!editTarget || !editForm) return;
    if (!editForm.name.trim()) {
      setFormError("请输入部门名称");
      return;
    }
    setFormError(null);
    setBusy(true);
    try {
      await withRefreshedToken((token) => updateOrganizationUnit(token, editTarget.id, editForm));
      setEditTarget(null);
      setEditForm(null);
      setFormError(null);
      await refreshAfterMutation("部门资料已更新。");
    } catch (cause) {
      notify.error(cause, "更新部门失败");
    } finally {
      setBusy(false);
    }
  }

  async function removeUnit() {
    if (!deleteTarget || deleteTarget.children.length > 0 || deleteTarget.direct_user_count > 0) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => deleteOrganizationUnit(token, deleteTarget.id));
      setDeleteTarget(null);
      await refreshAfterMutation("空部门已删除。");
    } catch (cause) {
      notify.error(cause, "删除部门失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="部门管理"
        description="维护企业部门的多级层级关系与成员归属。"
        actions={<Button type="button" onClick={() => { setFormError(null); setCreateOpen(true); }}><Plus />新增部门</Button>}
      />
      {error && (
        <Alert variant="destructive" className="mb-5">
          <CircleAlert className="size-5" />
          <AlertDescription className="flex items-center justify-between gap-3">
            <span>{error}</span>
            <Button type="button" size="sm" variant="outline" onClick={() => void dispatch(refreshOrganizationUnits())}>重试</Button>
          </AlertDescription>
        </Alert>
      )}
      {loading ? (
        <div className="grid h-64 place-items-center"><LoaderCircle className="animate-spin text-emerald-500" /></div>
      ) : (
        <section>
          <TooltipProvider delayDuration={200}>
            <ul role="tree" aria-label="企业部门树" className="space-y-1">
              {units.length === 0 ? (
                <li className="rounded-xl border border-dashed p-10 text-center text-sm text-[#919eab]">尚未创建部门</li>
              ) : units.map((unit) => (
                <TreeNode key={unit.id} unit={unit} onEdit={showEdit} onDelete={setDeleteTarget} busy={busy} />
              ))}
            </ul>
          </TooltipProvider>
        </section>
      )}

      <Dialog open={createOpen} onOpenChange={(open) => { if (open) setCreateOpen(true); else closeCreateDialog(); }}>
        <DialogContent>
          <form onSubmit={addUnit} noValidate>
            <DialogHeader><DialogTitle>新增部门</DialogTitle><DialogDescription>填写部门名称并选择上级部门。</DialogDescription></DialogHeader>
            <DepartmentTreeSelect className="mt-4" label="上级部门" placeholder="请选择上级部门" emptyLabel="无上级部门" value={createForm.parent_id} onChange={(parentId) => setCreateForm({ ...createForm, parent_id: parentId })} />
            <FormField label="部门名称" htmlFor="create-department-name" required error={formError ?? undefined} className="mt-4"><Input id="create-department-name" value={createForm.name} onChange={(event) => { setCreateForm({ ...createForm, name: event.target.value }); setFormError(null); }} onBlur={(event) => setFormError(event.currentTarget.value.trim() ? null : "请输入部门名称")} placeholder="请输入部门名称" aria-invalid={Boolean(formError)} aria-describedby={formError ? "create-department-name-error" : undefined} /></FormField>
            <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={closeCreateDialog} disabled={busy}>取消</Button><Button type="submit" disabled={busy}>创建部门</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={editTarget !== null} onOpenChange={(open) => { if (open) return; closeEditDialog(); }}>
        <DialogContent>
          {editTarget && editForm && (
            <form onSubmit={saveUnit} noValidate>
              <DialogHeader><DialogTitle>编辑部门</DialogTitle><DialogDescription>更新部门名称或上级部门。服务端会阻止跨租户或循环层级。</DialogDescription></DialogHeader>
              <div className="mt-6 space-y-4">
                <DepartmentTreeSelect label="上级部门" placeholder="请选择上级部门" emptyLabel="无上级部门" excludedIds={new Set([editTarget.id])} value={editForm.parent_id} onChange={(parentId) => setEditForm({ ...editForm, parent_id: parentId })} />
                <FormField label="部门名称" htmlFor="edit-department-name" required error={formError ?? undefined}><Input id="edit-department-name" value={editForm.name} onChange={(event) => { setEditForm({ ...editForm, name: event.target.value }); setFormError(null); }} onBlur={(event) => setFormError(event.currentTarget.value.trim() ? null : "请输入部门名称")} placeholder="请输入部门名称" aria-invalid={Boolean(formError)} aria-describedby={formError ? "edit-department-name-error" : undefined} /></FormField>
              </div>
              <DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={closeEditDialog}>取消</Button><Button type="submit" disabled={busy}>{busy ? "保存中…" : "保存修改"}</Button></DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open && !busy) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除部门</AlertDialogTitle>
            <AlertDialogDescription>确定删除“{deleteTarget?.name}”吗？删除后无法恢复。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => void removeUnit()} disabled={busy} className="bg-red-600 text-white hover:bg-red-700">{busy ? "删除中…" : "确认删除"}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

/**
 * 读取部门及其所有后代部门的成员总数。
 *
 * 新版 API 会直接返回 user_count；在开发服务尚未重启或旧数据响应缺少
 * 该字段时，前端按 children 递归计算，避免页面出现 undefined 人。
 */
function getAggregateUserCount(unit: OrganizationUnit): number {
  if (typeof unit.user_count === "number" && Number.isFinite(unit.user_count)) {
    return unit.user_count;
  }
  return unit.direct_user_count + unit.children.reduce((total, child) => total + getAggregateUserCount(child), 0);
}

/** 递归渲染可展开部门树，并为每个节点保留直属人数、编辑和删除入口。 */
function TreeNode({ unit, onEdit, onDelete, busy }: { unit: OrganizationUnit; onEdit: (unit: OrganizationUnit) => void; onDelete: (unit: OrganizationUnit) => void; busy: boolean }) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = unit.children.length > 0;
  const memberCount = getAggregateUserCount(unit);
  const deleteBlockedReason = unit.direct_user_count > 0
    ? "部门内有用户，不能删除"
    : hasChildren
      ? "部门存在下级部门，不能删除"
      : null;

  return (
    <li role="treeitem" aria-expanded={hasChildren ? expanded : undefined}>
      <Collapsible open={expanded} onOpenChange={setExpanded} disabled={!hasChildren}>
        <div className="group flex min-h-14 items-center gap-2 rounded-lg px-2 transition-colors hover:bg-[#f4f6f8]">
          {hasChildren ? (
            <CollapsibleTrigger asChild>
              <Button type="button" variant="ghost" size="icon" className="shrink-0 text-[#919eab] hover:bg-white" aria-label={expanded ? `收起 ${unit.name}` : `展开 ${unit.name}`}>
                <ChevronRight className={`size-4 transition-transform ${expanded ? "rotate-90" : ""}`} />
              </Button>
            </CollapsibleTrigger>
          ) : <span className="size-9 shrink-0" />}
          <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-emerald-50 text-emerald-600"><Building2 className="size-4" /></span>
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-[#1c252e]">{unit.name}</span>
          <span className="flex shrink-0 items-center gap-1.5 text-xs text-[#637381]" aria-label={`${memberCount} 名成员（含子部门）`}><Users className="size-4" />{memberCount} 人</span>
          <div className="flex shrink-0 gap-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            <Button size="icon" variant="ghost" className="shrink-0" onClick={() => onEdit(unit)} aria-label={`编辑 ${unit.name}`}><Pencil /></Button>
            {deleteBlockedReason ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex shrink-0 cursor-not-allowed" tabIndex={0} aria-label={deleteBlockedReason}>
                    <Button size="icon" variant="ghost" className="text-[#919eab]" disabled aria-label={`不能删除 ${unit.name}`}><Trash2 /></Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>{deleteBlockedReason}</TooltipContent>
              </Tooltip>
            ) : (
              <Button size="icon" variant="ghost" className="shrink-0 text-red-600 hover:bg-red-50 hover:text-red-700" onClick={() => onDelete(unit)} disabled={busy} aria-label={`删除 ${unit.name}`}><Trash2 /></Button>
            )}
          </div>
        </div>
        <CollapsibleContent>
          {hasChildren && (
            <ul role="group" className="ml-6 border-l border-[#dfe3e8] pl-3">
              {unit.children.map((child) => <TreeNode key={child.id} unit={child} onEdit={onEdit} onDelete={onDelete} busy={busy} />)}
            </ul>
          )}
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}
