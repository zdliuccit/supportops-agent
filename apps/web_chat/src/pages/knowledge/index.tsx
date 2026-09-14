import { useEffect, useState } from "react";
import { ArrowLeft, BookOpen, CircleAlert, FileText, Pencil, Save } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import {
  createKnowledgeVersion,
  getKnowledgeDocument,
  getKnowledgeVersions,
  listKnowledgeDocuments,
  listKnowledgeSources,
  publishKnowledgeVersion,
  retireKnowledgeVersion,
  rollbackKnowledgeVersion,
  submitKnowledgeVersion,
} from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { KnowledgeDocument, KnowledgeSource, KnowledgeVersion } from "@/types";

const statusLabels: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  expired: "已过期",
  archived: "已归档",
  in_review: "审核中",
  superseded: "已取代",
  retired: "已失效",
};

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function shortId(value: string | null | undefined): string {
  return value ? `${value.slice(0, 8)}…` : "—";
}

function environmentLabel(value: string): string {
  return value.toLowerCase() === "mock" ? "Mock" : value;
}

function sourceName(sources: KnowledgeSource[], sourceId: string): string {
  return sources.find((source) => source.id === sourceId)?.name ?? shortId(sourceId);
}

function statusBadge(value: string) {
  return <StatusBadge value={value} label={statusLabels[value] ?? value} />;
}

