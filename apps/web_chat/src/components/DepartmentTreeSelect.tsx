import { useEffect, useId, useMemo } from "react";
import { Building2 } from "lucide-react";

import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  getDepartmentNamePath,
  refreshOrganizationUnits,
  selectOrganizationUnits,
  selectOrganizationUnitsStatus,
} from "@/store/organizationUnitsSlice";
import type { OrganizationUnit } from "@/types";

const EMPTY_VALUE = "__none__";
const NO_EXCLUDED_IDS = new Set<string>();

interface DepartmentOption {
  /** 作为表单值提交的末级部门 UUID。 */
  id: string;
  /** 当前部门名称。 */
  name: string;
  /** 从顶级部门开始计算的层级，首层为 1。 */
  depth: number;
  /** 从顶级部门到当前部门的完整名称路径。 */
  path: string[];
}

interface DepartmentTreeSelectProps {
  /** 当前选中的末级部门 UUID；空值表示未选择部门。 */
  value: string | null;
  /** 选择完成后仅返回末级部门 UUID，不提交展示路径。 */
  onChange: (value: string | null) => void;
  /** 表单字段标题。 */
  label?: string;
  /** 未选择时显示的提示文案。 */
  placeholder?: string;
  /** 空值选项文案；传入 null 时不提供空值选项。 */
  emptyLabel?: string | null;
  /** 下拉列表最多展示的部门层级，默认四级。 */
  maxDepth?: number;
  /** 需要从可选树中排除的部门；排除节点时会同时排除其后代。 */
  excludedIds?: ReadonlySet<string>;
  /** 是否禁用选择器。 */
  disabled?: boolean;
  /** 选择器外层样式。 */
  className?: string;
  /** 是否显示字段标题；列表筛选栏等紧凑场景可隐藏。 */
  showLabel?: boolean;
}

/** 按先父后子的顺序生成部门选项，同时保留每个节点的完整路径。 */
function flattenDepartmentTree(
  units: OrganizationUnit[],
  maxDepth: number,
  excludedIds: ReadonlySet<string>,
  parentPath: string[] = [],
  depth = 1,
): DepartmentOption[] {
  if (depth > maxDepth) return [];
  return units.flatMap((unit) => {
    if (excludedIds.has(unit.id)) return [];
    const path = [...parentPath, unit.name];
    return [
      { id: unit.id, name: unit.name, depth, path },
      ...flattenDepartmentTree(unit.children, maxDepth, excludedIds, path, depth + 1),
    ];
  });
}

/**
 * 统一树形部门选择器。
 *
 * 下拉列表使用缩进表达树结构，选中后展示完整部门路径，但业务表单只保存末级部门 UUID。
 */
export function DepartmentTreeSelect({
  value,
  onChange,
  label = "部门",
  placeholder = "请选择部门",
  emptyLabel = "未分配部门",
  maxDepth = 4,
  excludedIds = NO_EXCLUDED_IDS,
  disabled = false,
  className,
  showLabel = true,
}: DepartmentTreeSelectProps) {
  const triggerId = useId();
  const dispatch = useAppDispatch();
  const units = useAppSelector(selectOrganizationUnits);
  const status = useAppSelector(selectOrganizationUnitsStatus);
  const normalizedMaxDepth = Math.max(1, maxDepth);
  const visibleOptions = useMemo(
    () => flattenDepartmentTree(units, normalizedMaxDepth, excludedIds),
    [excludedIds, normalizedMaxDepth, units],
  );
  const selectedPath = useMemo(() => getDepartmentNamePath(units, value), [units, value]);

  useEffect(() => {
    if (status === "idle") void dispatch(refreshOrganizationUnits());
  }, [dispatch, status]);

  const emptyText = status === "loading"
    ? "部门数据加载中…"
    : status === "failed"
      ? "部门数据加载失败"
      : emptyLabel ?? placeholder;

  return (
    <div className={className}>
      {showLabel && <Label htmlFor={triggerId}>{label}</Label>}
      <Select
        value={value ?? EMPTY_VALUE}
        onValueChange={(nextValue) => onChange(nextValue === EMPTY_VALUE ? null : nextValue)}
        disabled={disabled || status === "loading"}
      >
        <SelectTrigger id={triggerId} className={cn(showLabel && "mt-2")} aria-label={label || "部门"}>
          <span className={cn("truncate", selectedPath.length === 0 && "text-muted-foreground/50")}>
            {selectedPath.length > 0 ? selectedPath.join(" / ") : value === null ? emptyText : "当前部门不可选"}
          </span>
        </SelectTrigger>
        <SelectContent className="bg-white">
          {emptyLabel !== null && <SelectItem value={EMPTY_VALUE}>{emptyLabel}</SelectItem>}
          {visibleOptions.map((option) => (
            <SelectItem key={option.id} value={option.id}>
              <span className="flex min-w-0 items-center gap-2" style={{ paddingLeft: `${(option.depth - 1) * 16}px` }}>
                {option.depth > 1 && <span className="h-px w-3 shrink-0 bg-border" aria-hidden="true" />}
                <Building2 className="size-4 shrink-0 text-emerald-600" aria-hidden="true" />
                <span className="truncate">{option.name}</span>
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
