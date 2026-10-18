import { Fragment, Suspense, useEffect, useState, type ReactNode } from "react";
import { Building2, ChevronDown, LogOut, Menu, ShieldCheck } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { BrandLogo } from "@/components/BrandLogo";
import { GlobalLoading } from "@/components/GlobalLoading";
import { AppDrawer } from "@/components/AppDrawer";
import { Breadcrumb, BreadcrumbItem, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { logoutCurrentSession } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import { SidebarNavigation } from "@/components/navigation/SidebarNavigation";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import {
  clearOrganizationUnits,
  refreshOrganizationUnits,
  selectDepartmentNamePathById,
  selectOrganizationUnitsStatus,
} from "@/store/organizationUnitsSlice";
import { useIdentity } from "@/components/IdentityContext";
import { cn } from "@/lib/utils";

function breadcrumbs(pathname: string): string[] {
  if (pathname === "/knowledge/ingestion-jobs") return ["知识管理", "本人导入任务"];
  if (pathname === "/agents") return ["工作台", "Agent 工作台"];
  if (pathname === "/account/sessions") return ["个人账户", "登录会话"];
  if (pathname === "/account/data-requests") return ["个人账户", "本人数据申请"];
  if (pathname.startsWith("/agents/") && pathname.includes("/chat")) return ["工作台", "Agent 对话"];
  if (pathname === "/analytics/dashboard") return ["Analytics", "Dashboard"];
  if (pathname === "/analytics/rankings") return ["Analytics", "Agent 调用排行"];
  if (pathname === "/analytics/agents") return ["Analytics", "Agent 状态"];
  if (pathname.startsWith("/analytics/agents/")) return ["Analytics", "Agent 运行分析"];
  if (pathname === "/analytics/runs") return ["Analytics", "调用记录"];
  if (pathname === "/analytics/errors") return ["Analytics", "错误分析"];
  if (pathname.startsWith("/agent-management/models")) return ["智能体管理", "模型管理"];
  if (pathname.startsWith("/agent-management/tools")) return ["智能体管理", "工具目录"];
  if (pathname.startsWith("/agent-management/agents")) return ["智能体管理", "Agent 管理"];
  if (pathname.startsWith("/enterprise/users")) return ["企业管理", "用户管理"];
  if (pathname.startsWith("/enterprise/company")) return ["企业管理", "公司信息"];
  if (pathname.startsWith("/enterprise/departments")) return ["企业管理", "部门管理"];
  return ["工作台"];
}

function UserMenu({ onLogout, onSessions, onDataRequests, busy }: { onLogout: () => void; onSessions: () => void; onDataRequests: () => void; busy: boolean }) {
  const identity = useIdentity();
  const displayName = identity?.display_name ?? "企业成员";
  const isAdmin = identity?.roles.includes("platform_admin") ?? false;
  const departmentPath = useAppSelector((state) =>
    selectDepartmentNamePathById(state, identity?.organization_unit_id),
  );
  const departmentLabel = departmentPath.join(" / ") || "未分配部门";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="group h-auto gap-2 px-2 py-1.5 text-left font-normal" aria-label="打开用户菜单">
          <span className="grid size-9 place-items-center rounded-full bg-[#e8f7f3] text-sm font-semibold text-[#00a76f]">{displayName.slice(0, 1)}</span>
          <span className="hidden min-w-0 sm:block">
            <span className="block max-w-36 truncate text-xs font-semibold text-[#1c252e]">{displayName}</span>
            <span className="block max-w-36 truncate text-[10px] text-[#919eab]">{identity?.job_title || "企业成员"}</span>
          </span>
          <ChevronDown className="size-4 text-[#919eab] transition-transform group-data-[state=open]:rotate-180" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64 rounded-xl border-[#eef1f4] bg-white p-2 shadow-[0_12px_36px_rgba(28,37,46,.12)]">
        <div className="flex items-center gap-3 rounded-lg bg-[#f7f9fb] px-3 py-3">
          <span className="grid size-10 place-items-center rounded-full bg-[#d5f0e8] font-semibold text-[#008f63]">{displayName.slice(0, 1)}</span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-[#1c252e]">{displayName}</div>
            <div className="truncate text-xs text-[#919eab]">{identity?.email}</div>
          </div>
        </div>
        <div className="space-y-1.5 px-3 py-2 text-[11px] text-[#919eab]">
          <div>{isAdmin ? "平台管理员" : "企业成员"}</div>
          <div className="flex items-start gap-1.5"><Building2 className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /><span className="break-words">{departmentLabel}</span></div>
        </div>
        <DropdownMenuItem onSelect={onSessions}><ShieldCheck aria-hidden="true" />登录会话</DropdownMenuItem>
        <DropdownMenuItem onSelect={onDataRequests}><ShieldCheck aria-hidden="true" />本人数据申请</DropdownMenuItem>
        <DropdownMenuItem disabled={busy} onSelect={onLogout} className="rounded-lg text-red-600 focus:bg-red-50 focus:text-red-700"><LogOut aria-hidden="true" />{busy ? "退出中…" : "退出登录"}</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Breadcrumbs({ pathname }: { pathname: string }) {
  const items = breadcrumbs(pathname);
  return (
    <Breadcrumb className="min-w-0">
      <BreadcrumbList className="flex-nowrap text-[#919eab]">
        {items.map((item, index) => (
          <Fragment key={`${item}-${index}`}>
            {index > 0 && <BreadcrumbSeparator className="text-[#c5ccd3]">/</BreadcrumbSeparator>}
            <BreadcrumbItem className="min-w-0">
              {index === items.length - 1 ? <BreadcrumbPage className="truncate font-medium text-[#1c252e]">{item}</BreadcrumbPage> : <span className="truncate">{item}</span>}
            </BreadcrumbItem>
          </Fragment>
        ))}
      </BreadcrumbList>
    </Breadcrumb>
  );
}

export function MainLayout({ children }: { children?: ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const identity = useIdentity();
  const dispatch = useAppDispatch();
  const departmentStatus = useAppSelector(selectOrganizationUnitsStatus);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const isChatRoute = location.pathname.startsWith("/agents/") && location.pathname.includes("/chat");

  useEffect(() => {
    if (identity?.roles.includes("platform_admin") && departmentStatus === "idle") {
      void dispatch(refreshOrganizationUnits());
    }
  }, [departmentStatus, dispatch, identity?.roles]);

  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    const confirmed = await logoutCurrentSession();
    dispatch(clearOrganizationUnits());
    navigate("/login", { replace: true });
    if (confirmed) notify.success("已退出登录，服务端会话已失效。");
    else notify.warning("已退出本页，服务端撤销未确认；会话到期前仍可能有效。");
  }

  const closeMobile = () => setMobileOpen(false);

  return (
    <div className="min-h-svh bg-white text-[#1c252e]">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[280px] flex-col border-r border-[#dfe3e8] bg-white lg:flex">
        <div className="flex h-20 items-center px-6"><NavLink to="/agents" aria-label="SupportOps 工作台" onClick={closeMobile}><BrandLogo /></NavLink></div>
        <SidebarNavigation isAdmin={identity?.roles.includes("platform_admin") ?? false} onNavigate={closeMobile} />
      </aside>
      <AppDrawer
        open={mobileOpen}
        onOpenChange={setMobileOpen}
        title="主导航"
        side="left"
        showHeader={false}
        showFooter={false}
        className="w-[280px] sm:max-w-[280px]"
        contentClassName="p-0"
      >
          <div className="flex h-20 items-center px-6"><NavLink to="/agents" aria-label="SupportOps 工作台" onClick={closeMobile}><BrandLogo /></NavLink></div>
          <SidebarNavigation isAdmin={identity?.roles.includes("platform_admin") ?? false} onNavigate={closeMobile} />
      </AppDrawer>
      <div className="lg:pl-[280px]">
        <header className="fixed inset-x-0 top-0 z-50 flex h-[72px] items-center justify-between bg-white px-5 shadow-[0_2px_14px_rgba(28,37,46,.06)] lg:left-[280px] lg:px-6">
          <Button variant="ghost" size="icon" className="bg-white shadow-sm lg:hidden" onClick={() => setMobileOpen(true)} aria-label="打开导航"><Menu /></Button>
          <Breadcrumbs pathname={location.pathname} />
          <UserMenu onLogout={() => void logout()} onSessions={() => navigate("/account/sessions")} onDataRequests={() => navigate("/account/data-requests")} busy={loggingOut} />
        </header>
        <div className={cn("pt-[72px]", isChatRoute ? "h-svh" : "min-h-svh")}>
          <main
            className={cn(
              "w-full px-5 pb-12 pt-8 lg:px-6",
              isChatRoute && "h-[calc(100svh-72px)] overflow-hidden px-0 pb-0 pt-0 lg:px-0",
            )}
          >
            <Suspense fallback={<GlobalLoading fullScreen={false} label="正在加载页面…" />}>
              {children ?? <Outlet />}
            </Suspense>
          </main>
        </div>
      </div>
    </div>
  );
}
