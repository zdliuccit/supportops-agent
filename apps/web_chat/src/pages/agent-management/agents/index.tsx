import { Bot, ChartNoAxesCombined, ClipboardList, History, MoreHorizontal, Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { AppTable, type AppTableColumn } from "@/components/AppTable";

import { ListToolbar } from "@/components/ListToolbar";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { StatusBadge } from "@/components/StatusBadge";

import { Button, buttonVariants } from "@/components/ui/button";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";

import type { AdminAgent } from "@/types";

import { useAgentManagement } from "./hooks/useAgentManagement";
import { AgentLoadError } from "./components/AgentLoadError";
import { AgentManagementDialogs } from "./components/AgentManagementDialogs";

export function AgentManagementPage() {
const state = useAgentManagement();
  const {
    agents,
    totalAgents,
    pagination,
    setPagination,
    statusFilter,
    setStatusFilter,
    loading,
    setCreateOpen,
    activeModels,
    load,
    openHistory,
    resetCreateForm,
  } = state;
  const agentColumns: AppTableColumn<AdminAgent>[] = [
    {
      title: "Agent",
      key: "agent",
      width: "30%",
      minWidth: 220,
      render: (_, agent) => (
        <div className="flex min-w-0 items-center gap-3">
          <div className="brand-mark grid size-10 shrink-0 place-items-center overflow-hidden rounded-xl">
            {agent.logo_url ? <img src={agent.logo_url} alt="" className="size-full object-cover" /> : <Bot className="size-4" />}
          </div>
          <div className="min-w-0">
            <div className="truncate font-medium text-foreground">{agent.name}</div>
            <div className="mt-0.5 truncate text-xs text-muted-foreground">{agent.slug}</div>
          </div>
        </div>
      ),
    },
    {
      title: "状态",
      key: "status",
      width: "16%",
      minWidth: 90,
      render: (_, agent) => {
        const statusLabel = agent.status === "active" ? "已启用" : agent.status === "disabled" ? "已停用" : "待发布";
        return <StatusBadge value={agent.status} label={statusLabel} />;
      },
    },
    {
      title: "版本",
      key: "version",
      width: "20%",
      minWidth: 180,
      render: (_, agent) => (
        <div className="text-sm">
          <div>配置 revision r{agent.draft_revision ?? "-"}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">活动版本 {agent.active_version_id?.slice(0, 8) ?? "-"}</div>
        </div>
      ),
    },
    {
      title: "更新时间",
      key: "updated_at",
      width: "20%",
      minWidth: 180,
      render: (_, agent) => <span className="text-sm text-muted-foreground">{new Date(agent.updated_at).toLocaleString("zh-CN", { hour12: false })}</span>,
    },
    {
      title: "操作",
      key: "actions",
      fixed: "right",
      minWidth: 160,
      align: "right",
      width: "28%",
      render: (_, agent) => (
        <div className="flex flex-wrap justify-end gap-2">
          {agent.active_version_id && <Link className={buttonVariants({ variant: "ghost", size: "icon", className: "text-[#00a76f] hover:bg-[#e8f7ef] hover:text-[#008f63]" })} to={`/analytics/agents/${agent.id}`} aria-label={`查看 ${agent.name} 的运行数据`} title="运行数据"><ChartNoAxesCombined className="size-4" /></Link>}
          <Link className={buttonVariants({ variant: "outline", size: "sm" })} to={`/agent-management/agents/${agent.id}`}>配置</Link>
          <HoverCard openDelay={100} closeDelay={150}>
            <HoverCardTrigger asChild>
              <Button size="icon" variant="ghost" aria-label={`查看 ${agent.name} 的历史数据`}><MoreHorizontal /></Button>
            </HoverCardTrigger>
            <HoverCardContent side="bottom" align="end" className="w-40 p-1.5">
              <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition-colors hover:bg-accent" onClick={() => openHistory("versions", agent)}><History className="size-4" />历史版本</button>
              <button type="button" className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm transition-colors hover:bg-accent" onClick={() => openHistory("audit", agent)}><ClipboardList className="size-4" />操作记录</button>
            </HoverCardContent>
          </HoverCard>
        </div>
      ),
    },
  ];

 return <>
<PageHeader title="Agent 管理" description="配置 Agent 基础信息、模型、提示词、工具、权限与版本。" actions={<Button type="button" onClick={() => { resetCreateForm(); setCreateOpen(true); }} disabled={activeModels.length === 0}><Plus />新建 Agent</Button>} />
<AgentLoadError state={state} />
        <section className="">
          <ListToolbar
            filters={(
              <Select value={statusFilter || "__all__"} onValueChange={(value) => {
                setStatusFilter(value === "__all__" ? "" : value as typeof statusFilter);
                setPagination((current) => ({ ...current, current: 1 }));
              }}>
                <SelectTrigger className="w-36" aria-label="按状态筛选 Agent"><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="__all__">全部状态</SelectItem><SelectItem value="draft">待发布</SelectItem><SelectItem value="active">已启用</SelectItem><SelectItem value="disabled">已停用</SelectItem></SelectContent>
              </Select>
            )}
            onRefresh={() => void load()}
            loading={loading}
          />
          <div className="relative min-h-[360px]"><AppTable
            columns={agentColumns}
            dataSource={agents}
            rowKey="id"
            scroll={{ x: 1120 }}
            pagination={{
              current: pagination.current,
              pageSize: pagination.pageSize,
              total: totalAgents,
              onChange: (current, pageSize) => {
                const nextPagination = { current, pageSize };
                setPagination(nextPagination);
                void load(nextPagination);
              },
            }}
            emptyText="还没有 Agent。请先配置并验证模型端点。"
            ariaLabel="Agent 列表"
          />{loading && <ListLoadingOverlay label="正在加载 Agent 列表…" />}</div>
        </section>

<AgentManagementDialogs state={state} />
</>;
}
