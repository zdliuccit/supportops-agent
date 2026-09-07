import { type ReactNode } from "react";

import { Label } from "@/components/ui/label";

/** 只在存在错误时渲染字段错误，不为正常状态保留空白。 */
export function FieldError({ id, children }: { id: string; children?: ReactNode }) {
  if (!children) return null;
  return <span id={id} className="mt-1.5 block text-xs font-normal text-red-600" role="alert">{children}</span>;
}

/**
 * 统一的弹窗表单字段容器：负责标签、必填标识、控件和动态错误提示的布局。
 * 校验结果由表单提交逻辑传入 error；没有错误时不会渲染占位内容。
 */
export function FormField({
  label,
  htmlFor,
  required = false,
  error,
  hint,
  children,
  className,
}: {
  label: string;
  htmlFor?: string;
  required?: boolean;
  error?: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  const errorId = htmlFor ? `${htmlFor}-error` : undefined;
  return (
    <div className={className}>
      <Label htmlFor={htmlFor} className="block text-sm">
        {required && <span className="mr-1 text-red-600" aria-hidden="true">*</span>}
        {label}
      </Label>
      <div className="mt-2">{children}</div>
      {hint && !error && <p className="mt-1 text-xs text-[#919eab]">{hint}</p>}
      {error && errorId && <FieldError id={errorId}>{error}</FieldError>}
    </div>
  );
}
