import { cn } from "@/lib/utils";

/** SupportOps 自有品牌组合标志，可在登录页和后台导航中复用。 */
export function BrandLogo({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-3", className)}>
      <img src="/supportops-mark.svg" alt="" className="size-11 shrink-0" />
      <span className="text-[19px] font-bold tracking-[-0.35px] text-[#1c252e]">
        SupportOps
      </span>
    </span>
  );
}
