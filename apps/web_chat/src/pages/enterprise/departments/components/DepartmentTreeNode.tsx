import { useState } from "react";
import { Building2, ChevronRight, Pencil, Trash2, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { OrganizationUnit } from "@/types";

function aggregateUserCount(unit: OrganizationUnit): number {
  if (typeof unit.user_count === "number" && Number.isFinite(unit.user_count)) return unit.user_count;
  return unit.direct_user_count + unit.children.reduce((total, child) => total + aggregateUserCount(child), 0);
}

/** 递归渲染部门树节点及其页面专属操作。 */
export function DepartmentTreeNode({ unit, onEdit, onDelete, busy }: { unit: OrganizationUnit; onEdit: (unit: OrganizationUnit) => void; onDelete: (unit: OrganizationUnit) => void; busy: boolean }) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = unit.children.length > 0;
  const memberCount = aggregateUserCount(unit);
  const deleteBlockedReason = unit.direct_user_count > 0 ? "部门内有用户，不能删除" : hasChildren ? "部门存在下级部门，不能删除" : null;
  return <li role="treeitem" aria-expanded={hasChildren ? expanded : undefined}><Collapsible open={expanded} onOpenChange={setExpanded} disabled={!hasChildren}><div className="group flex min-h-14 items-center gap-2 rounded-lg px-2 transition-colors hover:bg-[#f4f6f8]">{hasChildren ? <CollapsibleTrigger asChild><Button type="button" variant="ghost" size="icon" className="shrink-0 text-[#919eab] hover:bg-white" aria-label={expanded ? `收起 ${unit.name}` : `展开 ${unit.name}`}><ChevronRight className={`size-4 transition-transform ${expanded ? "rotate-90" : ""}`} /></Button></CollapsibleTrigger> : <span className="size-9 shrink-0" />}<span className="grid size-9 shrink-0 place-items-center rounded-lg bg-emerald-50 text-emerald-600"><Building2 className="size-4" /></span><span className="min-w-0 flex-1 truncate text-sm font-medium text-[#1c252e]">{unit.name}</span><span className="flex shrink-0 items-center gap-1.5 text-xs text-[#637381]" aria-label={`${memberCount} 名成员（含子部门）`}><Users className="size-4" />{memberCount} 人</span><div className="flex shrink-0 gap-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100"><Button size="icon" variant="ghost" className="shrink-0" onClick={() => onEdit(unit)} aria-label={`编辑 ${unit.name}`}><Pencil /></Button>{deleteBlockedReason ? <Tooltip><TooltipTrigger asChild><span className="inline-flex shrink-0 cursor-not-allowed" tabIndex={0} aria-label={deleteBlockedReason}><Button size="icon" variant="ghost" className="text-[#919eab]" disabled aria-label={`不能删除 ${unit.name}`}><Trash2 /></Button></span></TooltipTrigger><TooltipContent>{deleteBlockedReason}</TooltipContent></Tooltip> : <Button size="icon" variant="ghost" className="shrink-0 text-red-600 hover:bg-red-50 hover:text-red-700" onClick={() => onDelete(unit)} disabled={busy} aria-label={`删除 ${unit.name}`}><Trash2 /></Button>}</div></div><CollapsibleContent>{hasChildren && <ul role="group" className="ml-6 border-l border-[#dfe3e8] pl-3">{unit.children.map((child) => <DepartmentTreeNode key={child.id} unit={child} onEdit={onEdit} onDelete={onDelete} busy={busy} />)}</ul>}</CollapsibleContent></Collapsible></li>;
}
