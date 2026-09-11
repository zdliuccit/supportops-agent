import { useEffect, useState, type ComponentType } from "react";
import { Bot, Building2, ChevronDown, GitBranch, MessageSquare, Network, Users, type LucideProps } from "lucide-react";
import { NavLink, useLocation } from "react-router-dom";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { demoMultiLevelMenu } from "./demoNavigationItems";

type NavIcon = ComponentType<LucideProps>;

/** 菜单最多展示三级：根菜单、子菜单和末级菜单。 */
const MAX_NAV_DEPTH = 2;

/** 侧栏菜单节点；当前产品最多支持三级，避免导航层级过深影响可读性。 */
export type SidebarNavItem = {
  label: string;
  to?: string;
  icon?: NavIcon;
  children?: SidebarNavItem[];
  adminOnly?: boolean;
};

function isItemActive(item: SidebarNavItem, pathname: string, depth = 0): boolean {
  if (item.to && (pathname === item.to || pathname.startsWith(`${item.to}/`))) return true;
  if (depth >= MAX_NAV_DEPTH) return false;
  return item.children?.some((child) => isItemActive(child, pathname, depth + 1)) ?? false;
}

const navigationItems: SidebarNavItem[] = [
  { label: "工作台", to: "/agents", icon: MessageSquare },
  {
    label: "智能体管理",
    icon: Bot,
    adminOnly: true,
    children: [
      { label: "Agent 管理", to: "/agent-management/agents", icon: Bot },
      { label: "模型管理", to: "/agent-management/models", icon: Network },
    ],
  },
  {
    label: "企业管理",
    icon: Building2,
    adminOnly: true,
    children: [
      { label: "公司信息", to: "/enterprise/company", icon: Building2 },
      { label: "部门管理", to: "/enterprise/departments", icon: GitBranch },
      { label: "用户管理", to: "/enterprise/users", icon: Users },
    ],
  },
  ...demoMultiLevelMenu,
];

function NavigationItem({ item, depth, onNavigate }: { item: SidebarNavItem; depth: number; onNavigate: () => void }) {
  const { pathname } = useLocation();
  const active = isItemActive(item, pathname);
  const [open, setOpen] = useState(active);
  const children = item.children ?? [];
  const hasChildren = depth < MAX_NAV_DEPTH && children.length > 0;

  useEffect(() => {
    // 路由切换时只保留当前节点及其父级展开，其他分组自动收起。
    setOpen(active);
  }, [active, pathname]);

  const Icon = item.icon;
  const itemClass = cn(
    "group flex w-full items-center gap-3 rounded-lg text-left font-medium text-[#637381] transition-colors duration-150 hover:bg-[#f4f6f8] hover:text-[#1c252e] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#9edccc]",
    depth === 0 ? "sidebar-navigation-root-item h-11 px-3" : "sidebar-navigation-child-item relative h-9 px-3",
    // 一级菜单和展开三级菜单的二级菜单都使用同一浅色展开背景。
    open && !active && hasChildren && "bg-[#f4f6f8] text-[#1c252e]",
    active && depth === 0 && "bg-[#e8f7f3] text-[#00a76f] hover:bg-[#d5f0e8] hover:text-[#008f63]",
    active && depth > 0 && "bg-[#f4f6f8] text-[#1c252e] hover:bg-[#eef1f4] hover:text-[#1c252e]",
  );

  if (hasChildren) {
    return <Collapsible open={open} onOpenChange={setOpen} className="w-full">
      <CollapsibleTrigger className={itemClass} aria-expanded={open}>
        {Icon && depth === 0 && <Icon className="size-5 shrink-0" aria-hidden="true" />}
        <span className="min-w-0 flex-1 truncate">{item.label}</span>
        <ChevronDown className={cn("size-4 shrink-0 transition-transform duration-200", open && "rotate-180")} aria-hidden="true" />
      </CollapsibleTrigger>
      <CollapsibleContent className="sidebar-nav-content">
        <div className="sidebar-nav-children relative ml-5 space-y-1 pl-3 pt-1">
          {children.map((child) => <NavigationItem key={`${child.label}-${child.to ?? "group"}`} item={child} depth={depth + 1} onNavigate={onNavigate} />)}
        </div>
      </CollapsibleContent>
    </Collapsible>;
  }

  return <NavLink to={item.to ?? "#"} end={Boolean(item.to)} onClick={onNavigate} className={itemClass}>
    {Icon && depth === 0 && <Icon className="size-5 shrink-0" aria-hidden="true" />}
    <span className="min-w-0 flex-1 truncate">{item.label}</span>
  </NavLink>;
}

/** 主布局侧栏导航，统一处理权限过滤、层级线和展开收起过渡。 */
export function SidebarNavigation({ isAdmin, onNavigate }: { isAdmin: boolean; onNavigate: () => void }) {
  return <nav className="flex-1 overflow-y-auto px-4 pb-5" aria-label="主导航">
    <div className="space-y-2">
      {navigationItems.filter((item) => !item.adminOnly || isAdmin).map((item) => <NavigationItem key={item.label} item={item} depth={0} onNavigate={onNavigate} />)}
    </div>
  </nav>;
}
