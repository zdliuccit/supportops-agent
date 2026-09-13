import { useEffect, useState } from "react";
import { Bot, ExternalLink, Settings2 } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { Button, buttonVariants } from "@/components/ui/button";
import { ListToolbar } from "@/components/ListToolbar";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { PageHeader } from "@/components/PageHeader";
import { listAgents } from "@/api";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import type { Agent } from "@/types";

export function AgentCatalogPage() {
  const navigate = useNavigate();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await delayRequest(() => withRefreshedToken((token) => listAgents(token, { pageSize: 100 })));
      setAgents(result.value.items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取 Agent 目录失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), []);

  return (
    <>
      <PageHeader title="选择一个 Agent" description="每个 Agent 都有独立的提示词、模型、工具、权限和版本历史。" />
      {loading ? (
        <div className="relative mt-8 min-h-[360px]"><ListLoadingOverlay label="正在加载 Agent 列表…" /></div>
      ) : error !== null ? (
        <div className="mt-8 rounded-2xl border bg-white p-6 text-sm text-destructive">
          {error}
          <Button variant="outline" className="ml-4" onClick={() => void load()}>
            重试
          </Button>
        </div>
      ) : agents.length === 0 ? (
        <section className="mt-10 rounded-3xl border border-dashed bg-white p-12 text-center">
          <Bot className="mx-auto size-9 text-muted-foreground" />
          <h2 className="mt-4 font-medium">当前没有可用 Agent</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            管理员需要先配置模型，再创建、发布并授权 Agent。
          </p>
          <Link className={buttonVariants()} to="/agent-management/agents">
            <Settings2 />进入 Agent 管理
          </Link>
        </section>
      ) : (
        <div className=""><ListToolbar title="Agent 列表" onRefresh={() => void load()} loading={loading} className="px-0" /><div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {agents.map((agent) => (
            <article
              key={agent.id}
              role="link"
              tabIndex={0}
              aria-label={`进入${agent.name}嵌入式聊天`}
              onClick={() => navigate(`/agents/${agent.id}/chat`)}
              onKeyDown={(event) => {
                if (event.target !== event.currentTarget) return;
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  navigate(`/agents/${agent.id}/chat`);
                }
              }}
              className="group flex min-h-[232px] cursor-pointer flex-col rounded-3xl border border-[#e6e8eb] bg-white p-6 shadow-[0_8px_24px_rgba(28,37,46,0.04)] transition-all duration-200 hover:-translate-y-1 hover:border-emerald-200 hover:shadow-[0_16px_36px_rgba(28,37,46,0.1)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/30"
            >
              <div className="flex items-start justify-between gap-4">
                {agent.logo_url ? (
                  <img alt="" className="size-12 rounded-2xl object-cover shadow-sm" src={agent.logo_url} />
                ) : (
                  <div className="brand-mark grid size-12 place-items-center rounded-2xl shadow-sm">
                    <Bot className="size-5" />
                  </div>
                )}
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700">
                  <span className="size-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
                  可用
                </span>
              </div>
              <div className="mt-5 min-w-0">
                <h2 className="truncate text-lg font-semibold tracking-tight text-[#1c252e]">{agent.name}</h2>
                <p className="mt-2 line-clamp-2 min-h-12 text-sm leading-6 text-muted-foreground">
                  {agent.description || "企业智能技术支持 Agent"}
                </p>
              </div>
              <div className="mt-auto pt-6">
                <div className="flex items-center justify-end border-t border-[#eef1f4] pt-4">
                  <Link
                    to={`/agents/${agent.id}/chat/standalone`}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(event) => event.stopPropagation()}
                    className={buttonVariants({
                      variant: "outline",
                      className: "h-9 gap-1.5 rounded-lg border-[#dfe3e8] px-3 text-sm hover:border-emerald-200 hover:bg-emerald-50 hover:text-emerald-700",
                    })}
                  >
                    新窗口打开
                    <ExternalLink className="size-3.5" />
                  </Link>
                </div>
              </div>
            </article>
          ))}
        </div></div>
      )}
    </>
  );
}
