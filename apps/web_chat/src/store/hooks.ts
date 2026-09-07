import { useDispatch, useSelector } from "react-redux";

import type { AppDispatch, RootState } from "@/store";

/** 带有应用 Dispatch 类型的 Redux Hook。 */
export const useAppDispatch = useDispatch.withTypes<AppDispatch>();

/** 带有根状态类型的 Redux Selector Hook。 */
export const useAppSelector = useSelector.withTypes<RootState>();
