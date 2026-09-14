import {
  useEffect,
  useState,
  type FormEvent,
  type Key,
  type ReactNode,
} from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

/** 统一表格支持的列对齐方式。 */
type AppTableColumnAlign = "left" | "center" | "right";
type AppTableColumnFixed = "left" | "right";

/** 统一表格内置的每页条数选项，业务页面无需重复配置。 */
const DEFAULT_PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const;

/**
 * 轻量列定义，调用方式与 Ant Design Table 保持相近。
 *
 * 当前只提供后台列表已使用的字段读取、自定义渲染、对齐和列宽能力。
 */
export interface AppTableColumn<T> {
  /** 表头内容。 */
  title: ReactNode;
  /** 从数据记录直接读取的字段。 */
  dataIndex?: keyof T;
  /** 列的稳定标识；未传时使用 dataIndex。 */
  key?: Key;
  /** 自定义单元格内容。 */
  render?: (value: unknown, record: T, index: number) => ReactNode;
  /** 表头和单元格的文字对齐方式。 */
  align?: AppTableColumnAlign;
  /** 可选列宽。 */
  width?: number | string;
  /** 列固定在横向滚动区域的左侧或右侧。 */
  fixed?: AppTableColumnFixed;
  /** 列的最低宽度，默认 140px，避免窄屏时表头换行。 */
  minWidth?: number | string;
}

/** 统一表格参数。 */
export interface AppTableProps<T> {
  /** 表格列定义。 */
  columns: AppTableColumn<T>[];
  /** 表格数据。 */
  dataSource: T[];
  /** 行唯一键字段或计算函数。 */
  rowKey: keyof T | ((record: T) => Key);
  /** 客户端分页配置；传 false 或不传时不展示分页。 */
  pagination?: false | AppTablePaginationConfig;
  /** 空数据时展示的内容，默认“暂无数据”。 */
  emptyText?: ReactNode;
  /** 表格无障碍名称。 */
  ariaLabel?: string;
  /** 外层滚动容器样式。 */
  className?: string;
}

/** 当前统一表格支持的轻量客户端分页配置。 */
export interface AppTablePaginationConfig {
  /** 当前页；不传时由表格内部维护。 */
  current?: number;
  /** 每页数据数量，默认 10。 */
  pageSize?: number;
  /** 服务端分页的数据总数；传入后 dataSource 视为当前页数据，不再由组件切片。 */
  total?: number;
  /** 页码变化回调。 */
  onChange?: (page: number, pageSize: number) => void;
  /** 是否显示数据总数，默认显示。 */
  showTotal?: boolean;
  /** 是否显示每页条数选择，默认显示。 */
  showSizeChanger?: boolean;
  /** 是否显示指定页码跳转，默认显示。 */
  showQuickJumper?: boolean;
}

/** 分页栏中的页码或省略标识。 */
type AppTablePaginationItem = number | "start-ellipsis" | "end-ellipsis";

/** 控制页码按钮数量，避免数据量较大时渲染过长的分页栏。 */
function buildPaginationItems(pageCount: number, current: number): AppTablePaginationItem[] {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, index) => index + 1);
  if (current <= 4) return [1, 2, 3, 4, 5, "end-ellipsis", pageCount];
  if (current >= pageCount - 3) {
    return [
      1,
      "start-ellipsis",
      pageCount - 4,
      pageCount - 3,
      pageCount - 2,
      pageCount - 1,
      pageCount,
    ];
  }
  return [
    1,
    "start-ellipsis",
    current - 1,
    current,
    current + 1,
    "end-ellipsis",
    pageCount,
  ];
}

function alignmentClassName(align: AppTableColumnAlign | undefined): string {
  if (align === "center") return "text-center";
  if (align === "right") return "text-right";
  return "text-left";
}

const DEFAULT_COLUMN_MIN_WIDTH = 140;

function widthInPixels(column: AppTableColumn<unknown>): number {
  if (typeof column.minWidth === "number") return column.minWidth;
  if (typeof column.width === "number") return column.width;
  return DEFAULT_COLUMN_MIN_WIDTH;
}

function fixedOffset(
  columns: AppTableColumn<unknown>[],
  index: number,
  side: AppTableColumnFixed,
): number {
  if (side === "left") {
    return columns
      .slice(0, index)
      .filter((column) => column.fixed === "left")
      .reduce((total, column) => total + widthInPixels(column), 0);
  }
  return columns
    .slice(index + 1)
    .filter((column) => column.fixed === "right")
    .reduce((total, column) => total + widthInPixels(column), 0);
}

