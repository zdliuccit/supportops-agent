import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, CircleAlert, FileText, ListChecks, Pencil } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { listKnowledgeDocuments, listKnowledgeSources } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { SearchableSelect } from "@/components/SearchableSelect";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";

import type { KnowledgeDocument } from "@/types";

import { statusLabels, formatDateTime, shortId, environmentLabel, statusBadge } from "@/pages/knowledge/lib/presentation";

export function KnowledgeManagementPage() {
  const navigate = useNavigate();
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const requestId = useRef(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tablePagination, setTablePagination] = useState({ current: 1, pageSize: 10 });
  const [totalDocuments, setTotalDocuments] = useState(0);
  const [keywords, setKeywords] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [status, setStatus] = useState("");
  const [sourceId, setSourceId] = useState("");

  const loadSourceOptions = useCallback(async (keywords: string, pageSize: number) => {
    const result = await withRefreshedToken((token) => listKnowledgeSources(token, { keywords, pageSize, page: 1 }));
    return result.value.items.map((source) => ({ value: source.id, label: source.name }));
  }, []);

  async function load(pagination = tablePagination) {
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const result = await delayRequest(() => withRefreshedToken(async (token) => {
        return listKnowledgeDocuments(token, {
            page: pagination.current,
            pageSize: pagination.pageSize,
            status: status || undefined,
            sourceId: sourceId || undefined,
            ownerName: ownerName.trim() || undefined,
            keywords: keywords.trim() || undefined,
          });
      }));
      if (currentRequest !== requestId.current) return;
      setDocuments(result.value.items);
      setTotalDocuments(result.value.total);
    } catch (cause) {
      if (currentRequest !== requestId.current) return;
      setError(cause instanceof Error ? cause.message : "加载知识列表失败");
    } finally {
      if (currentRequest === requestId.current) setLoading(false);
    }
  }

  useEffect(() => {
    const nextPagination = { current: 1, pageSize: tablePagination.pageSize };
    setTablePagination(nextPagination);
    void load(nextPagination);
    return () => { requestId.current += 1; };
  }, [keywords, ownerName, status, sourceId]);

  const columns: AppTableColumn<KnowledgeDocument>[] = [
    {
      title: "知识文档",
      key: "document",
      width: "25%",
      minWidth: 220,
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
      minWidth: 180,
      render: (_, document) => <div><div className="truncate text-sm">{document.source_name ?? shortId(document.source_id)}</div><div className="mt-1 text-xs text-[#919eab]">{document.source_type ? `${document.source_type} · ${environmentLabel(document.source_environment ?? "")}` : "—"}</div></div>,
    },
    { title: "状态", dataIndex: "status", width: "12%", minWidth: 90, render: (value) => statusBadge(String(value)) },
    { title: "当前版本", key: "version", width: "14%", minWidth: 110, render: (_, document) => <span className="text-sm text-[#637381]">{document.current_version_id ? "已绑定" : "未发布"}</span> },
    { title: "负责人", key: "owner", width: "15%", minWidth: 120, render: (_, document) => <span className="text-sm text-[#637381]">{document.owner_user_name ?? shortId(document.owner_user_id)}</span> },
    { title: "创建人", key: "created_by", width: "15%", minWidth: 120, render: (_, document) => <span className="text-sm text-[#637381]">{document.created_by_name ?? shortId(document.created_by)}</span> },
    { title: "更新时间", dataIndex: "updated_at", width: "17%", minWidth: 180, render: (value) => <span className="whitespace-nowrap text-xs text-[#637381]">{formatDateTime(String(value))}</span> },
    { title: "操作", key: "actions", width: 112, minWidth: 112, fixed: "right", align: "right", render: (_, document) => <Button size="sm" variant="outline" onClick={() => navigate(`/knowledge/${document.id}`)}><Pencil className="size-4" />详情</Button> },
  ];

  return (
    <>
      <PageHeader title="知识管理" description="维护知识来源、发布状态和有效期，为后续检索提供可信版本。"
        actions={<Button variant="outline" onClick={() => navigate("/knowledge/ingestion-jobs")}><ListChecks className="size-4" aria-hidden="true" />导入任务</Button>} />
      {error && <Alert variant="destructive" className="mb-5"><CircleAlert className="size-5" /><AlertDescription>{error}</AlertDescription></Alert>}
      <section>
        <ListToolbar onRefresh={() => void load()} loading={loading} filters={<div className="flex min-w-0 flex-1 flex-wrap items-center gap-3 pb-1">
          <Input className="h-9 w-56 shrink-0" placeholder="搜索文档标识" value={keywords} onChange={(event) => setKeywords(event.target.value)} aria-label="搜索知识文档" />
          <Input className="h-9 w-56 shrink-0" placeholder="负责人姓名" value={ownerName} onChange={(event) => setOwnerName(event.target.value)} aria-label="按负责人姓名筛选" />
          <SearchableSelect value={sourceId} onValueChange={setSourceId} loadOptions={loadSourceOptions} placeholder="全部来源" clearLabel="全部来源" searchPlaceholder="请输入来源名称" />
          <Select value={status || "all"} onValueChange={(value) => setStatus(value === "all" ? "" : value)}><SelectTrigger className="h-9 !w-auto min-w-[7rem] shrink-0" aria-label="按状态筛选"><SelectValue placeholder="全部状态" /></SelectTrigger><SelectContent><SelectItem value="all">全部状态</SelectItem>{Object.entries(statusLabels).filter(([value]) => ["draft", "published", "expired", "archived"].includes(value)).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select>
        </div>} />
        <div className="relative min-h-[360px]"><AppTable columns={columns} dataSource={documents} rowKey="id" scroll={{ x: 1280 }} emptyText={<><BookOpen className="mx-auto mb-2 size-8" /><span>当前筛选条件下暂无知识文档</span></>} pagination={{ current: tablePagination.current, pageSize: tablePagination.pageSize, total: totalDocuments, onChange: (current, pageSize) => { const nextPagination = { current, pageSize }; setTablePagination(nextPagination); void load(nextPagination); } }} ariaLabel="知识文档列表" />{loading && <ListLoadingOverlay label="正在加载知识列表…" />}</div>
      </section>
    </>
  );
}
