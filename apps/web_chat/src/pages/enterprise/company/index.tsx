import { useEffect, useState, type FormEvent } from "react";
import { CircleAlert, LoaderCircle, Pencil } from "lucide-react";

import { getCompany, updateCompany } from "@/api";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { withRefreshedToken } from "@/lib/auth";
import { notify } from "@/lib/notifications";
import type { Company, CompanyUpdateInput } from "@/types";
import { CompanyEditDialog } from "./components/CompanyEditDialog";
import { CompanyField } from "./components/CompanyField";

/** 公司基础资料的独立管理页面。 */
export function CompanyInfoPage() {
  const [company, setCompany] = useState<Company | null>(null);
  const [companyForm, setCompanyForm] = useState<CompanyUpdateInput | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [companyOpen, setCompanyOpen] = useState(false);
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const result = await withRefreshedToken((token) => getCompany(token));
      setCompany(result.value);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载公司资料失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => void load(), []);

  function openCompanyDialog() {
    if (!company) return;
    setCompanyForm({
      name: company.name,
      slug: company.slug ?? "",
      logo_url: company.logo_url,
      contact_email: company.contact_email,
    });
    setError(null);
    setCompanyOpen(true);
  }

  function setDialogOpen(open: boolean) {
    if (busy) return;
    setCompanyOpen(open);
    if (!open) {
      setCompanyForm(null);
      setFormErrors({});
    }
  }

  function validateCompanyField(field: "name" | "slug" | "contact_email", value: string) {
    const message = field === "name"
      ? (value.trim() ? undefined : "请输入公司名称")
      : field === "slug"
        ? (!value.trim() ? "请输入公司简称" : !/^[a-z0-9][a-z0-9-]*$/.test(value.trim()) ? "公司简称只允许小写字母、数字和连字符" : undefined)
        : (value.trim() && !/^\S+@\S+\.\S+$/.test(value.trim()) ? "请输入有效的联系邮箱" : undefined);
    setFormErrors((current) => {
      const next = { ...current };
      if (message) next[field] = message;
      else delete next[field];
      return next;
    });
  }

  async function saveCompany(event: FormEvent) {
    event.preventDefault();
    const form = companyForm;
    if (!form) return;
    const errors: Record<string, string> = {};
    if (!form.name.trim()) errors.name = "请输入公司名称";
    if (!form.slug.trim()) errors.slug = "请输入公司简称";
    else if (!/^[a-z0-9][a-z0-9-]*$/.test(form.slug.trim())) errors.slug = "公司简称只允许小写字母、数字和连字符";
    if (form.contact_email && !/^\S+@\S+\.\S+$/.test(form.contact_email.trim())) errors.contact_email = "请输入有效的联系邮箱";
    setFormErrors(errors);
    if (Object.keys(errors).length > 0) return;
    setBusy(true);
    try {
      const result = await withRefreshedToken((token) => updateCompany(token, form));
      setCompany(result.value);
      setCompanyOpen(false);
      setCompanyForm(null);
      setFormErrors({});
      notify.success("公司资料已保存。");
    } catch (cause) {
      notify.error(cause, "保存公司资料失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="公司信息"
        description="查看并维护当前企业的基础资料与平台标识。"
        actions={<Button type="button" onClick={openCompanyDialog} disabled={!company || loading}><Pencil />编辑公司资料</Button>}
      />
      {error && (
        <Alert variant="destructive" className="mb-5">
          <CircleAlert className="size-5" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}
      {loading ? (
        <div className="grid h-64 place-items-center"><LoaderCircle className="animate-spin text-emerald-500" /></div>
      ) : company ? (
        <section>
          <div className="flex min-w-0 items-center gap-3">
            <span className="grid size-11 shrink-0 place-items-center overflow-hidden rounded-xl bg-emerald-50">
              <img src="/supportops-mark.svg" alt="" className="size-8" />
            </span>
            <div className="min-w-0">
              <h2 className="font-semibold">基础资料</h2>
              <p className="mt-1 text-xs text-[#919eab]">公司名称、简称、状态和联系方式。</p>
            </div>
          </div>
          <dl className="mt-6 grid gap-x-8 gap-y-5 border-t border-[#f0f2f4] pt-6 sm:grid-cols-2 xl:grid-cols-3">
            <CompanyField label="公司名称">{company.name}</CompanyField>
            <CompanyField label="公司简称">{company.slug || "未设置"}</CompanyField>
            <CompanyField label="公司状态">
              <Badge variant={company.status === "active" ? "success" : "muted"}>{company.status === "active" ? "正常" : company.status}</Badge>
            </CompanyField>
            <CompanyField label="联系邮箱">
              {company.contact_email ? <a href={`mailto:${company.contact_email}`} className="hover:text-emerald-600">{company.contact_email}</a> : "未设置"}
            </CompanyField>
            <CompanyField label="最近更新时间">{formatDateTime(company.updated_at)}</CompanyField>
          </dl>
        </section>
      ) : null}

      <CompanyEditDialog open={companyOpen} form={companyForm} errors={formErrors} busy={busy} onOpenChange={setDialogOpen} onSubmit={saveCompany} onChange={setCompanyForm} onBlur={validateCompanyField} onClearError={(field) => setFormErrors((current) => { const next = { ...current }; delete next[field]; return next; })} onCancel={() => setDialogOpen(false)} />
    </>
  );
}

/** 将服务端时间统一转换为当前浏览器所在时区的中文日期时间。 */
function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}
