interface PageHeaderProps {
  /** 当前功能页面的主标题。 */
  title: string;
  /** 对当前页面用途的简短说明。 */
  description?: string;
}

/** 管理后台内容区域统一使用的页面标题。 */
export function PageHeader({ title, description }: PageHeaderProps) {
  return (
    <div className="mb-7">
      <h1 className="text-2xl font-bold tracking-[-.4px]">{title}</h1>
      {description && <p className="mt-2 text-sm text-[#919eab]">{description}</p>}
    </div>
  );
}
