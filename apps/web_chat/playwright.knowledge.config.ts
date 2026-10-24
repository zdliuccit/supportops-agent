import { defineConfig, devices } from "@playwright/test";

// 由 tests/run_knowledge_browser.py 创建临时数据库/API；不复用开发服务。
if (!process.env.SUPPORTOPS_KNOWLEDGE_E2E_API_URL) throw new Error("请通过隔离知识浏览器测试入口运行");
const webPort = process.env.SUPPORTOPS_KNOWLEDGE_E2E_WEB_PORT;
if (!webPort || !/^\d+$/.test(webPort)) throw new Error("缺少隔离 Web 端口");
export default defineConfig({
  testDir: "./e2e-knowledge",
  timeout: 45_000,
  fullyParallel: true,
  workers: 2,
  outputDir: process.env.SUPPORTOPS_KNOWLEDGE_E2E_OUTPUT_DIR ?? "test-results-knowledge",
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${webPort}`, ...devices["Desktop Chrome"] },
  webServer: {
    command: `pnpm exec vite --host 127.0.0.1 --port ${webPort} --strictPort`,
    url: `http://127.0.0.1:${webPort}`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { VITE_API_BASE_URL: process.env.SUPPORTOPS_KNOWLEDGE_E2E_API_URL },
  },
});
