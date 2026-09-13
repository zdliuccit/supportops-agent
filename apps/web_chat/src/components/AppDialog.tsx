/**
 * 业务弹窗统一入口。
 *
 * 具体布局和遮罩行为由 shadcn/Radix 基础组件集中实现；业务页面统一
 * 从此文件引入，避免不同弹窗各自维护高度、滚动和按钮规范。
 */
export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogBody,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

export {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
