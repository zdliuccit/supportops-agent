import { useEffect, useState } from "react";
import { BookOpen, CircleAlert, LoaderCircle, RefreshCw } from "lucide-react";

import { createKnowledgeVersion, getKnowledgeVersions, listKnowledgeDocuments, listKnowledgeSources, publishKnowledgeVersion, retireKnowledgeVersion, rollbackKnowledgeVersion, submitKnowledgeVersion } from "@/api";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import type { KnowledgeDocument, KnowledgeSource, KnowledgeVersion } from "@/types";

const statusLabels: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  expired: "已过期",
  archived: "已归档",
};

/** 知识治理的最小管理入口，先提供清单与筛选，编辑流程由后续版本详情继续补齐。 */
export function KnowledgeManagementPage() {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [status, setStatus] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [ownerUserId, setOwnerUserId] = useState("");
  const [keywords, setKeywords] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<KnowledgeDocument | null>(null);
  const [versions, setVersions] = useState<KnowledgeVersion[]>([]);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftContent, setDraftContent] = useState("");

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await withRefreshedToken(async (token) => {
        const [documentResult, sourceResult] = await Promise.all([
          listKnowledgeDocuments(token, { page: 1, pageSize: 100, status: status || undefined, sourceId: sourceId || undefined, ownerUserId: ownerUserId || undefined, keywords }),
          listKnowledgeSources(token),
        ]);
        return { documents: documentResult, sources: sourceResult };
      });
      setDocuments(result.value.documents.items);
      setSources(result.value.sources.items);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "加载知识清单失败";
      setError(message);
      notify.error(cause, "加载知识清单失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), [status, sourceId]);

  async function openDocument(document: KnowledgeDocument) {
    setSelected(document);
    setBusy(true);
    try {
      const result = await withRefreshedToken((token) => getKnowledgeVersions(token, document.id));
      setVersions(result.value);
      setDraftTitle(result.value[0]?.title ?? document.external_key);
      setDraftContent(result.value[0]?.content_markdown ?? "");
    } catch (cause) {
      notify.error(cause, "加载知识版本失败");
    } finally {
      setBusy(false);
    }
  }

  async function refreshSelected() {
    if (!selected) return;
    await openDocument(selected);
    await load();
  }

  async function saveDraft() {
    if (!selected || !draftTitle.trim() || !draftContent.trim()) return;
    setBusy(true);
    try {
      const expectedRevision = versions[0]?.version_number ?? 0;
      await withRefreshedToken((token) => createKnowledgeVersion(token, selected.id, {
        title: draftTitle,
        content_markdown: draftContent,
        expected_revision: expectedRevision,
        change_summary: "知识管理界面保存",
      }));
      notify.success("新草稿版本已创建。");
      await refreshSelected();
    } catch (cause) {
      notify.error(cause, "保存知识草稿失败");
    } finally {
      setBusy(false);
    }
  }

  async function runVersionAction(action: "submit" | "publish" | "retire" | "rollback", version: KnowledgeVersion) {
    if (!selected || !window.confirm(`确认对版本 v${version.version_number} 执行此操作？`)) return;
    setBusy(true);
    try {
      await withRefreshedToken((token) => action === "submit"
        ? submitKnowledgeVersion(token, version.id)
        : action === "publish"
          ? publishKnowledgeVersion(token, version.id)
          : action === "retire"
            ? retireKnowledgeVersion(token, version.id)
            : rollbackKnowledgeVersion(token, selected.id, version.id, versions[0]?.version_number ?? version.version_number));
      notify.success("知识版本状态已更新。");
      await refreshSelected();
    } catch (cause) {
      notify.error(cause, "更新知识版本失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="知识管理"
        description="维护知识来源、发布状态和有效期，为后续检索提供可信版本。"
        actions={<Button variant="outline" onClick={() => void load()} disabled={loading}><RefreshCw className={loading ? "animate-spin" : ""} />刷新</Button>}
      />
      {error && <Alert variant="destructive" className="mb-5"><CircleAlert className="size-5" /><AlertDescription>{error}</AlertDescription></Alert>}
      <section className="rounded-xl border border-[#e6eaee] bg-white p-5 shadow-sm">
        <div className="mb-5 flex flex-wrap items-center gap-3">
          <Input aria-label="搜索知识文档" placeholder="按外部标识搜索" value={keywords} onChange={(event) => setKeywords(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void load(); }} className="w-64" />
          <Input aria-label="按负责人筛选" placeholder="负责人用户 ID" value={ownerUserId} onChange={(event) => setOwnerUserId(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void load(); }} className="w-56" />
          <select aria-label="按来源筛选" value={sourceId} onChange={(event) => setSourceId(event.target.value)} className="h-10 rounded-md border border-input bg-background px-3 text-sm">
            <option value="">全部来源</option>
            {sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>)}
          </select>
          <select aria-label="按状态筛选" value={status} onChange={(event) => setStatus(event.target.value)} className="h-10 rounded-md border border-input bg-background px-3 text-sm">
            <option value="">全部状态</option>
            {Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <Button variant="subtle" onClick={() => void load()}>查询</Button>
        </div>
        {loading ? <div className="grid h-48 place-items-center"><LoaderCircle className="animate-spin text-emerald-500" /></div> : documents.length === 0 ? (
          <div className="grid h-48 place-items-center text-center text-sm text-[#919eab]"><BookOpen className="mb-2 size-8" /><p>当前筛选条件下暂无知识文档</p></div>
        ) : (
          <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr className="border-b text-xs text-[#919eab]"><th className="px-3 py-3">文档标识</th><th className="px-3 py-3">来源</th><th className="px-3 py-3">状态</th><th className="px-3 py-3">当前版本</th><th className="px-3 py-3">更新时间</th></tr></thead><tbody>{documents.map((document) => <tr key={document.id} className="border-b last:border-0 hover:bg-[#f8faf9]"><td className="px-3 py-3 font-medium"><button type="button" className="text-left text-[#1c252e] underline-offset-4 hover:text-emerald-600 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500" onClick={() => void openDocument(document)}>{document.external_key}</button></td><td className="px-3 py-3 text-[#637381]">{sources.find((source) => source.id === document.source_id)?.name ?? document.source_id}</td><td className="px-3 py-3"><span className="rounded-full bg-[#eef7f3] px-2.5 py-1 text-xs text-[#008f63]">{statusLabels[document.status] ?? document.status}</span></td><td className="px-3 py-3 text-[#637381]">{document.current_version_id ? "已绑定" : "—"}</td><td className="px-3 py-3 text-[#637381]">{new Date(document.updated_at).toLocaleString("zh-CN")}</td></tr>)}</tbody></table></div>
        )}
      </section>
      {selected && <section className="mt-5 rounded-xl border border-[#e6eaee] bg-white p-5 shadow-sm" aria-busy={busy}>
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-semibold">{selected.external_key}</h2><p className="mt-1 text-xs text-[#919eab]">负责人 {selected.owner_user_id} · 状态 {statusLabels[selected.status] ?? selected.status}</p></div><Button variant="outline" onClick={() => setSelected(null)} disabled={busy}>关闭详情</Button></div>
        <div className="grid gap-5 lg:grid-cols-2">
          <div><label className="mb-1 block text-sm font-medium" htmlFor="knowledge-title">新版本标题</label><Input id="knowledge-title" value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} disabled={busy} /><label className="mb-1 mt-4 block text-sm font-medium" htmlFor="knowledge-content">Markdown 正文</label><textarea id="knowledge-content" value={draftContent} onChange={(event) => setDraftContent(event.target.value)} disabled={busy} className="min-h-64 w-full rounded-md border border-input p-3 font-mono text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500" /><Button className="mt-3" onClick={() => void saveDraft()} disabled={busy || !draftTitle.trim() || !draftContent.trim()}>创建草稿版本</Button></div>
          <div><h3 className="mb-3 text-sm font-semibold">版本历史</h3><div className="max-h-[420px] space-y-3 overflow-y-auto">{versions.map((version) => <article key={version.id} className="rounded-lg border p-3"><div className="flex items-center justify-between gap-2"><strong className="text-sm">v{version.version_number} · {version.title}</strong><span className="text-xs text-[#637381]">{version.status}</span></div><p className="mt-2 line-clamp-3 whitespace-pre-wrap text-xs text-[#637381]">{version.content_markdown}</p><div className="mt-3 flex flex-wrap gap-2">{version.status === "draft" && <Button size="sm" variant="outline" disabled={busy} onClick={() => void runVersionAction("submit", version)}>提交审核</Button>}{version.status === "in_review" && <Button size="sm" disabled={busy} onClick={() => void runVersionAction("publish", version)}>发布</Button>}{version.status === "published" && <Button size="sm" variant="destructive" disabled={busy} onClick={() => void runVersionAction("retire", version)}>失效</Button>}{(version.status === "superseded" || version.status === "retired") && <Button size="sm" variant="outline" disabled={busy} onClick={() => void runVersionAction("rollback", version)}>创建回滚草稿</Button>}</div></article>)}</div>{versions.length >= 2 && <div className="mt-4 grid gap-2 sm:grid-cols-2"><pre className="max-h-48 overflow-auto rounded bg-[#f4f6f8] p-3 text-xs">{versions[0].content_markdown}</pre><pre className="max-h-48 overflow-auto rounded bg-[#f4f6f8] p-3 text-xs">{versions[1].content_markdown}</pre></div>}</div>
        </div>
      </section>}
    </>
  );
}
