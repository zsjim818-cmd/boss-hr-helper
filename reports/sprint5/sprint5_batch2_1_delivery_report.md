# Sprint 5 Batch 2.1 交付报告（CDP 页面生命周期自愈 + 一键召唤直达推荐页）

日期：2026-11-16
基线：Sprint 5 Batch 2（commit `4812154`，v12）
约束：未修改 001–012 迁移、未修改 `config.json`、未发送任何真实消息、真实 BOSS 仍硬禁用。

## 一、根因分析

### 1. 有头 Edge 停留在 about:blank
`CDPConnectionManager._open_page_impl` 只按目标 URL 前缀查找已有标签页；Edge 启动自带的 `about:blank` 不匹配 Mock `file://` URL，于是直接调用 `context.new_page()`。同时 `/api/browser/session/connect` 只建立连接，不导航，所以有头窗口必然停在空白页。

### 2. `Target page, context or browser has been closed`
- 管理器缓存 `_browser` 后只用 `is_connected()` 判断，不验证默认 `BrowserContext`/`Page` 是否仍存活。用户关闭标签页/窗口后，缓存句柄仍被复用。
- `CdpMockBossAdapter` 的 `list_cards/open_detail/send_greeting` 直接使用缓存的 `self._page`，一旦标签页被手动关闭就抛 Playwright 原生错误。
- `connect()` 内部对异常做二次包装时没有先识别 `BrowserError`，部分路径把 `PAGE_UNAVAILABLE` 再次包进消息，形成 `PAGE_UNAVAILABLE: PAGE_UNAVAILABLE: ...`。

## 二、修复内容

### 1. `browser/runtime.py`
- `connect()` 缓存命中时额外 touch `browser.contexts`，发现句柄失效立即清空重连。
- 新增 `_is_target_closed()` 识别 Playwright/Chromium 陈旧句柄错误。
- 新增 `run_with_recovery(action)`：在同一 CDP worker 线程内捕获陈旧句柄，丢弃失效 Browser/Playwright 句柄、保留受控 Edge 进程，重连后重试一次。
- 新增 `_drop_connection_impl()`：只断开失效连接，不终止我们启动的 Edge；外部 Edge 永不结束。
- `_open_page_impl()` 改为：
  1. 目标 URL 前缀已存在 → 原地复用（`navigate=False` 时不重置筛选）；
  2. 存在 `about:blank`/新标签页 → **直接复用并导航**，不再新开空白页；
  3. 否则新建 Page 并导航。
- 新增 `page_for()`：带自愈的页面获取入口。

### 2. `browser/cdp_adapter.py`
- `connect()` 改用 `manager.page_for()`；`BrowserError` 原样抛出，不再双重包装。
- 新增 `_ensure_page()`：校验缓存 Page 是否关闭、URL 是否仍为目标 Mock 页；失效则在 worker 线程内重新复用/打开页面。
- `list_cards / open_detail / send_greeting` 全部改为 `run_with_recovery`，捕获“标签页被关闭/连接断开”后自动重建页面并重试一次。
- `close()` 仍只丢弃 adapter 自己的 Page 引用，绝不关闭底层 Browser/Context。

### 3. `server/browser_session.py` + `server/api.py` + `server/schemas.py`
- `POST /api/browser/session/connect` 新增可选 `{job_id}`：
  - `cdp_mock`：连接后立即把启动自带的空白页渲染为当前岗位 Mock 推荐页，直接返回 READY、页面标题、URL、可见卡片；
  - `mock`：同样可直接进入推荐页；
  - `boss_readonly`：保持 `REAL_CONTRACT_UNVERIFIED` 硬门禁，不访问真实页面。
- `navigate` 保持幂等：重复点击、标签页被关闭、连接断开后都能重新定位到推荐页。
- 修正会话错误状态字段（写回 `connected=False`）。

### 4. 前端
- `browserSessionConnect(jobId?)` 传递当前 `store.activeJobId`。
- ScannerView 点击“连接/召唤专用浏览器”后直接展示推荐页，不再需要先面对空白窗口。
- “打开/定位推荐页”仍保留为幂等刷新入口。

## 三、专项回归测试（真实 Edge CDP）

`tests/test_browser_session.py` 新增 `CdpPageLifecycleTests`（Edge/Playwright 不可用时自动 skip）：

1. `test_connect_job_reuses_blank_tab_and_full_chain_is_stable`
   - 初始 Edge 自带 about:blank；
   - connect(job_id) → 同一标签页被导航为 Mock 页（Mock 页数量 = 1）；
   - navigate → precheck → scan → re-extract → score → approve → greeting-dry-run 全链路；
   - 全程复用同一标签页，Dry-Run 不记录真实发送。
2. `test_closed_page_and_browser_disconnect_self_heal`
   - 主动关闭所有 Page 后 navigate / precheck / scan 自动重建页面；
   - 关闭 Browser 连接后 navigate 自动重连；
   - 再次关闭 Page 后 re-extract 仍成功。

专项测试已在无头与 `BROWSER_HEADLESS=0` 有头 Edge 两种模式下运行通过。若关闭最后一个窗口导致受控 Edge 无法再创建标签页，管理器会重启自己启动的 Edge（外部 Edge 永不终止）并重试操作。

## 四、测试结果

- 后端全量：**714 / 714 OK**
- 前端 UI E2E：**43 / 43 通过，退出码 0**
  - 新增：连接后直接展示 `推荐牛人（CDP Mock）`，无 about:blank
- Node：7 / 7
- `vue-tsc --noEmit`：0 错误
- `vite build`：成功
- 主库 db-check：v12 / ok / pending=[]（本次无迁移变更）

## 五、安全约束

- 未修改 001–012；未改 `config.json`。
- 真实 BOSS 导航/采集仍 `REAL_CONTRACT_UNVERIFIED`。
- 真实消息发送仍物理禁用，仅 Dry-Run。
- 未使用 `git clean` / `git reset --hard`；用户未跟踪资料与数据库备份保持不动。
