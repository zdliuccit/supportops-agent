import { useCallback, useEffect, useRef, useState } from "react";
import { acknowledgeRuntimeAlert, listRuntimeAlerts } from "@/api";
import { AppTable, type AppTableColumn } from "@/components/AppTable";
import { ListLoadingOverlay } from "@/components/ListLoadingOverlay";
import { ListToolbar } from "@/components/ListToolbar";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { withRefreshedToken } from "@/lib/auth";
import { delayRequest } from "@/lib/delayRequest";
import { notify } from "@/lib/notifications";
import type { RuntimeAlert, RuntimeAlertList } from "@/types";
import { RuntimeAlertDetail } from "./components/RuntimeAlertDetail";

const rules = { BUDGET_EXHAUSTED: "预算耗尽", LEASE_EXPIRED: "租约过期", RECOVERY_ACTIVE: "运行恢复",
  RECOVERY_NEEDS_ATTENTION: "恢复需处理", ACTION_RECONCILIATION_REQUIRED: "动作待对账", AUTHORIZATION_DENIED: "授权拒绝",
  FIRST_OUTPUT_SLOW: "模型首输出慢", OBSERVATION_MISSING: "观测缺失" };
type StateFilter = "unacknowledged" | "acknowledged" | "resolved" | "all";

