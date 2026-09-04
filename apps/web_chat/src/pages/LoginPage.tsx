import { useState, type FormEvent } from "react";
import { Eye, EyeOff, Headphones, LoaderCircle, LockKeyhole, Network, ShieldCheck } from "lucide-react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { login } from "@/api";
import { BrandLogo } from "@/components/BrandLogo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getStoredAccessToken, saveAccessToken } from "@/lib/auth";

interface LoginFieldErrors {
  email?: string;
  password?: string;
}

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function validateEmail(value: string): string | undefined {
  const normalized = value.trim();
  if (!normalized) return "请输入邮箱地址";
  if (!EMAIL_PATTERN.test(normalized)) return "请输入有效的邮箱地址";
  return undefined;
}

function validatePassword(value: string): string | undefined {
  return value ? undefined : "请输入密码";
}

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("zdliuccit@gmail.com");
  const [password, setPassword] = useState("ZDLIU@246810jia");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<LoginFieldErrors>({});

  if (getStoredAccessToken()) return <Navigate to="/agents" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const nextErrors: LoginFieldErrors = {
      email: validateEmail(email),
      password: validatePassword(password),
    };
    setFieldErrors(nextErrors);
    if (nextErrors.email || nextErrors.password) return;
    setBusy(true);
    setError(null);
    try {
      const result = await login(email, password);
      saveAccessToken(result.access_token);
      const target = (location.state as { from?: string } | null)?.from ?? "/agents";
      navigate(target, { replace: true });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "登录失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  return <main className="min-h-svh bg-white text-[#1c252e] lg:grid lg:grid-cols-[580px_minmax(0,1fr)]">
    <section className="relative hidden min-h-svh overflow-hidden bg-[#f6f8fa] px-12 py-10 lg:flex lg:flex-col">
      <BrandLogo className="absolute left-6 top-6" />
      <div className="h-11 shrink-0" aria-hidden="true" />
      <div className="relative z-10 mx-auto mt-[14vh] w-full max-w-md text-center">
        <h1 className="text-[34px] font-bold tracking-[-0.8px]">欢迎回来</h1>
        <p className="mt-4 text-base text-[#637381]">让企业技术支持、知识检索与工单处置更高效。</p>
        <div className="relative mx-auto mt-16 h-80 max-w-sm" aria-hidden="true">
          <div className="absolute inset-x-10 bottom-4 h-40 rounded-[50%] bg-emerald-200/30 blur-3xl" />
          <div className="absolute left-6 top-10 h-52 w-64 rounded-[28px] border border-white bg-white/90 p-5 text-left shadow-[0_24px_80px_rgba(28,37,46,.12)]">
            <div className="flex items-center gap-2"><span className="size-2 rounded-full bg-red-400" /><span className="size-2 rounded-full bg-amber-400" /><span className="size-2 rounded-full bg-emerald-400" /></div>
            <div className="mt-7 h-2 w-24 rounded-full bg-slate-300" />
            <div className="mt-7 flex h-24 items-end gap-2 rounded-2xl bg-emerald-50 p-4">{[35, 54, 42, 75, 60, 92].map((height, index) => <span key={index} className="flex-1 rounded-t-md bg-emerald-400" style={{ height: `${height}%` }} />)}</div>
          </div>
          <div className="absolute right-2 top-0 grid h-36 w-32 place-items-center rounded-[26px] border border-white bg-white shadow-xl"><Network className="size-14 text-emerald-500" strokeWidth={1.4} /></div>
          <div className="absolute bottom-0 right-0 w-44 rounded-[24px] border border-white bg-white p-5 text-left shadow-xl"><ShieldCheck className="size-8 text-emerald-500" /><div className="mt-4 h-2 w-24 rounded-full bg-slate-300" /><div className="mt-2 h-2 w-16 rounded-full bg-slate-200" /></div>
        </div>
      </div>
      <div className="mt-auto flex justify-center gap-7 text-slate-400"><ShieldCheck /><Network /><Headphones /><LockKeyhole /></div>
    </section>
    <section className="relative flex min-h-svh items-center justify-center px-6 py-16">
      <div className="absolute left-6 top-6 lg:hidden"><BrandLogo /></div>
      <div className="w-full max-w-[480px]">
        <h2 className="text-[30px] font-bold tracking-[-0.5px]">登录您的账户</h2>
        <p className="mt-3 text-sm text-[#637381]">使用公司管理员创建的账号进入 SupportOps。</p>
        <div className="mt-9 rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-900"><strong>企业安全登录</strong><span className="ml-2 text-emerald-700">账号由平台管理员统一管理</span></div>
        <form onSubmit={submit} className="mt-6 space-y-5" noValidate>
          <label className="block text-sm font-medium">
            邮箱地址
            <Input
              className="mt-2 h-14 border-[#dfe3e8] bg-white px-4 text-[15px] focus-visible:ring-emerald-500"
              type="email"
              value={email}
              placeholder="请输入邮箱地址"
              onChange={(event) => {
                const value = event.target.value;
                setEmail(value);
                setError(null);
                if (fieldErrors.email) {
                  setFieldErrors((current) => ({ ...current, email: validateEmail(value) }));
                }
              }}
              onBlur={() =>
                setFieldErrors((current) => ({ ...current, email: validateEmail(email) }))
              }
              autoComplete="username"
              aria-invalid={Boolean(fieldErrors.email)}
              aria-describedby={fieldErrors.email ? "login-email-error" : undefined}
              required
              autoFocus
            />
            {fieldErrors.email && (
              <span id="login-email-error" className="mt-1.5 block text-xs font-normal text-red-600" aria-live="polite">
                {fieldErrors.email}
              </span>
            )}
          </label>
          <label className="block text-sm font-medium">
            密码
            <span className="relative mt-2 block">
              <Input
                className="h-14 border-[#dfe3e8] bg-white px-4 pr-12 text-[15px] focus-visible:ring-emerald-500"
                type={showPassword ? "text" : "password"}
                value={password}
                placeholder="请输入密码"
                onChange={(event) => {
                  const value = event.target.value;
                  setPassword(value);
                  setError(null);
                  if (fieldErrors.password) {
                    setFieldErrors((current) => ({
                      ...current,
                      password: validatePassword(value),
                    }));
                  }
                }}
                onBlur={() =>
                  setFieldErrors((current) => ({
                    ...current,
                    password: validatePassword(password),
                  }))
                }
                autoComplete="current-password"
                aria-invalid={Boolean(fieldErrors.password)}
                aria-describedby={fieldErrors.password ? "login-password-error" : undefined}
                required
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => setShowPassword((value) => !value)}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full text-slate-500 hover:bg-slate-100 [&_svg]:size-5"
                aria-label={showPassword ? "隐藏密码" : "显示密码"}
              >
                {showPassword ? <EyeOff /> : <Eye />}
              </Button>
            </span>
            {fieldErrors.password && (
              <span id="login-password-error" className="mt-1.5 block text-xs font-normal text-red-600" aria-live="polite">
                {fieldErrors.password}
              </span>
            )}
          </label>
          {error && <div role="alert" aria-live="polite" className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
          <Button type="submit" className="h-12 w-full rounded-lg bg-[#1c252e] text-base font-semibold text-white shadow-none hover:bg-[#2b3742]" disabled={busy}>{busy ? <><LoaderCircle className="animate-spin" />登录中…</> : "登录"}</Button>
        </form>
        <p className="mt-8 text-center text-xs leading-5 text-[#919eab]">登录即表示您同意遵守企业信息安全与数据使用规范。</p>
      </div>
    </section>
  </main>;
}
