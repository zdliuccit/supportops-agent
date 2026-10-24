import { useLayoutEffect, useRef } from "react";
import { notify } from "@/lib/notifications";

/** 提交时控件全部禁用，主动聚焦容器，防止焦点落到弹窗外。 */
export function useKnowledgeBusyDialogFocus(busy: boolean) {
  const contentRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { if (busy) contentRef.current?.focus(); }, [busy]);
  return contentRef;
}

/** 受控弹窗没有 Radix Trigger；关闭时回原按钮，原操作消失时回当前知识区域。 */
export function useKnowledgeDialogFocus() {
  const trigger = useRef<HTMLElement | null>(null);
  const fallbackRef = useRef<HTMLDivElement>(null);
  function remember(element: HTMLElement) {
    // 新操作已承接上一成功结果，避免旧提示遮住短屏弹窗标题和关闭按钮。
    notify.dismissSuccess();
    trigger.current = element;
  }
  function restore(event: Event) {
    event.preventDefault();
    const original = trigger.current;
    const target = original?.isConnected && !original.matches(":disabled") ? original : fallbackRef.current;
    if (target?.isConnected) target.focus({ preventScroll: true });
  }
  return { remember, restore, fallbackRef };
}