export function KnowledgeManagementPage() {
  const navigate = useNavigate();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tablePagination, setTablePagination] = useState({ current: 1, pageSize: 10 });
  const [totalDocuments, setTotalDocuments] = useState(0);
  const [keywords, setKeywords] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [status, setStatus] = useState("");
  const [sourceId, setSourceId] = useState("");

  async function load(pagination = tablePagination) {
    setLoading(true);
    setError(null);
    try {
      const result = await delayRequest(() => withRefreshedToken(async (token) => {
        const [documentResult, sourceResult] = await Promise.all([
          listKnowledgeDocuments(token, {
            page: pagination.current,
            pageSize: pagination.pageSize,
            status: status || undefined,
            sourceId: sourceId || undefined,
            ownerName: ownerName.trim() || undefined,
            keywords: keywords.trim() || undefined,
          }),
          listKnowledgeSources(token),
        ]);
        return { documents: documentResult, sources: sourceResult };
      }));
      setDocuments(result.value.documents.items);
      setTotalDocuments(result.value.documents.total);
      setSources(result.value.sources.items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载知识列表失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    const nextPagination = { current: 1, pageSize: tablePagination.pageSize };
    setTablePagination(nextPagination);
    void load(nextPagination);
  }, [keywords, ownerName, status, sourceId]);

  const columns: AppTableColumn<KnowledgeDocument>[] = [
    {
      title: "知识文档",
      key: "document",
      width: "25%",
      render: (_, document) => (
        <button type="button" className="flex min-w-[220px] items-center gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500" onClick={() => navigate(`/knowledge/${document.id}`)} aria-label={`查看 ${document.external_key}`}>
          <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-emerald-50 text-emerald-700"><FileText className="size-5" /></span>
          <span className="min-w-0"><span className="block truncate font-semibold hover:text-emerald-600">{document.external_key}</span><span className="mt-1 block truncate text-xs text-[#919eab]">ID {shortId(document.id)}</span></span>
        </button>
      ),
    },
    {
      title: "来源",
      key: "source",
      width: "20%",
      render: (_, document) => { const source = sources.find((item) => item.id === document.source_id); return <div><div className="truncate text-sm">{source?.name ?? shortId(document.source_id)}</div><div className="mt-1 text-xs text-[#919eab]">{source ? `${source.source_type} · ${environmentLabel(source.environment)}` : "—"}</div></div>; },
    },
    { title: "状态", dataIndex: "status", width: "12%", render: (value) => statusBadge(String(value)) },
    { title: "当前版本", key: "version", width: "14%", render: (_, document) => <span className="text-sm text-[#637381]">{document.current_version_id ? "已绑定" : "未发布"}</span> },
    { title: "负责人", key: "owner", width: "15%", render: (_, document) => <span className="text-sm text-[#637381]">{document.owner_user_name ?? shortId(document.owner_user_id)}</span> },
    { title: "创建人", key: "created_by", width: "15%", render: (_, document) => <span className="text-sm text-[#637381]">{document.created_by_name ?? shortId(document.created_by)}</span> },
    { title: "更新时间", dataIndex: "updated_at", width: "17%", render: (value) => <span className="whitespace-nowrap text-xs text-[#637381]">{formatDateTime(String(value))}</span> },
    { title: "操作", key: "actions", width: 112, minWidth: 112, fixed: "right", align: "right", render: (_, document) => <Button size="sm" variant="outline" onClick={() => navigate(`/knowledge/${document.id}`)}><Pencil className="size-4" />详情</Button> },
  ];

  return (
    <>
      <PageHeader title="知识管理" description="维护知识来源、发布状态和有效期，为后续检索提供可信版本。" />
      {error && <Alert variant="destructive" className="mb-5"><CircleAlert className="size-5" /><AlertDescription>{error}</AlertDescription></Alert>}
      <section>
        <ListToolbar onRefresh={() => void load()} loading={loading} filters={<div className="flex min-w-0 flex-1 flex-nowrap items-center gap-3 pb-1">
          <Input className="h-9 w-56 shrink-0" placeholder="搜索文档标识" value={keywords} onChange={(event) => setKeywords(event.target.value)} aria-label="搜索知识文档" />
          <Input className="h-9 w-56 shrink-0" placeholder="负责人姓名" value={ownerName} onChange={(event) => setOwnerName(event.target.value)} aria-label="按负责人姓名筛选" />
          <Select value={sourceId || "all"} onValueChange={(value) => setSourceId(value === "all" ? "" : value)}><SelectTrigger className="h-9 !w-auto min-w-[10rem] shrink-0" aria-label="按来源筛选"><SelectValue placeholder="全部来源" /></SelectTrigger><SelectContent><SelectItem value="all">全部来源</SelectItem>{sources.map((source) => <SelectItem key={source.id} value={source.id}>{source.name}</SelectItem>)}</SelectContent></Select>
          <Select value={status || "all"} onValueChange={(value) => setStatus(value === "all" ? "" : value)}><SelectTrigger className="h-9 !w-auto min-w-[7rem] shrink-0" aria-label="按状态筛选"><SelectValue placeholder="全部状态" /></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem>{Object.entries(statusLabels).filter(([value]) => ["draft", "published", "expired", "archived"].includes(value)).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
        </div>} />
        <div className="relative min-h-[360px]"><AppTable columns={columns} dataSource={documents} rowKey="id" emptyText={<><BookOpen className="mx-auto mb-2 size-8" /><span>当前筛选条件下暂无知识文档</span></>} pagination={{ current: tablePagination.current, pageSize: tablePagination.pageSize, total: totalDocuments, onChange: (current, pageSize) => { const nextPagination = { current, pageSize }; setTablePagination(nextPagination); void load(nextPagination); } }} ariaLabel="知识文档列表" />{loading && <ListLoadingOverlay label="正在加载知识列表…" />}</div>
      </section>
    </>
  );
}

export function KnowledgeDetailPage() {
  const navigate = useNavigate();
  const { documentId } = useParams();
  const [document, setDocument] = useState<KnowledgeDocument | null>(null);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [versions, setVersions] = useState<KnowledgeVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftContent, setDraftContent] = useState("");

  async function load() {
    if (!documentId) return;
    setLoading(true);
    setError(null);
    try {
      const result = await withRefreshedToken(async (token) => {
        const [documentResult, versionResult, sourceResult] = await Promise.all([
          getKnowledgeDocument(token, documentId),
          getKnowledgeVersions(token, documentId),
          listKnowledgeSources(token),
        ]);
        return { document: documentResult, versions: versionResult, sources: sourceResult.items };
      });
      setDocument(result.value.document);
      setVersions(result.value.versions);
      setSources(result.value.sources);
      setDraftTitle(result.value.versions[0]?.title ?? result.value.document.external_key);
      setDraftContent(result.value.versions[0]?.content_markdown ?? "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载知识详情失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [documentId]);

  async function saveDraft() {
    if (!document || !draftTitle.trim() || !draftContent.trim()) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => createKnowledgeVersion(token, document.id, { title: draftTitle.trim(), content_markdown: draftContent, expected_revision: versions[0]?.version_number ?? 0, change_summary: "知识管理界面保存" }));
      notify.success("新草稿版本已创建。");
      await load();
    } catch (cause) { notify.error(cause, "保存知识草稿失败"); } finally { setBusy(false); }
  }

  async function runVersionAction(action: "submit" | "publish" | "retire" | "rollback", version: KnowledgeVersion) {
    if (!document || !window.confirm(`确认对版本 v${version.version_number} 执行此操作？`)) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => action === "submit" ? submitKnowledgeVersion(token, version.id) : action === "publish" ? publishKnowledgeVersion(token, version.id) : action === "retire" ? retireKnowledgeVersion(token, version.id) : rollbackKnowledgeVersion(token, document.id, version.id, versions[0]?.version_number ?? version.version_number));
      notify.success("知识版本状态已更新。");
      await load();
    } catch (cause) { notify.error(cause, "更新知识版本失败"); } finally { setBusy(false); }
  }

  if (loading) return <div className="relative min-h-[520px]"><ListLoadingOverlay label="正在加载知识详情…" /></div>;
  if (!document) return <Alert variant="destructive"><CircleAlert className="size-5" /><AlertDescription>{error ?? "知识文档不存在"}</AlertDescription></Alert>;

  const source = sources.find((item) => item.id === document.source_id);
  return (
    <div className="space-y-6">
      <PageHeader title={document.external_key} description="查看知识治理信息、版本内容和发布操作。" actions={<Button variant="outline" onClick={() => navigate("/knowledge")}><ArrowLeft className="size-4" />返回知识库</Button>} />
      {error && <Alert variant="destructive"><CircleAlert className="size-5" /><AlertDescription>{error}</AlertDescription></Alert>}
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {[{ label: "文档状态", value: statusBadge(document.status) }, { label: "来源", value: <><span>{source?.name ?? shortId(document.source_id)}</span><span className="mt-1 block text-xs text-muted-foreground">{source ? `${source.source_type} · ${environmentLabel(source.environment)}` : "—"}</span></> }, { label: "负责人", value: <><span>{document.owner_user_name ?? shortId(document.owner_user_id)}</span><span className="mt-1 block font-mono text-xs text-muted-foreground">{document.owner_user_id}</span></> }, { label: "创建人", value: <><span>{document.created_by_name ?? shortId(document.created_by)}</span><span className="mt-1 block font-mono text-xs text-muted-foreground">{document.created_by}</span></> }].map((item) => <div key={item.label} className="rounded-xl border bg-white p-4 shadow-sm"><div className="text-xs text-muted-foreground">{item.label}</div><div className="mt-2 text-sm font-medium">{item.value}</div></div>)}
      </section>
      <section className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <div className="rounded-xl border bg-white p-5 shadow-sm"><div className="mb-4 flex items-center justify-between"><div><h2 className="text-base font-semibold">编辑草稿</h2><p className="mt-1 text-xs text-muted-foreground">已发布版本不可原地修改，保存会创建新版本。</p></div><Button onClick={() => void saveDraft()} disabled={busy || !draftTitle.trim() || !draftContent.trim()}><Save className="size-4" />{busy ? "保存中…" : "保存草稿"}</Button></div><label className="mb-1 block text-sm font-medium" htmlFor="knowledge-title">标题</label><Input id="knowledge-title" value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} disabled={busy} /><label className="mb-1 mt-4 block text-sm font-medium" htmlFor="knowledge-content">Markdown 正文</label><textarea id="knowledge-content" value={draftContent} onChange={(event) => setDraftContent(event.target.value)} disabled={busy} className="min-h-[420px] w-full rounded-md border border-input p-3 font-mono text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500" /></div>
        <div className="rounded-xl border bg-white p-5 shadow-sm"><div className="mb-4 flex items-center justify-between"><div><h2 className="text-base font-semibold">版本历史</h2><p className="mt-1 text-xs text-muted-foreground">共 {versions.length} 个不可变版本</p></div><StatusBadge value={document.status} label={statusLabels[document.status] ?? document.status} /></div><div className="space-y-4">{versions.map((version) => <article key={version.id} className="relative rounded-xl border p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-sm font-semibold">v{version.version_number} · {version.title}</h3><p className="mt-1 text-xs text-muted-foreground">创建人：{version.created_by_name ?? shortId(version.created_by)} · {formatDateTime(version.created_at)}</p></div>{statusBadge(version.status)}</div><p className="mt-3 whitespace-pre-wrap rounded-lg bg-muted/30 p-3 text-xs leading-5 text-[#637381]">{version.content_markdown}</p><p className="mt-3 text-xs text-muted-foreground">{version.change_summary || "未填写变更说明"}</p><div className="mt-4 flex flex-wrap gap-2">{version.status === "draft" && <Button size="sm" variant="outline" disabled={busy} onClick={() => void runVersionAction("submit", version)}>提交审核</Button>}{version.status === "in_review" && <Button size="sm" disabled={busy} onClick={() => void runVersionAction("publish", version)}>发布</Button>}{version.status === "published" && <Button size="sm" variant="destructive" disabled={busy} onClick={() => void runVersionAction("retire", version)}>失效</Button>}{(version.status === "superseded" || version.status === "retired") && <Button size="sm" variant="outline" disabled={busy} onClick={() => void runVersionAction("rollback", version)}>创建回滚草稿</Button>}</div></article>)}</div></div>
      </section>
    </div>
  );
}
