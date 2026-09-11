import { Menu } from "lucide-react";

import type { SidebarNavItem } from "./SidebarNavigation";

/**
 * 多级菜单演示数据，对应 Minimal Dashboard 的 Level 示例：
 *
 * Level
 * ├─ Level 1a
 * │  ├─ Level 2a
 * │  └─ Level 2b
 * └─ Level 1b
 *
 * 叶子节点使用 hash 地址，接入演示页面时不会依赖后端接口或业务路由。
 */
export const demoMultiLevelMenu: SidebarNavItem[] = [
  {
    label: "Level",
    icon: Menu,
    children: [
      {
        label: "Level 1a",
        children: [
          { label: "Level 2a", to: "#level-2a" },
          { label: "Level 2b", to: "#level-2b" },
        ],
      },
      { label: "Level 1b", to: "#level-1b" },
    ],
  },
];
