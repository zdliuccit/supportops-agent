import { useState, type ReactNode } from "react";
import { Bot, Building2, ChevronDown, LogOut, Menu, MessageSquare, Network, Users, X } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { BrandLogo } from "@/components/BrandLogo";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { clearAccessToken } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { useIdentity } from "@/components/IdentityContext";

const groups = [
  { label: "工作台", links: [{ to: "/agents", label: "Agent 工作台", icon: MessageSquare }] },
  { label: "智能体管理", links: [{ to: "/admin/agents", label: "Agent 管理", icon: Bot }, { to: "/admin/models", label: "模型管理", icon: Network }] },
  { label: "企业管理", links: [{ to: "/admin/users", label: "用户管理", icon: Users }, { to: "/admin/organization", label: "组织架构", icon: Building2 }] },
];

function breadcrumbs(pathname: string): string[] {
  if (pathname === "/agents") return ["工作台", "Agent 工作台"];
  if (pathname.startsWith("/admin/models")) return ["智能体管理", "模型管理"];
  if (pathname.startsWith("/admin/agents")) return ["智能体管理", "Agent 管理"];
  if (pathname.startsWith("/admin/users")) return ["企业管理", "用户管理"];
  if (pathname.startsWith("/admin/organization")) return ["企业管理", "组织架构"];
  return ["工作台"];
}

function Navigation({ onNavigate }: { onNavigate: () => void }) {
  const identity = useIdentity();
  const isAdmin = identity?.roles.includes("platform_admin") ?? false;
  return (
    <>
      <div className="flex h-20 items-center px-6">
        <NavLink to="/agents" aria-label="SupportOps 工作台" onClick={onNavigate}><BrandLogo /></NavLink>
      </div>
      <nav className="flex-1 overflow-y-auto px-4 pb-5" aria-label="主导航">
        {groups.map((group) => {
          const visible = group.links.filter((link) => isAdmin || !link.to.startsWith("/admin"));
          if (!visible.length) return null;
          return (
            <div key={group.label} className="mb-7">
              <div className="px-3 pb-2 text-[11px] font-bold uppercase tracking-[.08em] text-[#919eab]">{group.label}</div>
              <div className="space-y-1">
                {visible.map(({ to, label, icon: Icon }) => (
                  <NavLink
                    key={to}
                    to={to}
                    onClick={onNavigate}
                    className={({ isActive }) => cn(
                      "flex h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium text-[#637381] transition-colors hover:bg-[#f4f6f8] hover:text-[#1c252e]",
                      isActive && "bg-[#e8f7f3] text-[#00a76f] hover:bg-[#d5f0e8] hover:text-[#008f63]",
                    )}
                  >
                    <Icon className="size-5" />
                    <span>{label}</span>
                  </NavLink>
                ))}
              </div>
            </div>
          );
        })}
      </nav>
    </>
  );
}

function UserMenu({ onLogout }: { onLogout: () => void }) {
  const identity = useIdentity();
  const displayName = identity?.display_name ?? "企业成员";
  const isAdmin = identity?.roles.includes("platform_admin") ?? false;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="group flex items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-[#f4f6f8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-200" aria-label="打开用户菜单">
          <span className="grid size-9 place-items-center rounded-full bg-[#e8f7f3] text-sm font-semibold text-[#00a76f]">{displayName.slice(0, 1)}</span>
          <span className="hidden min-w-0 sm:block">
            <span className="block max-w-36 truncate text-xs font-semibold text-[#1c252e]">{displayName}</span>
            <span className="block max-w-36 truncate text-[10px] text-[#919eab]">{identity?.job_title || "企业成员"}</span>
          </span>
          <ChevronDown className="size-4 text-[#919eab] transition-transform group-data-[state=open]:rotate-180" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64 rounded-xl border-[#eef1f4] bg-white p-2 shadow-[0_12px_36px_rgba(28,37,46,.12)]">
        <div className="flex items-center gap-3 rounded-lg bg-[#f7f9fb] px-3 py-3">
          <span className="grid size-10 place-items-center rounded-full bg-[#d5f0e8] font-semibold text-[#008f63]">{displayName.slice(0, 1)}</span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-[#1c252e]">{displayName}</div>
            <div className="truncate text-xs text-[#919eab]">{identity?.email}</div>
          </div>
        </div>
        <div className="px-3 py-2 text-[11px] text-[#919eab]">{isAdmin ? "平台管理员" : "企业成员"}</div>
        <DropdownMenuItem onSelect={onLogout} className="rounded-lg text-red-600 focus:bg-red-50 focus:text-red-700"><LogOut />退出登录</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Breadcrumbs({ pathname }: { pathname: string }) {
  const items = breadcrumbs(pathname);
  return <nav aria-label="面包屑" className="flex min-w-0 items-center gap-2 text-sm text-[#919eab]">{items.map((item, index) => <span key={`${item}-${index}`} className={cn("truncate", index === items.length - 1 && "font-medium text-[#1c252e]")}>{index > 0 && <span className="mr-2 text-[#c5ccd3]">/</span>}{item}</span>)}</nav>;
}

export function MainLayout({ children }: { children?: ReactNode }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = useState(false);

  function logout() {
    clearAccessToken();
    navigate("/login", { replace: true });
  }

  const closeMobile = () => setMobileOpen(false);

  return (
    <div className="min-h-svh bg-white text-[#1c252e]">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[280px] flex-col border-r border-[#dfe3e8] bg-white lg:flex"><Navigation onNavigate={closeMobile} /></aside>
      {mobileOpen && <div className="fixed inset-0 z-50 lg:hidden"><button className="absolute inset-0 bg-slate-950/35" onClick={closeMobile} aria-label="关闭导航" /><aside className="relative flex h-full w-[280px] flex-col bg-white shadow-2xl"><Navigation onNavigate={closeMobile} /><button className="absolute right-3 top-5 grid size-10 place-items-center rounded-xl hover:bg-slate-100" onClick={closeMobile} aria-label="关闭导航"><X /></button></aside></div>}
      <div className="lg:pl-[280px]">
        <header className="fixed inset-x-0 top-0 z-20 flex h-20 items-center justify-between bg-white px-5 shadow-[0_2px_14px_rgba(28,37,46,.06)] lg:left-[280px] lg:px-10">
          <button className="grid size-10 place-items-center rounded-xl bg-white shadow-sm lg:hidden" onClick={() => setMobileOpen(true)} aria-label="打开导航"><Menu /></button>
          <Breadcrumbs pathname={location.pathname} />
          <UserMenu onLogout={logout} />
        </header>
        <main className="mx-auto max-w-[1440px] px-5 pb-12 pt-28 lg:px-10">{children ?? <Outlet />}</main>
      </div>
    </div>
  );
}
