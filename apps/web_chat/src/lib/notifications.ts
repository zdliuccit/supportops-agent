import { toast, type ExternalToast } from "sonner";

/** 从未知异常中提取适合展示给用户的操作失败文案。 */
function errorMessage(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

/** 全局操作反馈入口；页面不得直接拼装不同样式的成功、失败或警告提示。 */
export const notify = {
  /** 展示绿色成功提示。 */
  success(message: string, options?: ExternalToast) {
    return toast.success(message, options);
  },
  /** 展示红色失败提示，并优先保留服务端返回的错误信息。 */
  error(cause: unknown, fallback: string, options?: ExternalToast) {
    return toast.error(errorMessage(cause, fallback), options);
  },
  /** 展示黄色警告提示，用于操作完成但需要用户注意的结果。 */
  warning(message: string, options?: ExternalToast) {
    return toast.warning(message, options);
  },
};
