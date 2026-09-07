import { createAsyncThunk, createSlice } from "@reduxjs/toolkit";

import { listOrganizationUnits } from "@/api";
import { withRefreshedToken } from "@/lib/auth";
import type { OrganizationUnit } from "@/types";

/** 部门树异步请求状态。 */
export type OrganizationUnitsStatus = "idle" | "loading" | "succeeded" | "failed";

/** Redux 中集中维护的当前租户部门树状态。 */
export interface OrganizationUnitsState {
  /** 服务端返回的树形部门集合。 */
  items: OrganizationUnit[];
  /** 最近一次部门请求状态。 */
  status: OrganizationUnitsStatus;
  /** 最近一次部门请求失败原因。 */
  error: string | null;
}

interface OrganizationUnitsRootState {
  organizationUnits: OrganizationUnitsState;
}

const initialState: OrganizationUnitsState = {
  items: [],
  status: "idle",
  error: null,
};

/** 从服务端重新获取当前租户的完整部门树。 */
export const refreshOrganizationUnits = createAsyncThunk<
  OrganizationUnit[],
  void | { force?: boolean },
  { state: OrganizationUnitsRootState; rejectValue: string }
>(
  "organizationUnits/refresh",
  async (_, { rejectWithValue }) => {
    try {
      const result = await withRefreshedToken((token) => listOrganizationUnits(token));
      return result.value.items;
    } catch (cause) {
      return rejectWithValue(cause instanceof Error ? cause.message : "加载部门结构失败");
    }
  },
  {
    // React StrictMode 或多个部门选择器同时挂载时只保留一个进行中的请求。
    condition: (options, { getState }) =>
      options?.force === true || getState().organizationUnits.status !== "loading",
  },
);

const organizationUnitsSlice = createSlice({
  name: "organizationUnits",
  initialState,
  reducers: {
    /** 退出登录或身份失效时清除租户部门缓存。 */
    clearOrganizationUnits: () => initialState,
  },
  extraReducers: (builder) => {
    builder
      .addCase(refreshOrganizationUnits.pending, (state) => {
        state.status = "loading";
        state.error = null;
      })
      .addCase(refreshOrganizationUnits.fulfilled, (state, action) => {
        state.items = action.payload;
        state.status = "succeeded";
        state.error = null;
      })
      .addCase(refreshOrganizationUnits.rejected, (state, action) => {
        // condition 阻止的重复请求不应覆盖正在加载的状态。
        if (action.meta.condition) return;
        state.status = "failed";
        state.error = action.payload ?? action.error.message ?? "加载部门结构失败";
      });
  },
});

/** 清除当前租户部门缓存。 */
export const { clearOrganizationUnits } = organizationUnitsSlice.actions;

/** 部门树 Reducer。 */
export const organizationUnitsReducer = organizationUnitsSlice.reducer;

/** 读取全局部门树。 */
export const selectOrganizationUnits = (state: OrganizationUnitsRootState) =>
  state.organizationUnits.items;

/** 读取全局部门请求状态。 */
export const selectOrganizationUnitsStatus = (state: OrganizationUnitsRootState) =>
  state.organizationUnits.status;

/** 读取全局部门请求错误。 */
export const selectOrganizationUnitsError = (state: OrganizationUnitsRootState) =>
  state.organizationUnits.error;

/**
 * 根据末级部门 ID 返回从顶级部门开始的名称集合。
 *
 * 默认最多返回四级；ID 为空或不存在时返回空数组。
 */
export function getDepartmentNamePath(
  units: OrganizationUnit[],
  departmentId: string | null | undefined,
  maxDepth = 4,
): string[] {
  if (!departmentId) return [];

  function find(nodes: OrganizationUnit[], parentPath: string[]): string[] | null {
    for (const unit of nodes) {
      const path = [...parentPath, unit.name];
      if (unit.id === departmentId) return path;
      const childPath = find(unit.children, path);
      if (childPath) return childPath;
    }
    return null;
  }

  return (find(units, []) ?? []).slice(0, Math.max(1, maxDepth));
}

/** 从 Redux 状态按部门 ID 读取最多四级名称路径。 */
export function selectDepartmentNamePathById(
  state: OrganizationUnitsRootState,
  departmentId: string | null | undefined,
): string[] {
  return getDepartmentNamePath(state.organizationUnits.items, departmentId);
}
