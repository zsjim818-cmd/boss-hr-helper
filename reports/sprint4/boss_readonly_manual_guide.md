# BOSS 招聘端「只读采集 + Dry-Run」人工真机联调指南

适用范围：Sprint 4 Batch 2B 起的 `boss_readonly` 数据源。
**安全红线**：只读采集；Dry-Run 只重定位并校验招呼按钮，绝不点击发送；不代填账号、不处理验证码/滑块；不读取或落库 Cookie/Token；自动化测试不访问真实 `zhipin.com`。

---

## 0. 前置条件

- Windows + 已安装 Microsoft Edge（脚本自动查找，或 `-EdgePath` 指定）。
- 本仓库依赖已安装：`.\.venv\Scripts\python.exe -m pip install -e .[dev]`。
- 数据库已升级到 v11：`.\\.venv\\Scripts\\python.exe main.py db-check` 应显示 `schema_version: 11`。
- 你的 HR 系统身份（org_id / user_id）已在数据库中存在且为 ACTIVE 成员。

## 1. 为当前 HR 绑定浏览器账号（持久化，不存凭据）

```powershell
.\.venv\Scripts\python.exe scripts\bind_browser_account.py `
  --org org --user owner `
  --account boss-account-001 `
  --port 9222 `
  --profile-id owner-port-9222
```

说明：
- `browser_account_id` 是 **BOSS 账号身份标识**，与系统 `actor_user_id` 分开保存；
- `profile_id` 是受控相对标识，只用于定位专用 Profile 目录；
- 绑定表不包含任何 Cookie/Token 字段。

查看绑定（只读）：运行一次上表后可用 `sqlite3` 查询 `browser_account_bindings`，或用后续 `runtime-mode` 本人视图（脱敏）。

## 2. 拉起专用 Edge 并人工扫码登录

```powershell
# 有头模式（人工扫码需要）
powershell -ExecutionPolicy Bypass -File scripts\launch_boss_edge.ps1 `
  -Port 9222 -ProfileDir "$env:LOCALAPPDATA\boss-hr-helper\edge-profile\owner-port-9222"

# 健康检查
powershell -ExecutionPolicy Bypass -File scripts\launch_boss_edge.ps1 -Port 9222 -Check
```

- 脚本拒绝使用日常 Edge Profile；
- 已携带 `--remote-debugging-port`、`--user-data-dir`、`--enable-automation`，满足运行时 Profile 核验；
- 在打开的 Edge 中**人工扫码登录 BOSS 招聘端**，进入「推荐牛人」页面。

## 3. 启动服务（live 模型 + boss_readonly）

```powershell
$env:APP_RUN_MODE = "live"
$env:APP_ALLOW_TRUSTED_HEADERS = "1"
$env:BROWSER_SOURCE = "boss_readonly"
$env:BROWSER_ALLOW_REAL_BOSS = "1"
$env:BROWSER_CDP_PORT = "9222"
$env:BROWSER_USER_DATA_DIR = "$env:LOCALAPPDATA\boss-hr-helper\edge-profile\owner-port-9222"
$env:BROWSER_CDP_AUTOLAUNCH = "0"
$env:BROWSER_BOSS_URL = "https://www.zhipin.com/web/chat/recommend"
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

诊断：`GET http://127.0.0.1:8000/api/runtime-mode`
- 匿名只看全局模式；
- 带 `X-Org-Id/X-User-Id` 只看自己的会话（无绝对路径）。

## 4. 前端操作流程

1. 打开 `http://127.0.0.1:5173`，选择岗位并确认已发布 Policy；
2. 进入「采集控制台」→ 创建批次（锁定当前 Policy 版本）；
3. 点击「Mock 浏览器扫描」的位置——`boss_readonly` 模式下同一入口调用真实只读扫描：
   - 扫描报告显示 `source=boss_readonly` 与实际页面 URL；
   - 未登录 → `AUTH_REQUIRED`；出现验证码/滑块 → `CAPTCHA_BLOCKED`；
   - 扫描统计与错误持久化，重启后仍可查询。
4. 评分：
   - 前端按批次传递 `batch_id` 与批次级幂等键；
   - live 模式会先准备该批次 Policy 版本的 BGE 资产，再跑 Tier 0–3（Qwen）。
5. 进入「审核看板」：选择候选人 → HR 审核 `APPROVE`；
6. 点击「Greeting Dry-Run 预演」：
   - 适配器重新打开该候选人详情并核验 `data-geek-id`/姓名；
   - 仅在核验面板内定位招呼按钮并检查是否可用/达到上限；
   - 返回 `DRY_RUN_READY` 并写入 `mode=DRY_RUN` 的 PENDING 记录（幂等）；
   - **不会点击发送**，重复点击复用同一条预演记录。

## 5. 故障排查

| 现象 | 处理 |
| --- | --- |
| `AUTH_REQUIRED` | 回到 Edge 人工扫码登录，确认进入招聘者推荐页 |
| `CAPTCHA_BLOCKED` | 人工完成安全验证/滑块后重试；不要自动化绕过 |
| `CDP_PROFILE_MISMATCH` / `CDP_PROFILE_UNVERIFIED` | 确认使用脚本拉起的专用 Profile 与 `BROWSER_USER_DATA_DIR` 一致；外部未知 Profile 会被拒绝 |
| `BROWSER_UNAVAILABLE` | `launch_boss_edge.ps1 -Check` 检查端口；不要随意关闭非本程序启动的浏览器 |
| `BROWSER_ACCOUNT_UNMAPPED` | 执行第 1 步绑定当前 HR 的 BOSS 账号 |
| 页面结构变化 | 更新 `browser/boss_readonly_adapter.py` 的选择器常量，并先用本地夹具回归 |

## 6. 自动化验证（不访问外网）

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_boss_readonly_adapter -v
.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_api_browser_cdp -v
```

本地夹具覆盖：`recommendFrame` iframe、未登录、滑块/验证码、详情面板切换、字段残缺、招呼按钮禁用/上限；断言 `window.__BOSS_GREET_CLICKED__` 永远为 false。