export function RuntimeAlertsPage() {
  const [result, setResult] = useState<RuntimeAlertList | null>(null);
  const [page, setPage] = useState({ current: 1, pageSize: 20 });
  const [state, setState] = useState<StateFilter>("unacknowledged");
  const [severity, setSeverity] = useState<RuntimeAlert["severity"] | "all">("all");
  const [rule, setRule] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<RuntimeAlert | null>(null);
  const [acknowledging, setAcknowledging] = useState<string | null>(null);
  const ackBusy = useRef(false);
  const sequence = useRef(0);
  const mounted = useRef(false);
  const load = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true); setError(null); setResult(null); setDetail(null);
    try {
      const response = await delayRequest(() => withRefreshedToken((token) => listRuntimeAlerts(token, {
        page: page.current, pageSize: page.pageSize, state, severity: severity === "all" ? undefined : severity,
        ruleCode: rule === "all" ? undefined : rule,
      })));
      if (sequence.current === current) setResult(response.value);
    } catch (cause) {
      if (sequence.current === current) setError(cause instanceof Error ? cause.message : "运行告警读取失败");
    } finally { if (sequence.current === current) setLoading(false); }
  }, [page.current, page.pageSize, rule, severity, state]);
  const latestLoad = useRef(load);
  latestLoad.current = load;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { void load(); return () => { sequence.current += 1; }; }, [load]);

  async function acknowledge(alert: RuntimeAlert) {
    if (ackBusy.current) return;
    ackBusy.current = true; setAcknowledging(alert.id);
    try {
      await withRefreshedToken((token) => acknowledgeRuntimeAlert(token, alert));
      notify.success("已确认阅读；业务状态保持原有事实。");
    } catch (cause) { notify.error(cause, "确认失败，请刷新后重试"); }
    finally {
      ackBusy.current = false;
      if (mounted.current) { setAcknowledging(null); await latestLoad.current(); }
    }
  }

  const columns: AppTableColumn<RuntimeAlert>[] = [
    { title: "级别", key: "severity", render: (_, row) => <StatusBadge value={row.severity === "warning" ? "pending" : row.severity}
      label={row.severity === "critical" ? "严重" : row.severity === "warning" ? "警告" : "提示"} /> },
    { title: "告警", key: "rule", render: (_, row) => <div className="max-w-sm"><p>{row.title}</p><p className="text-xs text-muted-foreground">{row.rule_code}</p></div> },
    { title: "最近观测状态", key: "status", render: (_, row) => <div className="space-y-1"><StatusBadge
      value={row.is_active && !row.acknowledged_at ? "pending" : "unknown"}
      label={!row.is_active ? "条件不再命中" : row.acknowledged_at ? "已阅，条件仍命中" : "未读，条件命中"} />
      {row.is_stale && <p className="text-xs text-muted-foreground">观测过期，当前未知</p>}</div> },
    { title: "最近扫描", key: "observed", render: (_, row) => new Date(row.last_observed_at).toLocaleString() },
    { title: "来源", key: "scope", render: (_, row) => <p className="max-w-xs break-all text-xs">{row.scope_kind} · {row.scope_id}</p> },
    { title: "操作", key: "actions", align: "right", render: (_, row) => <div className="flex justify-end gap-2">
      <Button size="sm" variant="ghost" onClick={() => setDetail(row)}>查看详情</Button>
      {row.is_active && !row.acknowledged_at && <Button size="sm" variant="outline" disabled={acknowledging !== null}
        onClick={() => void acknowledge(row)}>{acknowledging === row.id ? "确认中…" : "确认已阅"}</Button>}
    </div> },
  ];
  return <div className="space-y-6">
    <PageHeader title="运行告警" description="按持久化事实查看预算、租约、恢复、对账、授权与观测异常；已阅不代表修复。" />
    {error && <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-red-600"><span>{error}</span><Button variant="outline" size="sm" onClick={() => void load()}>重试</Button></div>}
    {!loading && result && <p role="status" className="text-sm text-muted-foreground">{result.scan_stale
      ? "扫描尚未运行或已过期，空列表不能证明系统正常。" : "扫描任务近期有成功批次，逐条来源可能仍待下一轮扫描。"}
      {result.scan_observed_at && ` 最近成功批次：${new Date(result.scan_observed_at).toLocaleString()}`}</p>}
    <section className="rounded-2xl bg-white">
      <ListToolbar loading={loading} onRefresh={() => void load()} filters={<>
        <Select value={state} onValueChange={(value) => { setState(value as StateFilter); setPage((old) => ({ ...old, current: 1 })); }}>
          <SelectTrigger className="w-44" aria-label="告警状态"><SelectValue /></SelectTrigger><SelectContent>
            <SelectItem value="unacknowledged">未读且条件命中</SelectItem><SelectItem value="acknowledged">已阅且条件命中</SelectItem>
            <SelectItem value="resolved">条件不再命中</SelectItem><SelectItem value="all">全部状态</SelectItem>
          </SelectContent></Select>
        <Select value={severity} onValueChange={(value) => { setSeverity(value as RuntimeAlert["severity"] | "all"); setPage((old) => ({ ...old, current: 1 })); }}>
          <SelectTrigger className="w-32" aria-label="告警级别"><SelectValue /></SelectTrigger><SelectContent>
            <SelectItem value="all">全部级别</SelectItem><SelectItem value="info">提示</SelectItem><SelectItem value="warning">警告</SelectItem><SelectItem value="critical">严重</SelectItem>
          </SelectContent></Select>
        <Select value={rule} onValueChange={(value) => { setRule(value); setPage((old) => ({ ...old, current: 1 })); }}>
          <SelectTrigger className="w-44" aria-label="告警规则"><SelectValue /></SelectTrigger><SelectContent>
            <SelectItem value="all">全部规则</SelectItem>{Object.entries(rules).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
          </SelectContent></Select>
      </>} />
      <div className="relative min-h-48"><AppTable columns={columns} dataSource={result?.items ?? []} rowKey="id" ariaLabel="运行告警列表"
        emptyText={error ? "告警数据未能读取" : "当前条件没有已观测告警"} pagination={{ ...page, total: result?.total ?? 0,
          onChange: (current, pageSize) => setPage({ current, pageSize }) }} />
        {loading && <ListLoadingOverlay label="正在读取运行告警…" />}
      </div>
    </section>
    <RuntimeAlertDetail alert={detail} onClose={() => setDetail(null)} />
  </div>;
}
