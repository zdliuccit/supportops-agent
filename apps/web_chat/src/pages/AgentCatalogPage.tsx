import { useEffect, useState } from "react";
import { ArrowRight, Bot, RefreshCw, Settings2 } from "lucide-react";
import { Link } from "react-router-dom";

import { Button, buttonVariants } from "@/components/ui/button";
import { PageHeader } from "@/components/PageHeader";
import { listAgents } from "@/api";
import { withRefreshedToken } from "@/lib/auth";
import type { Agent } from "@/types";

export function AgentCatalogPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await withRefreshedToken((token) => listAgents(token, { pageSize: 100 }));
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
        <div className="mt-16 flex items-center justify-center text-sm text-muted-foreground">
          <RefreshCw className="mr-2 size-4 animate-spin" />加载可用 Agent…
        </div>
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
          <Link className={buttonVariants({ className: "mt-6" })} to="/admin/agents">
            <Settings2 />进入 Agent 管理
          </Link>
        </section>
      ) : (
        <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {agents.map((agent) => (
            <Link
              key={agent.id}
              to={`/agents/${agent.id}/chat`}
              className="group rounded-3xl border bg-white p-6 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
            >
              {agent.logo_url ? (
                <img src={agent.logo_url} alt="" className="size-12 rounded-2xl object-cover" />
              ) : (
                <div className="brand-mark grid size-12 place-items-center rounded-2xl">
                  <Bot className="size-5" />
                </div>
              )}
              <h2 className="mt-5 font-semibold">{agent.name}</h2>
              <p className="mt-2 line-clamp-3 min-h-15 text-sm leading-5 text-muted-foreground">
                {agent.description || "企业智能技术支持 Agent"}
              </p>
              <span className="mt-5 flex items-center text-sm font-medium">
                开始对话<ArrowRight className="ml-1 size-4 transition group-hover:translate-x-1" />
              </span>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
