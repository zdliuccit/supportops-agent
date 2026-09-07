import type { ComponentProps } from "react";
import { useTheme } from "next-themes";
import { Check, TriangleAlert, X } from "lucide-react";
import { Toaster as Sonner } from "sonner";

type ToasterProps = ComponentProps<typeof Sonner>;

const Toaster = ({ ...props }: ToasterProps) => {
  const { theme = "system" } = useTheme();

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      position="top-right"
      closeButton
      className="toaster group"
      icons={{
        success: (
          <Check className="size-4 rounded-full bg-[#22c55e] p-0.5 text-white" strokeWidth={3} />
        ),
        error: (
          <X className="size-4 rounded-full bg-[#ef4444] p-0.5 text-white" strokeWidth={3} />
        ),
        warning: (
          <TriangleAlert className="size-4 text-[#f59e0b]" strokeWidth={2.5} />
        ),
      }}
      toastOptions={{
        closeButtonAriaLabel: "关闭通知",
        classNames: {
          toast: "group toast rounded-lg border-0! bg-white! text-[#1c252e]! shadow-lg",
          success: "!border-0 !bg-white !text-[#1c252e]",
          error: "!border-0 !bg-white !text-[#1c252e]",
          warning: "!border-0 !bg-white !text-[#1c252e]",
          description: "group-[.toast]:text-muted-foreground",
          closeButton:
            "!left-auto !right-0 !border-[#dfe3e8] !bg-white !text-[#637381] hover:!bg-[#f4f6f8]",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
        },
      }}
      {...props}
    />
  );
};

export { Toaster };
