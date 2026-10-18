import { useState } from "react";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { ErasureCategoryImpact } from "@/types";

const explanations: Record<string, { label: string; description: string }> = {
  FIXED_MODEL_CREDENTIAL_REFERENCE: {
    label: "固定模型版本关联的凭据",
    description: "凭据可能同时被其他版本和主体使用，不能按个人记录直接清理。",
  },
  FIXED_RUN_CREDENTIAL_REVISION_REFERENCE: {
    label: "运行实际固定的凭据修订",
    description: "按运行中保存的凭据及修订组合统计，不包含同凭据的其他修订。",
  },
  FIXED_CONVERSATION_AGENT_REFERENCE: {
    label: "会话固定的 Agent 版本",
    description: "历史会话仍引用这些版本；停用、软删除不会清除引用事实。",
  },
  FIXED_MODEL_VERSION_REFERENCE: {
    label: "会话与运行关联的模型版本",
    description: "包含会话固定版本和运行固定模型的关联，不授予使用权限。",
  },
  FIXED_EVIDENCE_SNAPSHOT_REFERENCE: {
    label: "知识证据引用的发布快照",
    description: "仅统计截止时间内本人会话证据的关联，不读取快照标题或正文。",
  },
  FIXED_EVIDENCE_CHUNK_REFERENCE: {
    label: "知识证据引用的分块",
    description: "依据截止时间内证据定位统计；同一分块的重复引用只计一份。",
  },
};

type ReferenceRow = { key: string; label: string; description: string; count: number | null };
const columns: AppTableColumn<ReferenceRow>[] = [
  { title: "引用范围", dataIndex: "label" },
  { title: "关联记录数", key: "count", render: (_, row) => row.count ?? "未确认" },
  { title: "保留依赖说明", dataIndex: "description" },
];

export function FixedReferenceDetails({ impacts }: { impacts: ErasureCategoryImpact[] }) {
  const [open, setOpen] = useState(false);
  const selected = impacts.filter((impact) => impact.category === "knowledge" || impact.category === "credentials");
  if (!selected.length) return null;
  const rows: ReferenceRow[] = selected.flatMap((impact) => impact.tables.flatMap((item, index) => {
    const code = item.reason_code;
    if (typeof code !== "string" || !Object.prototype.hasOwnProperty.call(explanations, code)
      || item.relation !== "shared_reference") return [];
    const explanation = explanations[code];
    if (!explanation) return [];
    return [{
      key: `${impact.category}:${code}:${index}`,
      ...explanation,
      count: typeof item.row_count === "number" && Number.isSafeInteger(item.row_count) && item.row_count >= 0
        ? item.row_count : null,
    }];
  }));
  return <Collapsible open={open} onOpenChange={setOpen} className="space-y-3">
    <CollapsibleTrigger asChild>
      <Button variant="outline">{open ? "收起固定引用说明" : "展开固定引用说明"}</Button>
    </CollapsibleTrigger>
    <CollapsibleContent className="space-y-3">
      <p className="text-sm text-muted-foreground">
        以下是本次报告记录的保留依赖，关联数量可能重叠，不是可删除数量，也不证明凭据仍有效或知识仍获授权。
        在途运行与未确认动作按报告上方单独统计；其他主体、外部副本及未列出的依赖仍需盘点。
      </p>
      {rows.length ? <AppTable columns={columns} dataSource={rows} rowKey="key" ariaLabel="固定引用保留依赖" />
        : <p role="status" className="text-sm text-muted-foreground">本报告未记录可识别的固定引用说明，不能据此判断没有依赖。旧报告不会重新计算或补填。</p>}
    </CollapsibleContent>
  </Collapsible>;
}
