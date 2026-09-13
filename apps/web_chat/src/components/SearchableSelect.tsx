import { useEffect, useRef, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";

import { GlobalLoading } from "@/components/GlobalLoading";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

export interface SearchableSelectOption {
  value: string;
  label: string;
  description?: string;
}

export interface SearchableSelectProps {
  value: string;
  onValueChange: (value: string) => void;
  loadOptions: (keywords: string, pageSize: number) => Promise<SearchableSelectOption[]>;
  /** 当受控 value 不在首屏列表时，用 ID 获取其显示信息。 */
  resolveOption?: (value: string) => Promise<SearchableSelectOption | null>;
  pageSize?: number;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyText?: string;
  clearLabel?: string;
  disabled?: boolean;
  className?: string;
}

/** 支持服务端关键字搜索的下拉选择器，默认缓存第一页 20 条。 */
export function SearchableSelect({
  value,
  onValueChange,
  loadOptions,
  resolveOption,
  pageSize = 20,
  placeholder = "请选择",
  searchPlaceholder = "请输入关键字搜索",
  emptyText = "暂无匹配数据",
  clearLabel = "全部",
  disabled = false,
  className,
}: SearchableSelectProps) {
  const [open, setOpen] = useState(false);
  const [keywords, setKeywords] = useState("");
  const [options, setOptions] = useState<SearchableSelectOption[]>([]);
  const [selectedOption, setSelectedOption] = useState<SearchableSelectOption | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const requestId = useRef(0);
  const defaultOptions = useRef<SearchableSelectOption[]>([]);
  const defaultLoaded = useRef(false);
  const inputChangedSinceOpen = useRef(false);
  const resolveRequestId = useRef(0);

  useEffect(() => {
    defaultOptions.current = [];
    defaultLoaded.current = false;
    setOptions([]);
  }, [loadOptions, pageSize]);

  useEffect(() => {
    if (!value || options.some((option) => option.value === value) || !resolveOption) return undefined;
    const currentRequest = ++resolveRequestId.current;
    void resolveOption(value).then((resolved) => {
      if (currentRequest === resolveRequestId.current && resolved) setSelectedOption(resolved);
    }).catch(() => undefined);
    return () => { resolveRequestId.current += 1; };
  }, [options, resolveOption, value]);

  useEffect(() => {
    if (!value) {
      setSelectedOption(null);
      return;
    }
    const nextSelected = options.find((option) => option.value === value);
    if (nextSelected) setSelectedOption(nextSelected);
  }, [options, value]);

  useEffect(() => {
    if (!open) return undefined;
    const normalizedKeywords = keywords.trim();

    if (!normalizedKeywords && defaultLoaded.current && !inputChangedSinceOpen.current) {
      requestId.current += 1;
      setOptions(defaultOptions.current);
      setLoading(false);
      setLoadError(null);
      return undefined;
    }

    // 关键字变化后立即显示 Loading，防抖只负责延迟请求，不得延迟反馈。
    setLoading(true);
    setLoadError(null);

    let cancelRequest: (() => void) | undefined;
    const load = () => {
      const currentRequest = ++requestId.current;
      setLoading(true);
      setLoadError(null);
      const requestTimer = window.setTimeout(() => {
        void loadOptions(normalizedKeywords, pageSize)
          .then((nextOptions) => {
            if (currentRequest !== requestId.current) return;
            setOptions(nextOptions);
            if (!normalizedKeywords) {
              defaultOptions.current = nextOptions;
              defaultLoaded.current = true;
              inputChangedSinceOpen.current = false;
            }
          })
          .catch(() => {
            if (currentRequest === requestId.current) setLoadError("搜索数据加载失败，请重试");
          })
          .finally(() => {
            if (currentRequest === requestId.current) setLoading(false);
          });
      }, 300);
      cancelRequest = () => window.clearTimeout(requestTimer);
      return cancelRequest;
    };

    if (!normalizedKeywords && !inputChangedSinceOpen.current) {
      return load();
    }

    const debounceTimer = window.setTimeout(load, 500);
    return () => {
      window.clearTimeout(debounceTimer);
      cancelRequest?.();
    };
  }, [keywords, loadOptions, open, pageSize]);

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen);
    if (!nextOpen) {
      requestId.current += 1;
      inputChangedSinceOpen.current = false;
      setKeywords("");
      setLoading(false);
      setLoadError(null);
      if (defaultLoaded.current) setOptions(defaultOptions.current);
    }
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" role="combobox" aria-expanded={open} disabled={disabled} className={cn("h-9 min-w-40 justify-between gap-2 font-normal", className)}>
          <span className={cn("truncate", !selectedOption && !value && "text-muted-foreground")}>{selectedOption?.label ?? (value || placeholder)}</span>
          <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[280px] p-0">
        <Command shouldFilter={false}>
          <CommandInput value={keywords} onValueChange={(nextKeywords) => { inputChangedSinceOpen.current = true; setKeywords(nextKeywords); }} placeholder={searchPlaceholder} />
          <CommandList>
            {loading && <GlobalLoading fullScreen={false} size="sm" label="正在加载选项…" className="py-4" />}
            {!loading && loadError && <div className="px-3 py-6 text-center text-sm text-destructive">{loadError}</div>}
            {!loading && !loadError && (
              <>
                <CommandItem value="__all__" onSelect={() => { onValueChange(""); handleOpenChange(false); }}>
                  <Check className={cn("size-4", value ? "opacity-0" : "opacity-100")} />{clearLabel}
                </CommandItem>
                {options.map((option) => (
                  <CommandItem key={option.value} value={option.value} onSelect={() => { setSelectedOption(option); onValueChange(option.value); handleOpenChange(false); }}>
                    <Check className={cn("size-4", value === option.value ? "opacity-100" : "opacity-0")} />
                    <span className="min-w-0"><span className="block truncate">{option.label}</span>{option.description && <span className="block truncate text-xs text-muted-foreground">{option.description}</span>}</span>
                  </CommandItem>
                ))}
                {options.length === 0 && <CommandEmpty>{emptyText}</CommandEmpty>}
              </>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
