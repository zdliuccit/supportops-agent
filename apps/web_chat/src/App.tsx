import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowUp,
  Bot,
  Menu,
  MoreHorizontal,
  PanelLeftClose,
  Pencil,
  Pin,
  PinOff,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  SquarePen,
  Square,
  Trash2,
} from "lucide-react";

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
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/FormField";
import { notify } from "@/lib/notifications";
import { cn } from "@/lib/utils";
import {
  ApiError,
  cancelRun,
  createConversation,
  deleteConversation,
  getAgent,
  getConversation,
  listConversations,
  sendMessage,
  streamRun,
  updateConversation,
} from "./api";
import { withRefreshedToken } from "@/lib/auth";
import type {
  Agent,
  Conversation,
  ConversationSummary,
  Message,
  RunEvent,
  RunStatus,
} from "./types";

type UiStatus =
  | "booting"
  | "idle"
  | "loading-conversation"
  | "sending"
  | "streaming"
  | "error";

function temporaryMessage(role: Message["role"], content: string): Message {
  return { id: crypto.randomUUID(), role, content, created_at: new Date().toISOString() };
}

function summarize(conversation: Conversation): ConversationSummary {
  return {
    id: conversation.id,
    agent_id: conversation.agent_id,
    agent_version_id: conversation.agent_version_id,
    title: conversation.title,
    is_pinned: conversation.is_pinned,
    status: conversation.status,
    created_at: conversation.created_at,
    updated_at: conversation.updated_at,
  };
}

function titleFrom(content: string): string {
  const normalized = content.replaceAll(/\s+/g, " ").trim();
  return normalized.slice(0, 200);
}

function statusLabel(status: RunStatus | null, uiStatus: UiStatus): string {
  if (uiStatus === "booting") return "正在初始化";
  if (uiStatus === "sending") return "正在提交";
  if (uiStatus === "streaming") return "正在思考";
  if (status === "completed") return "已完成";
  if (status === "cancelled") return "已取消";
  if (status === "failed") return "运行失败";
  return "在线";
}

function AgentMark({ agent, className }: { agent: Agent | null; className: string }) {
  if (agent?.logo_url) {
    return <img src={agent.logo_url} alt="" className={`${className} object-cover`} />;
  }
  return (
    <div className={`brand-mark ${className}`}>
      <Bot className="size-4" />
    </div>
  );
}