function columnStyle<T>(column: AppTableColumn<T>, index: number, columns: AppTableColumn<T>[]) {
  const style: { width?: number | string; minWidth?: number | string; left?: number; right?: number } = {
    width: column.width,
    minWidth: column.minWidth ?? DEFAULT_COLUMN_MIN_WIDTH,
  };
  if (column.fixed) {
    style[column.fixed] = fixedOffset(columns as AppTableColumn<unknown>[], index, column.fixed);
  }
  return style;
}

function fixedClassName<T>(column: AppTableColumn<T>, index: number, columns: AppTableColumn<T>[]): string {
  if (!column.fixed) return "";
  const isBoundary = column.fixed === "left"
    ? columns[index + 1]?.fixed !== "left"
    : columns[index - 1]?.fixed !== "right";
  return cn(
    "sticky z-10",
    isBoundary && column.fixed === "left" && "relative after:pointer-events-none after:absolute after:inset-y-0 after:right-[-14px] after:w-[14px] after:bg-gradient-to-r after:from-slate-900/5 after:to-transparent",
    isBoundary && column.fixed === "right" && "relative before:pointer-events-none before:absolute before:inset-y-0 before:left-[-14px] before:w-[14px] before:bg-gradient-to-l before:from-slate-900/5 before:to-transparent",
  );
}

