import * as React from "react";

import { cn } from "@/lib/utils";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        "flex min-h-16 w-full resize-none rounded-lg border border-input bg-background px-3 py-2 text-base outline-none placeholder:text-muted-foreground transition-shadow focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20 aria-invalid:border-red-500! aria-invalid:focus-visible:border-red-500! aria-invalid:focus-visible:ring-red-200! disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