export default function App() {
  const navigate = useNavigate();
  const { agentId, conversationId } = useParams();
  const routeConversationId = conversationId ?? null;
  const [token, setToken] = useState<string | null>(null);
  const [agent, setAgent] = useState<Agent | null>(null);
  const [agentUsable, setAgentUsable] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [pendingNewMessage, setPendingNewMessage] = useState<Message | null>(null);
  const [draft, setDraft] = useState("");
  const [uiStatus, setUiStatus] = useState<UiStatus>("booting");
  const [runStatus, setRunStatus] = useState<RunStatus | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [lastEventId, setLastEventId] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [renameTarget, setRenameTarget] = useState<ConversationSummary | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameError, setRenameError] = useState<string | undefined>();
  const [deleteTarget, setDeleteTarget] = useState<ConversationSummary | null>(null);
  const [conversationActionId, setConversationActionId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const locallyCreatedConversationIdRef = useRef<string | null>(null);
  const previousRouteConversationIdRef = useRef<string | null>(routeConversationId);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function initialize() {
      setUiStatus("booting");
      setError(null);
      try {
        if (!agentId) throw new Error("缺少 Agent ID");
        const result = await withRefreshedToken(async (activeToken) => {
          const [profile, history] = await Promise.all([
            getAgent(agentId, activeToken).catch((cause) => {
              if (cause instanceof ApiError && cause.status === 404) return null;
              throw cause;
            }),
            listConversations(activeToken, agentId),
          ]);
          return { profile, history };
        });
        if (result.value.profile === null && routeConversationId === null) {
          throw new Error("Agent 已停用、未发布或当前身份没有使用权限");
        }
        if (!cancelled) {
          setToken(result.token);
          setAgent(result.value.profile);
          setAgentUsable(result.value.profile !== null);
          setConversations(result.value.history.items);
          setUiStatus("idle");
        }
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "初始化本地访问身份失败");
          setUiStatus("error");
        }
      }
    }

    void initialize();
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [agentId]);

  useEffect(() => {
    const previousRouteConversationId = previousRouteConversationIdRef.current;
    previousRouteConversationIdRef.current = routeConversationId;
    if (token === null) return;

    if (routeConversationId === null) {
      if (previousRouteConversationId !== null) clearConversationState(false);
      return;
    }

    if (locallyCreatedConversationIdRef.current === routeConversationId) {
      if (conversation?.id === routeConversationId) {
        locallyCreatedConversationIdRef.current = null;
      }
      return;
    }
    if (pendingNewMessage !== null) return;
    if (conversation?.id === routeConversationId) return;

    const conversationId = routeConversationId;
    const activeToken = token;
    let cancelled = false;
    abortRef.current?.abort();
    locallyCreatedConversationIdRef.current = null;
    setConversation(null);
    setUiStatus("loading-conversation");
    setError(null);
    setRunStatus(null);
    setActiveRunId(null);
    setLastEventId(0);

    async function loadRoutedConversation() {
      try {
        const loaded = await getConversation(conversationId, activeToken);
        if (loaded.agent_id !== agentId) throw new Error("会话不属于当前 Agent");
        if (!cancelled) {
          setConversation(loaded);
          setAgent(loaded.agent);
          setSidebarOpen(false);
          setUiStatus("idle");
        }
      } catch (cause) {
        if (!cancelled) {
          clearConversationState(false);
          setError(cause instanceof Error ? cause.message : "读取历史会话失败");
          navigate(
            agent !== null && agentUsable && agentId ? `/agents/${agentId}/chat` : "/agents",
            { replace: true },
          );
        }
      }
    }

    void loadRoutedConversation();
    return () => {
      cancelled = true;
    };
  }, [
    agent,
    agentId,
    agentUsable,
    conversation?.id,
    navigate,
    pendingNewMessage,
    routeConversationId,
    token,
  ]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conversation?.messages, pendingNewMessage, uiStatus]);

  async function refreshHistory(activeToken: string) {
    if (!agentId) return;
    const history = await listConversations(activeToken, agentId);
    setConversations(history.items);
  }

  function handleEvent(event: RunEvent) {
    setLastEventId(event.id);
    if (event.type === "run.started") setRunStatus("running");
    if (event.type === "run.completed") setRunStatus("completed");
    if (event.type === "run.failed") setRunStatus("failed");
    if (event.type === "run.cancelled") setRunStatus("cancelled");
    if (event.type === "text.delta") {
      const delta = typeof event.data.delta === "string" ? event.data.delta : "";
      setConversation((current) => {
        if (current === null) return current;
        const messages = [...current.messages];
        const previous = messages.at(-1);
        if (previous?.role === "assistant" && previous.id.startsWith("stream:")) {
          messages[messages.length - 1] = { ...previous, content: previous.content + delta };
        } else {
          messages.push({ ...temporaryMessage("assistant", delta), id: `stream:${event.id}` });
        }
        return { ...current, messages };
      });
    }
  }

  async function subscribe(
    runId: string,
    conversationId: string,
    activeToken: string,
    after = 0,
  ) {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setUiStatus("streaming");
    setError(null);
    try {
      await streamRun(runId, activeToken, after, controller.signal, handleEvent);
      const refreshed = await getConversation(conversationId, activeToken);
      setConversation((current) => (current?.id === conversationId ? refreshed : current));
      await refreshHistory(activeToken);
      setUiStatus("idle");
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error ? cause.message : "事件流连接中断");
      setUiStatus("error");
    }
  }

  function openConversation(item: ConversationSummary) {
    if (token === null || uiStatus === "sending" || uiStatus === "streaming") return;
    setSidebarOpen(false);
    if (agentId) navigate(`/agents/${agentId}/chat/c/${item.id}`);
  }

  function clearConversationState(focusComposer = true) {
    abortRef.current?.abort();
    setConversation(null);
    setPendingNewMessage(null);
    setDraft("");
    setError(null);
    setRunStatus(null);
    setActiveRunId(null);
    setLastEventId(0);
    setSidebarOpen(false);
    setUiStatus("idle");
    if (focusComposer) requestAnimationFrame(() => composerRef.current?.focus());
  }

  function resetToNewConversation(replace = false) {
    clearConversationState();
    navigate(agentId ? `/agents/${agentId}/chat` : "/agents", { replace });
  }

  function startNewConversation() {
    if (uiStatus === "sending" || uiStatus === "streaming") return;
    if (!agentUsable) {
      navigate("/agents");
      return;
    }
    resetToNewConversation();
  }

  async function togglePinned(item: ConversationSummary) {
    if (token === null) return;
    setConversationActionId(item.id);
    try {
      const updated = await updateConversation(item.id, token, {
        is_pinned: !item.is_pinned,
      });
      setConversation((current) =>
        current?.id === updated.id ? { ...current, ...updated } : current,
      );
      await refreshHistory(token);
      if (item.is_pinned) {
        notify.success("会话已取消置顶。");
      } else {
        notify.success("会话已置顶。");
      }
    } catch (cause) {
      notify.error(cause, "更新置顶状态失败");
    } finally {
      setConversationActionId(null);
    }
  }

  function beginRename(item: ConversationSummary) {
    setRenameTarget(item);
    setRenameValue(item.title || "未命名对话");
    setRenameError(undefined);
  }

  async function renameConversation(event: FormEvent) {
    event.preventDefault();
    const title = renameValue.trim();
    if (!title) {
      setRenameError("请输入会话名称");
      return;
    }
    if (token === null || renameTarget === null) return;
    setConversationActionId(renameTarget.id);
    try {
      const updated = await updateConversation(renameTarget.id, token, { title });
      setConversation((current) =>
        current?.id === updated.id ? { ...current, ...updated } : current,
      );
      await refreshHistory(token);
      setRenameTarget(null);
      setRenameValue("");
      setRenameError(undefined);
      notify.success("会话已重命名。");
    } catch (cause) {
      notify.error(cause, "重命名会话失败");
    } finally {
      setConversationActionId(null);
    }
  }

  async function confirmDeleteConversation() {
    if (token === null || deleteTarget === null) return;
    const targetId = deleteTarget.id;
    setConversationActionId(targetId);
    try {
      await deleteConversation(targetId, token);
      setConversations((current) => current.filter((item) => item.id !== targetId));
      if (conversation?.id === targetId) resetToNewConversation(true);
      setDeleteTarget(null);
      notify.success("会话已删除。");
    } catch (cause) {
      notify.error(cause, "删除会话失败");
    } finally {
      setConversationActionId(null);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (token === null || !content || uiStatus === "sending" || uiStatus === "streaming") return;

    setUiStatus("sending");
    setError(null);
    setDraft("");
    let target = conversation;
    const optimisticUserMessage = temporaryMessage("user", content);
    const isNewConversation = target === null;
    if (isNewConversation) setPendingNewMessage(optimisticUserMessage);
    try {
      if (target === null) {
        if (!agentId) throw new Error("缺少 Agent ID");
        const created = await createConversation(token, agentId, titleFrom(content));
        target = created;
        setConversations((current) => [summarize(created), ...current]);
      }
      if (isNewConversation) locallyCreatedConversationIdRef.current = target.id;
      setConversation({
        ...target,
        messages: [...target.messages, optimisticUserMessage],
      });
      if (isNewConversation) {
        navigate(`/agents/${agentId}/chat/c/${target.id}`, { replace: true });
      }
      setPendingNewMessage(null);
      const accepted = await sendMessage(target.id, content, token, crypto.randomUUID());
      setActiveRunId(accepted.run_id);
      setRunStatus(accepted.status);
      setLastEventId(0);
      await subscribe(accepted.run_id, target.id, token);
    } catch (cause) {
      setPendingNewMessage(null);
      if (target === null) setDraft(content);
      setError(cause instanceof Error ? cause.message : "发送消息失败");
      setUiStatus("error");
      if (target !== null) {
        try {
          setConversation(await getConversation(target.id, token));
          await refreshHistory(token);
        } catch {
          // 保留原始错误，避免恢复请求覆盖真正的失败原因。
        }
      }
    }
  }

  async function stopRun() {
    if (activeRunId === null || token === null) return;
    try {
      await cancelRun(activeRunId, token);
      abortRef.current?.abort();
      setRunStatus("cancelled");
      setUiStatus("idle");
      if (conversation !== null) {
        setConversation(await getConversation(conversation.id, token));
      }
      notify.success("已停止生成。");
    } catch (cause) {
      notify.error(cause, "取消运行失败");
      setUiStatus("error");
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  const isInitializing = uiStatus === "booting";
  const displayedMessages =
    conversation?.messages ?? (pendingNewMessage === null ? [] : [pendingNewMessage]);
  const isRoutedConversationLoading =
    routeConversationId !== null &&
    pendingNewMessage === null &&
    (conversation?.id !== routeConversationId || uiStatus === "loading-conversation");
  const busy =
    isInitializing ||
    isRoutedConversationLoading ||
    uiStatus === "sending" ||
    uiStatus === "streaming";
  const isBootFailure = uiStatus === "error" && token === null;
  const suggestions =
    agent?.suggested_prompts.length ? agent.suggested_prompts : ["请描述你遇到的技术问题"];

  if (isBootFailure) {
    return (
      <main className="grid min-h-svh place-items-center bg-background px-6">
        <div className="w-full max-w-md rounded-3xl border bg-card p-8 text-center shadow-sm">
          <AlertCircle className="mx-auto size-8 text-destructive" />
          <h1 className="mt-4 text-xl font-semibold">无法打开工作台</h1>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{error}</p>
          <Button className="mt-6" onClick={() => window.location.reload()}>
            <RefreshCw />重新尝试
          </Button>
        </div>
      </main>
    );
  }

  return (
    <div className="flex h-svh overflow-hidden bg-background text-foreground">
      {sidebarOpen && (
        <Button
          variant="ghost"
          className="fixed inset-0 z-30 h-auto w-auto rounded-none bg-black/20 p-0 backdrop-blur-[1px] hover:bg-black/20 lg:hidden"
          aria-label="关闭历史会话"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-[260px] flex-col border-r border-sidebar-border bg-sidebar p-3 transition-transform duration-200 lg:static lg:translate-x-0",
          sidebarOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex items-center justify-between px-2 py-2">
          <div className="flex items-center gap-2.5">
            <AgentMark agent={agent} className="size-8 rounded-xl" />
            <div>
              <strong className="block text-sm font-semibold tracking-tight">
                {agent?.name ?? "Agent"}
              </strong>
              <span className="block text-[11px] text-muted-foreground">企业智能支持</span>
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            onClick={() => setSidebarOpen(false)}
            aria-label="收起侧栏"
          >
            <PanelLeftClose />
          </Button>
        </div>

        <Button
          variant="ghost"
          className={cn(
            "new-conversation-button mt-3 -ml-1.5 h-9 w-[248px] justify-start gap-2.5 rounded-[10px] px-2.5 py-1.5 font-normal text-sidebar-foreground shadow-none hover:bg-black/[0.04] hover:text-sidebar-foreground [&_svg]:size-5",
            routeConversationId === null &&
              conversation === null &&
              "bg-black/[0.05] hover:bg-black/[0.05]",
          )}
          onClick={startNewConversation}
          disabled={busy}
          aria-current={
            routeConversationId === null && conversation === null ? "page" : undefined
          }
        >
          <SquarePen />
          新建对话
        </Button>

        <div className="mt-7 px-1 text-xs font-medium text-muted-foreground">最近</div>
        <ScrollArea className="-ml-3 -mr-[13px] mt-2 min-h-0 flex-1">
          <nav
            className={cn(
              "w-0 min-w-full transition-opacity duration-150",
              isInitializing && "pointer-events-none opacity-0",
            )}
            aria-label="历史会话"
            aria-busy={isInitializing}
          >
            {conversations.length === 0 ? (
              <div className="px-2 py-8 text-center text-xs leading-5 text-muted-foreground">
                还没有历史会话
                <br />
                从一个问题开始吧
              </div>
            ) : (
              conversations.map((item) => (
                <div
                  className="history-conversation-row group relative mx-1.5 h-9 overflow-visible rounded-[10px] transition-colors"
                  data-active={routeConversationId === item.id}
                  key={item.id}
                >
                  <Button
                    type="button"
                    variant="ghost"
                    className="relative h-full w-full justify-start rounded-[10px] px-2.5 text-left font-normal shadow-none hover:bg-transparent"
                    onClick={() => void openConversation(item)}
                    disabled={busy}
                    aria-current={routeConversationId === item.id ? "page" : undefined}
                  >
                    <span className="block min-w-0 flex-1 overflow-hidden whitespace-nowrap">
                      {item.title || "未命名对话"}
                    </span>
                    <span
                      aria-hidden="true"
                      className="history-conversation-fade pointer-events-none absolute inset-y-0 right-0 w-10 rounded-r-[10px]"
                    />
                  </Button>
                  <div className="history-conversation-actions pointer-events-none absolute inset-y-0 right-0 z-20 flex items-center rounded-r-[10px] pl-4 opacity-0 transition-opacity group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-9 w-[34px] shrink-0 rounded-[10px] bg-transparent text-muted-foreground hover:bg-black/[0.04] hover:text-sidebar-foreground data-[state=open]:text-sidebar-foreground"
                          aria-label={`管理${item.title || "未命名对话"}`}
                          disabled={busy || conversationActionId === item.id}
                        >
                          <MoreHorizontal className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent side="bottom" align="start" sideOffset={4} className="w-44">
                        <DropdownMenuItem onSelect={() => beginRename(item)}>
                          <Pencil />重命名
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => void togglePinned(item)}>
                          {item.is_pinned ? (
                            <PinOff className="rotate-45" />
                          ) : (
                            <Pin className="rotate-45" />
                          )}
                          {item.is_pinned ? "取消置顶" : "置顶"}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="text-destructive focus:bg-destructive/10 focus:text-destructive"
                          onSelect={() => setDeleteTarget(item)}
                        >
                          <Trash2 />删除
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                </div>
              ))
            )}
          </nav>
        </ScrollArea>

        <div className="mt-3 border-t border-sidebar-border px-2 pt-3">
          <div className="flex items-center gap-2 rounded-xl px-2 py-2 text-xs text-muted-foreground">
            <ShieldCheck className="size-4 text-emerald-600" />
            <span>{isInitializing ? "正在建立本地访问身份…" : "本地访问身份已启用"}</span>
          </div>
        </div>
      </aside>

      <main className="relative flex min-w-0 flex-1 flex-col bg-background">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-transparent px-4 lg:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="lg:hidden"
              onClick={() => setSidebarOpen(true)}
              aria-label="打开历史会话"
            >
              <Menu />
            </Button>
            <h1 className="truncate text-sm font-medium">
              {conversation !== null
                ? conversation.title || "未命名对话"
                : pendingNewMessage !== null
                  ? titleFrom(pendingNewMessage.content)
                : routeConversationId === null
                  ? "新对话"
                  : "正在加载会话"}
            </h1>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {conversation && (
              <span title={`模型版本 ${conversation.model_endpoint_version_id}`}>
                {conversation.current_agent_version_id !== null &&
                conversation.current_agent_version_id !== conversation.agent_version_id
                  ? `历史版本 · Agent v${conversation.agent_version_number}`
                  : `Agent v${conversation.agent_version_number}`}
              </span>
            )}
            <span className={cn("size-1.5 rounded-full bg-emerald-500", busy && "animate-pulse")} />
            <span>{statusLabel(runStatus, uiStatus)}</span>
          </div>
        </header>

        {isRoutedConversationLoading ? (
          <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden text-sm text-muted-foreground">
            <RefreshCw className="mr-2 size-4 animate-spin" />读取会话…
          </div>
        ) : displayedMessages.length === 0 ? (
          <div className="flex min-h-0 flex-1 overflow-hidden px-5 sm:px-8">
            <section className="m-auto flex w-full max-w-3xl flex-col items-center py-8 text-center">
                <AgentMark agent={agent} className="size-11 rounded-2xl shadow-sm" />
                <h2 className="mt-5 text-2xl font-semibold tracking-tight sm:text-3xl">
                  今天想解决什么问题？
                </h2>
                <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
                  {agent?.welcome_message || agent?.description || "描述你遇到的技术问题。"}
                </p>
                <div className="mt-8 grid w-full max-w-2xl gap-2 sm:grid-cols-3">
                  {suggestions.map((suggestion) => (
                    <Button
                      key={suggestion}
                      type="button"
                      variant="outline"
                      className="h-auto whitespace-normal rounded-2xl bg-card p-4 text-left text-sm font-normal leading-5 text-muted-foreground shadow-xs transition-all hover:-translate-y-0.5 hover:border-foreground/15 hover:text-foreground hover:shadow-sm"
                      onClick={() => {
                        setDraft(suggestion);
                        requestAnimationFrame(() => composerRef.current?.focus());
                      }}
                    >
                      {suggestion}
                    </Button>
                  ))}
                </div>
            </section>
          </div>
        ) : (
          <ScrollArea className="min-h-0 flex-1">
            <div className="mx-auto w-full max-w-3xl px-5 pb-10 pt-8 sm:px-8">
              <section
                className="conversation-content-enter space-y-8"
                aria-live="polite"
                aria-busy={busy}
              >
                {displayedMessages.map((message) =>
                  message.role === "user" ? (
                    <article className="flex justify-end" key={message.id}>
                      <div className="max-w-[85%] rounded-3xl rounded-br-lg bg-user-message px-4 py-3 text-[15px] leading-6 sm:max-w-[72%]">
                        <p className="whitespace-pre-wrap">{message.content}</p>
                      </div>
                    </article>
                  ) : (
                    <article className="flex gap-3.5" key={message.id}>
                      <AgentMark agent={agent} className="mt-0.5 size-8 rounded-full border shadow-xs" />
                      <div className="min-w-0 flex-1 pt-1">
                        <div className="mb-1.5 text-xs font-medium text-muted-foreground">
                          {agent?.name ?? "Agent"}
                        </div>
                        <p className="whitespace-pre-wrap text-[15px] leading-7">{message.content}</p>
                      </div>
                    </article>
                  ),
                )}
                {(uiStatus === "sending" || uiStatus === "streaming") &&
                  displayedMessages.at(-1)?.role !== "assistant" && (
                    <div className="flex items-center gap-3.5 text-sm text-muted-foreground">
                      <AgentMark agent={agent} className="size-8 rounded-full border" />
                      <span className="thinking-dots" aria-label={`${agent?.name ?? "Agent"} 正在思考`}>
                        <i />
                        <i />
                        <i />
                      </span>
                    </div>
                  )}
                <div ref={messagesEndRef} />
              </section>
            </div>
          </ScrollArea>
        )}

        <div
          className={cn(
            "shrink-0 bg-gradient-to-t from-background via-background to-transparent px-4 pb-4 pt-2 sm:px-6",
            isRoutedConversationLoading && "invisible pointer-events-none",
          )}
        >
          <div className="mx-auto w-full max-w-3xl">
            {!agentUsable && conversation !== null && (
              <Alert className="mb-2 bg-muted text-muted-foreground" role="status">
                <AlertDescription>该 Agent 已停用或你的授权已撤销；历史消息仍可查看，但不能继续发送。</AlertDescription>
              </Alert>
            )}
            {agentUsable && conversation !== null && conversation.current_agent_version_id !== null && conversation.current_agent_version_id !== conversation.agent_version_id && (
              <Alert className="mb-2 bg-muted text-muted-foreground" role="status">
                <AlertDescription className="flex items-center justify-between gap-3">
                  <span>当前会话固定在历史 Agent 版本，不会随配置更新。</span>
                  <Button variant="outline" size="sm" onClick={() => resetToNewConversation()}>使用当前版本新建对话</Button>
                </AlertDescription>
              </Alert>
            )}
            {error !== null && !isBootFailure && (
              <Alert variant="destructive" className="mb-2">
                <AlertCircle className="size-4" />
                <AlertDescription className="flex items-center justify-between gap-3">
                  <span className="truncate">{error}</span>
                  {activeRunId !== null && conversation !== null && token !== null && (
                    <Button variant="ghost" size="sm" className="text-destructive hover:text-destructive" onClick={() => void subscribe(activeRunId, conversation.id, token, lastEventId)}>重试</Button>
                  )}
                </AlertDescription>
              </Alert>
            )}

            <form
              className="rounded-[26px] border bg-card p-2 shadow-[0_12px_40px_rgba(0,0,0,0.08)] transition-shadow focus-within:shadow-[0_14px_44px_rgba(0,0,0,0.12)]"
              onSubmit={submit}
            >
              <Textarea
                ref={composerRef}
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={handleComposerKeyDown}
                placeholder={`向 ${agent?.name ?? "Agent"} 提问`}
                disabled={token === null || busy || !agentUsable}
                rows={2}
                aria-label="输入支持问题"
                className="max-h-40 min-h-[52px] rounded-none border-0 bg-transparent px-3 py-2.5 text-[15px]"
              />
              <div className="flex items-center justify-between px-1 pb-1">
                <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
                  <Sparkles className="size-3.5" />企业技术支持
                </div>
                {busy ? (
                  <Button
                    type="button"
                    size="icon"
                    variant="outline"
                    className="size-9 rounded-full"
                    onClick={() => void stopRun()}
                    aria-label="停止生成"
                  >
                    <Square className="size-3.5 fill-current" />
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    size="icon"
                    className="size-9 rounded-full"
                    disabled={token === null || !draft.trim() || !agentUsable}
                    aria-label="发送消息"
                  >
                    <ArrowUp className="size-4.5" />
                  </Button>
                )}
              </div>
            </form>
            <p className="mt-2 text-center text-[11px] text-muted-foreground">
              {agent?.name ?? "Agent"} 可能会出错，请核对关键技术结论。
            </p>
          </div>
        </div>
      </main>

      <Dialog open={renameTarget !== null} onOpenChange={(open) => { if (!open) { setRenameTarget(null); setRenameValue(""); setRenameError(undefined); } }}>
        <DialogContent>
          <form onSubmit={renameConversation} noValidate>
            <DialogHeader>
              <DialogTitle>重命名对话</DialogTitle>
              <DialogDescription>输入一个便于在历史记录中识别的名称。</DialogDescription>
            </DialogHeader>
            <FormField label="会话名称" htmlFor="rename-conversation" required error={renameError} className="mt-5"><Input
              id="rename-conversation"
              value={renameValue}
              onChange={(event) => { setRenameValue(event.target.value); setRenameError(undefined); }}
              placeholder="请输入会话名称"
              maxLength={200}
              autoFocus
              aria-label="会话名称"
              aria-invalid={Boolean(renameError)}
              aria-describedby={renameError ? "rename-conversation-error" : undefined}
            /></FormField>
            <DialogFooter className="mt-6">
              <DialogClose asChild>
                <Button variant="outline">取消</Button>
              </DialogClose>
              <Button
                type="submit"
                disabled={conversationActionId !== null}
              >
                保存
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除这个对话？</AlertDialogTitle>
            <AlertDialogDescription>
              “{deleteTarget?.title || "未命名对话"}”及其消息和运行记录将被永久删除，此操作无法撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void confirmDeleteConversation()}
              disabled={conversationActionId !== null}
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