/** 后台数据列表统一表格。 */
export function AppTable<T>({
  columns,
  dataSource,
  rowKey,
  pagination = false,
  emptyText = "暂无数据",
  ariaLabel,
  className,
}: AppTableProps<T>) {
  const [internalCurrent, setInternalCurrent] = useState(1);
  const [internalPageSize, setInternalPageSize] = useState(10);
  const [jumpPage, setJumpPage] = useState("");
  const paginationEnabled = pagination !== false;
  const controlledPageSize = pagination === false ? undefined : pagination.pageSize;
  const pageSize = pagination === false
    ? Math.max(dataSource.length, 1)
    : Math.max(controlledPageSize ?? internalPageSize, 1);
  const total = pagination === false ? dataSource.length : (pagination.total ?? dataSource.length);
  const serverSidePagination = pagination !== false && pagination.total !== undefined;
  const pageCount = Math.max(Math.ceil(total / pageSize), 1);
  const controlledCurrent = pagination === false ? undefined : pagination.current;
  const requestedCurrent = pagination === false ? 1 : (controlledCurrent ?? internalCurrent);
  const current = Math.min(Math.max(requestedCurrent, 1), pageCount);
  const startIndex = pagination === false ? 0 : (current - 1) * pageSize;
  const pageData = pagination === false || serverSidePagination
    ? dataSource
    : dataSource.slice(startIndex, startIndex + pageSize);
  const paginationOnChange = pagination === false ? undefined : pagination.onChange;
  const pageSizeOptions = pagination === false
    ? []
    : Array.from(new Set([...DEFAULT_PAGE_SIZE_OPTIONS, pageSize]))
      .filter((option) => option > 0)
      .sort((left, right) => left - right);
  const paginationItems = buildPaginationItems(pageCount, current);

  useEffect(() => {
    if (!paginationEnabled || requestedCurrent === current) return;
    if (controlledCurrent === undefined) setInternalCurrent(current);
    else paginationOnChange?.(current, pageSize);
  }, [
    controlledCurrent,
    current,
    pageSize,
    paginationEnabled,
    paginationOnChange,
    requestedCurrent,
  ]);

  function changePage(page: number) {
    if (pagination === false) return;
    const nextPage = Math.min(Math.max(page, 1), pageCount);
    if (pagination.current === undefined) setInternalCurrent(nextPage);
    pagination.onChange?.(nextPage, pageSize);
  }

  function changePageSize(value: string) {
    if (pagination === false) return;
    const nextPageSize = Number(value);
    if (!Number.isInteger(nextPageSize) || nextPageSize <= 0) return;
    if (controlledPageSize === undefined) setInternalPageSize(nextPageSize);
    if (controlledCurrent === undefined) setInternalCurrent(1);
    setJumpPage("");
    pagination.onChange?.(1, nextPageSize);
  }

  function submitJump(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const targetPage = Number(jumpPage);
    if (!Number.isInteger(targetPage) || targetPage <= 0) return;
    changePage(targetPage);
    setJumpPage("");
  }

  return (
    <div className={cn("w-full", className)}>
      <Table className="min-w-[960px]" aria-label={ariaLabel}>
        <TableHeader className="[&_tr]:border-0">
          <TableRow className="border-0 bg-[#f4f6f8] hover:bg-[#f4f6f8]">
            {columns.map((column, index) => (
              <TableHead
                key={column.key ?? (column.dataIndex !== undefined ? String(column.dataIndex) : index)}
                className={cn(
                  "h-auto whitespace-nowrap px-6 py-4 text-sm font-semibold text-[#637381]",
                  alignmentClassName(column.align),
                  fixedClassName(column, index, columns),
                  column.fixed && "bg-[#f4f6f8]",
                )}
                style={columnStyle(column, index, columns)}
              >
                {column.title}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {pageData.length === 0 ? (
            <TableRow className="border-0 hover:bg-transparent">
              <TableCell
                colSpan={Math.max(columns.length, 1)}
                className="px-6 py-16 text-center text-sm text-[#919eab]"
              >
                {emptyText}
              </TableCell>
            </TableRow>
          ) : pageData.map((record, rowIndex) => {
            const recordKey = typeof rowKey === "function" ? rowKey(record) : record[rowKey];
            return (
              <TableRow key={String(recordKey)} className="group border-dashed border-[#e6e8eb] hover:bg-[#fafbfc]">
                {columns.map((column, columnIndex) => {
                  const value = column.dataIndex !== undefined ? record[column.dataIndex] : undefined;
                  return (
                    <TableCell
                      key={column.key ?? (column.dataIndex !== undefined ? String(column.dataIndex) : columnIndex)}
                      className={cn("bg-white px-6 py-4", alignmentClassName(column.align), fixedClassName(column, columnIndex, columns))}
                      style={columnStyle(column, columnIndex, columns)}
                    >
                      {column.render ? column.render(value, record, startIndex + rowIndex) : String(value ?? "")}
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {pagination !== false && total > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[#e6e8eb] py-4 pl-0 pr-6">
          <span className="text-sm text-[#637381]">
            {pagination.showTotal === false ? null : `共 ${total} 条`}
          </span>
          <div className="flex flex-wrap items-center justify-end gap-3" aria-label="表格分页">
            {pagination.showSizeChanger !== false && (
              <Select value={String(pageSize)} onValueChange={changePageSize}>
                <SelectTrigger className="h-9 w-max min-w-[104px] shrink-0 gap-2 [&>svg]:shrink-0" aria-label="每页显示条数">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {pageSizeOptions.map((option) => (
                    <SelectItem key={option} value={String(option)}>{option} 条/页</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => changePage(current - 1)}
                disabled={current <= 1}
                aria-label="上一页"
              >
                <ChevronLeft />
              </Button>
              {paginationItems.map((item) => typeof item === "number" ? (
                <Button
                  key={item}
                  type="button"
                  variant="ghost"
                  size="icon"
                  className={cn(
                    item === current
                      && "bg-emerald-50 text-emerald-700 hover:bg-emerald-100 hover:text-emerald-800",
                  )}
                  onClick={() => changePage(item)}
                  aria-label={`第 ${item} 页`}
                  aria-current={item === current ? "page" : undefined}
                >
                  {item}
                </Button>
              ) : (
                <span
                  key={item}
                  className="grid size-9 place-items-center text-sm text-[#919eab]"
                  aria-hidden
                >
                  …
                </span>
              ))}
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => changePage(current + 1)}
                disabled={current >= pageCount}
                aria-label="下一页"
              >
                <ChevronRight />
              </Button>
            </div>
            {pagination.showQuickJumper !== false && (
              <form className="flex items-center gap-2 text-sm text-[#637381]" onSubmit={submitJump}>
                <span>前往</span>
                <Input
                  className="h-9 w-16 px-2 text-center"
                  value={jumpPage}
                  onChange={(event) => setJumpPage(event.target.value.replace(/\D/g, ""))}
                  inputMode="numeric"
                  min={1}
                  max={pageCount}
                  aria-label="跳转页码"
                />
                <span>页</span>
                <Button type="submit" variant="outline" size="sm" disabled={!jumpPage}>
                  跳转
                </Button>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
