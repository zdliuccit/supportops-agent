import { Switch } from "@/components/ui/switch";

/** 用户管理弹窗中的统一二态开关字段。 */
export function SwitchField({ label, description, checked, onChange }: { label: string; description: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <div className="flex min-h-12 items-center justify-between gap-4 border-b border-[#eef1f4] py-2.5">
      <div className="min-w-0">
        <div className="text-sm font-medium text-[#1c252e]">{label}</div>
        <div className="mt-1 text-xs text-[#919eab]">{description}</div>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} aria-label={label} />
    </div>
  );
}
