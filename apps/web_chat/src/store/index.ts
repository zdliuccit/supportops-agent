import { configureStore } from "@reduxjs/toolkit";

import { organizationUnitsReducer } from "@/store/organizationUnitsSlice";

/** Web 应用全局 Redux Store。 */
export const store = configureStore({
  reducer: {
    organizationUnits: organizationUnitsReducer,
  },
});

/** 全局 Redux 根状态类型。 */
export type RootState = ReturnType<typeof store.getState>;

/** 全局 Redux Dispatch 类型，包含异步 Thunk。 */
export type AppDispatch = typeof store.dispatch;
