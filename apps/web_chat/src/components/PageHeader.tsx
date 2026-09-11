import type { ReactNode } from "react";

interface PageHeaderProps {
  /** 当前功能页面的主标题。 */
  title: string;
  /** 对当前页面用途的简短说明。 */
  description?: string;
  /** 显示在页面标题右侧的主要操作，例如新增按钮。 */
  actions?: ReactNode;
}

/** 管理后台内容区域统一使用的页面标题。 */
export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <div className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-2xl font-bold tracking-[-.4px]">{title}</h1>
        {description && <p className="mt-2 text-sm text-[#919eab]">{description}</p>}
      </div>
      {actions && <div className="shrink-0">{actions}</div>}
    </div>
  );
}
