import { useEffect, useState, type FormEvent } from "react";
import { CircleAlert, LoaderCircle, Plus } from "lucide-react";

import {
  createOrganizationUnit,
  deleteOrganizationUnit,
  updateOrganizationUnit,
} from "@/api";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { TooltipProvider } from "@/components/ui/tooltip";
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

import { CreateDepartmentDialog } from "./components/CreateDepartmentDialog";
import { DeleteDepartmentDialog } from "./components/DeleteDepartmentDialog";
import { DepartmentTreeNode } from "./components/DepartmentTreeNode";
import { EditDepartmentDialog } from "./components/EditDepartmentDialog";

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
                <DepartmentTreeNode key={unit.id} unit={unit} onEdit={showEdit} onDelete={setDeleteTarget} busy={busy} />
              ))}
            </ul>
          </TooltipProvider>
        </section>
      )}

      <CreateDepartmentDialog open={createOpen} form={createForm} error={formError} busy={busy} onOpenChange={(open) => { if (open) setCreateOpen(true); else closeCreateDialog(); }} onSubmit={addUnit} onChange={(value) => { setCreateForm(value); setFormError(null); }} onValidateName={(value) => setFormError(value.trim() ? null : "请输入部门名称")} onCancel={closeCreateDialog} />
      <EditDepartmentDialog target={editTarget} form={editForm} error={formError} busy={busy} onOpenChange={(open) => { if (!open) closeEditDialog(); }} onSubmit={saveUnit} onChange={(value) => { setEditForm(value); setFormError(null); }} onValidateName={(value) => setFormError(value.trim() ? null : "请输入部门名称")} onCancel={closeEditDialog} />
      <DeleteDepartmentDialog target={deleteTarget} busy={busy} onOpenChange={(open) => { if (!open && !busy) setDeleteTarget(null); }} onConfirm={() => void removeUnit()} />
    </>
  );
}
