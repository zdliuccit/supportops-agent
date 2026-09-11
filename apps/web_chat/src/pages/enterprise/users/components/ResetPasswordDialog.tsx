import type { FormEvent } from "react";
import { FormField } from "@/components/FormField";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import type { AdminUser } from "@/types";

export function ResetPasswordDialog({ target, password, error, busy, onOpenChange, onSubmit, onPasswordChange, onCancel }: { target: AdminUser | null; password: string; error?: string; busy: boolean; onOpenChange: (open: boolean) => void; onSubmit: (event: FormEvent) => void; onPasswordChange: (value: string) => void; onCancel: () => void }) {
  return <Dialog open={target !== null} onOpenChange={onOpenChange}><DialogContent><form onSubmit={onSubmit} noValidate><DialogHeader><DialogTitle>重置用户密码</DialogTitle><DialogDescription>为 {target?.display_name} 设置新的登录密码，旧密码会立即失效。</DialogDescription></DialogHeader><FormField label="新密码" htmlFor="reset-user-password" required error={error}><Input id="reset-user-password" type="password" value={password} onChange={(event) => onPasswordChange(event.target.value)} placeholder="请输入新密码" autoFocus aria-invalid={Boolean(error)} aria-describedby={error ? "reset-user-password-error" : undefined} /></FormField><DialogFooter className="mt-6"><Button type="button" variant="outline" onClick={onCancel}>取消</Button><Button type="submit" disabled={busy}>确认重置</Button></DialogFooter></form></DialogContent></Dialog>;
}
