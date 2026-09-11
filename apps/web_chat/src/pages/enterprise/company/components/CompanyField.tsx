import type { ReactNode } from "react";

/** 公司信息页面的只读字段展示单元。 */
export function CompanyField({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt className="text-xs text-[#919eab]">{label}</dt><dd className="mt-2 min-h-6 text-sm text-[#1c252e]">{children}</dd></div>;
}
