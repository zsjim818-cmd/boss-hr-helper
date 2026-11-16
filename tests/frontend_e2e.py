"""Frontend E2E (Playwright + Edge): job switching and identity switching reset.

Run manually:  .\\.venv\\Scripts\\python.exe -m tests.frontend_e2e
Requires local Edge; starts a temp backend (deterministic mode) and vite dev.
"""
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
PYTHON = str(ROOT / ".venv" / "Scripts" / "python.exe")
BACKEND_PORT = os.getenv("E2E_BACKEND_PORT", "8010")
FRONTEND_PORT = os.getenv("E2E_FRONTEND_PORT", "5174")

JOBS = [("AI自动化应用开发工程师", "job-ai"),
        ("Java后端开发工程师", "job-java"),
        ("企业客户销售经理", "job-sales")]


def seed(database_path: Path):
    from storage.database import Database
    from storage.models import AccessContext, ProvisioningContext
    from storage.repositories import RecruitmentRepository
    database = Database(database_path)
    database.migrate()
    repo = RecruitmentRepository(database)
    system = ProvisioningContext("frontend e2e seed")
    repo.create_user(system, "e2e:owner", user_id="owner")
    repo.create_organization(system, "E2E org", "owner", org_id="org")
    repo.create_user(system, "e2e:hr", user_id="hr1")
    ctx = AccessContext("org", "owner")
    repo.add_organization_member(ctx, "hr1", org_role="HR")
    for title, job_id in JOBS:
        repo.create_job(ctx, title, job_id=job_id)


def wait_http(url: str, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError(f"service did not become ready: {url}")


def http_json(method: str, path: str, payload=None):
    """Direct HTTP call against the E2E backend (seeds policy / reads candidates)."""
    import urllib.error
    url = f"http://127.0.0.1:{BACKEND_PORT}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Org-Id": "org", "X-User-Id": "owner"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body or "{}")
        except ValueError:
            parsed = {"raw": body}
        return exc.code, parsed


def extra_resume(key: str, index: int) -> dict:
    """A complete manual resume for server-side pagination E2E seeding."""
    geek = key.split(":", 1)[1]
    return {
        "name": f"补录候选人{index}",
        "degree": "本科",
        "work_years": "4年",
        "job_status": "离职-随时到岗",
        "active_status": "今日活跃",
        "expectation": {"city": "北京", "salary": "12K/月", "position": "Java工程师"},
        "skills": "Java, SQL, Spring Boot",
        "work_experience": [{
            "company": "补录演示公司",
            "position": "Java工程师",
            "responsibility": "负责Java后端接口开发与SQL优化，编写接口自动化测试。",
            "keywords": []}],
        "project_experience": [],
        "education": [{"school": "补录大学", "degree": "本科"}],
        "certifications": [],
        "advantage": "",
        "_meta": {"candidate_key": key, "encrypt_geek_id": geek,
                  "encrypt_job_id": "boss-job-job-java"},
    }


def seed_policy(job_id: str, job_title: str, jd_text: str) -> str:
    """Publish a sealed policy through the public HTTP API (owner identity)."""
    status, compiled = http_json("POST", f"/api/jobs/{job_id}/policies/compile", {
        "job_title": job_title, "jd_text": jd_text,
        "hr_requirements": "重视可核验的原文证据"})
    if status != 200:
        raise RuntimeError(f"compile failed: {status} {compiled}")
    draft = compiled["draft"]
    run_id = compiled["compilation_run"]["run_id"]
    status, confirmed = http_json(
        "POST", f"/api/jobs/{job_id}/policies/drafts/{draft['draft_id']}/confirm",
        {"run_id": run_id, "confirmation_hash": draft["draft_hash"]})
    if status != 200:
        raise RuntimeError(f"confirm failed: {status} {confirmed}")
    status, sealed = http_json(
        "POST", f"/api/jobs/{job_id}/policies/drafts/{draft['draft_id']}/versions",
        {"run_id": run_id, "confirmation_id": confirmed["confirmation_id"]})
    if status != 200:
        raise RuntimeError(f"version failed: {status} {sealed}")
    policy_id = sealed["version"]["policy_id"]
    status, published = http_json(
        "POST", f"/api/jobs/{job_id}/policies/{policy_id}/publish",
        {"confirmation_hash": draft["draft_hash"],
         "quality_override_reason": "frontend E2E acceptance"})
    if status != 200:
        raise RuntimeError(f"publish failed: {status} {published}")
    return policy_id


def http_score(job_id: str, jcid: str, mode: str, idempotency_key: str) -> dict:
    """Score through HTTP and wait for the task to finish."""
    status, started = http_json(
        "POST", f"/api/jobs/{job_id}/candidates/{jcid}/score",
        {"scoring_mode": mode, "idempotency_key": idempotency_key})
    if status != 200:
        raise RuntimeError(f"score failed: {status} {started}")
    if started.get("reused"):
        return started["evaluation"]
    task_id = started["task"]["pipeline_task_id"]
    body = {}
    for _ in range(300):
        status, body = http_json("GET", f"/api/jobs/{job_id}/tasks/{task_id}")
        if body.get("task", {}).get("status") in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(0.2)
    if body.get("task", {}).get("status") != "SUCCEEDED":
        raise RuntimeError(f"scoring failed: {body.get('task')}")
    return body["evaluation"]


def _kill_test_browsers(profile_root: str) -> None:
    """Stop only Edge processes launched with this run's throwaway profile."""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{profile_root}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
        "-ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
    except Exception:
        pass


def main():
    from playwright.sync_api import sync_playwright

    temp = tempfile.mkdtemp(prefix="frontend_e2e_")
    try:
        database_path = Path(temp) / "e2e.sqlite3"
        seed(database_path)
        env = os.environ.copy()
        env.update(RECRUITMENT_DB_PATH=str(database_path), APP_RUN_MODE="deterministic",
                   APP_ALLOW_TRUSTED_HEADERS="1",
                   BROWSER_SOURCE=os.getenv("E2E_BROWSER_SOURCE", "cdp_mock"),
                   BROWSER_CDP_PORT=os.getenv("E2E_CDP_PORT", "9335"),
                   BROWSER_USER_DATA_DIR=os.getenv(
                       "E2E_CDP_PROFILE", str(Path(temp) / "edge-profile")),
                   BROWSER_HEADLESS=os.getenv("E2E_CDP_HEADLESS", "1"),
                   VITE_API_TARGET=f"http://127.0.0.1:{BACKEND_PORT}")
        backend = subprocess.Popen(
            [PYTHON, "-X", "utf8", "-m", "uvicorn", "server.app:app",
             "--host", "127.0.0.1", "--port", BACKEND_PORT],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        node = shutil.which("node") or "node"
        frontend = subprocess.Popen(
            [node, str(FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"),
             "--host", "127.0.0.1", "--port", FRONTEND_PORT, "--strictPort"],
            cwd=str(FRONTEND), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_http(f"http://127.0.0.1:{BACKEND_PORT}/api/health")
            wait_http(f"http://127.0.0.1:{FRONTEND_PORT}/")
            results = run_browser()
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return 0 if all(item["ok"] for item in results) else 1
        finally:
            for process in (frontend, backend):
                process.terminate()
            for process in (frontend, backend):
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            # The backend shuts its CDP runtime down; give Edge a moment to
            # release profile files before removing the temp directory.
            time.sleep(1)
            _kill_test_browsers(str(Path(temp)))
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def run_browser():
    from playwright.sync_api import sync_playwright
    checks = []

    def check(name, condition, detail=""):
        checks.append({"name": name, "ok": bool(condition), "detail": detail})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{FRONTEND_PORT}/policy", wait_until="networkidle")
        page.wait_for_selector(".el-select", timeout=30000)

        def select_job(title):
            page.locator(".el-header .el-select").first.click()
            option = page.locator(".el-select-dropdown__item", has_text=title).first
            option.wait_for(state="visible", timeout=10000)
            option.click()
            page.wait_for_timeout(800)

        # Three seeded jobs are listed.
        page.locator(".el-header .el-select").first.click()
        page.wait_for_timeout(500)
        options = page.locator(".el-select-dropdown__item").all_inner_texts()
        check("three jobs listed", len([o for o in options if any(t in o for t, _ in JOBS)]) == 3,
              str(options))
        page.keyboard.press("Escape")

        # Compile a draft on the AI job.
        select_job("AI自动化应用开发工程师")
        page.locator("textarea").nth(0).fill("岗位职责：开发AI自动化应用。\n必须熟悉Python工作流。")
        page.locator("textarea").nth(1).fill("重视可核验项目")
        page.get_by_role("button", name="编译 Policy 草稿").click()
        page.wait_for_selector("text=草稿已编译", timeout=30000)
        check("draft compiled on AI", page.locator("text=草稿已编译").count() > 0)

        # Switch to Java: job-scoped draft/quality state must be cleared.
        select_job("Java后端开发工程师")
        page.wait_for_timeout(600)
        check("draft reset after switch", page.locator("text=尚未编译草稿").count() > 0)
        check("quality panel cleared", page.locator("text=质量问题").count() == 0)

        # Switch to Sales.
        select_job("企业客户销售经理")
        page.wait_for_timeout(600)
        sales_title = page.locator(".el-form input").nth(1).input_value()
        check("sales job selected", "销售" in sales_title, sales_title)

        # Identity switch: HR without grants sees no jobs; owner sees three again.
        identity = page.locator(".el-header .el-select").nth(1)
        identity.click()
        page.wait_for_timeout(500)
        hr_option = page.locator(".el-select-dropdown__item", has_text="HR").first
        hr_option.wait_for(state="visible", timeout=10000)
        hr_option.click()
        page.wait_for_timeout(1500)
        check("HR identity clears jobs",
              page.locator("text=请先创建岗位").count() > 0,
              page.locator(".el-form input").nth(1).input_value())
        identity.click()
        page.wait_for_timeout(500)
        admin_option = page.locator(".el-select-dropdown__item", has_text="ORG_ADMIN").first
        admin_option.wait_for(state="visible", timeout=10000)
        admin_option.click()
        page.wait_for_timeout(1500)
        check("owner identity restores jobs",
              page.locator(".el-form input").nth(1).input_value() != "",
              page.locator(".el-form input").nth(1).input_value())

        # ------------------------------------------------------------------
        # Batch 1.1: Vue UI -> Mock browser scan -> HTTP scoring -> HR approve
        # -> Greeting Dry-Run (no real message is ever sent).
        # ------------------------------------------------------------------
        try:
            policy_id = seed_policy(
                "job-java", "Java后端开发工程师",
                "岗位职责：负责Java后端服务开发。必须熟悉Spring Boot、SQL与接口自动化测试。")
            check("policy published for scan flow", bool(policy_id), policy_id)
        except Exception as exc:  # noqa: BLE001 - surfaced as a failed check
            check("policy published for scan flow", False, str(exc))
            browser.close()
            return checks

        page.goto(f"http://127.0.0.1:{FRONTEND_PORT}/scanner", wait_until="networkidle")
        page.wait_for_selector(".el-select", timeout=30000)
        select_job("Java后端开发工程师")
        page.get_by_role("button", name="新建批次").click()
        page.wait_for_selector("text=批次已创建", timeout=30000)
        check("batch created in UI", page.locator("text=批次已创建").count() > 0)

        # Browser session -> navigate -> page precheck -> confirmed scan.
        page.get_by_role("button", name="连接/召唤专用浏览器").click()
        page.wait_for_selector("text=READY", timeout=60000)
        check("browser session connected", page.locator("text=READY").count() > 0)
        page.get_by_role("button", name="打开/定位推荐页").click()
        page.wait_for_selector("text=页面岗位", timeout=60000)
        page.wait_for_timeout(800)
        check("page precheck read back",
              page.locator("text=页面岗位").count() > 0
              and page.locator("text=可见卡片").count() > 0)
        page.locator(".el-checkbox").first.click()
        page.wait_for_selector("text=预检确认已保存到批次", timeout=30000)
        page.wait_for_timeout(400)
        check("precheck confirmation persisted to batch",
              page.locator("text=确认已持久化").count() > 0)

        # F5 must keep the persisted HR precheck confirmation.
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".el-checkbox", timeout=30000)
        page.wait_for_timeout(1500)
        check("precheck confirmation survives F5",
              page.locator(".el-checkbox").first.is_checked()
              and page.locator("text=确认已持久化").count() > 0)

        page.get_by_role("button", name="开始只读扫描").click()
        page.wait_for_selector("text=扫描完成", timeout=90000)
        page.wait_for_timeout(1200)
        check("mock scan completed",
              page.locator("text=COMPLETED").count() > 0)
        check("scan report shows the CDP source",
              page.locator("text=CDP_MOCK").count() > 0)
        rows = page.locator(".el-table__body tr").all_inner_texts()
        check("scan imported mock candidates",
              any("geek-java-1" in row for row in rows), str(rows[:3]))
        check("scanner enables scoring for scanned candidate",
              page.get_by_role("button", name="Fast 初筛").is_enabled())

        # Server-side pagination: seed extra candidates into the same batch.
        status, batch_body = http_json("GET", "/api/jobs/job-java/batches")
        restored_batch = batch_body["batches"][0]["batch_id"]
        for index in range(1, 9):
            key = f"job-java:extra-{index}"
            status, seeded = http_json(
                "POST",
                f"/api/jobs/job-java/batches/{restored_batch}/candidates",
                {"candidate_key": key, "resume": extra_resume(key, index),
                 "source": "manual"})
            if status != 200:
                raise RuntimeError(f"extra import failed: {status} {seeded}")

        # F5 must restore the latest batch, persisted scan status and page 1.
        page.reload(wait_until="networkidle")
        page.wait_for_selector(".el-select", timeout=30000)
        page.wait_for_timeout(1800)
        check("batch restored after F5",
              page.get_by_text(restored_batch, exact=False).count() > 0)
        check("scan status restored after F5",
              page.locator("text=COMPLETED").count() > 0)
        check("server pagination shows the filtered total",
              page.locator("text=共 11 人").count() > 0)
        page.locator(".btn-next").click()
        page.wait_for_timeout(900)
        second_page_rows = page.locator(".el-table__body tr").all_inner_texts()
        check("server pagination switches page",
              len(second_page_rows) == 1
              and any("extra-8" in row for row in second_page_rows),
              str(second_page_rows))
        page.locator(".btn-prev").click()
        page.wait_for_timeout(700)

        # Resume snapshot drawer for the scanned candidate.
        page.get_by_role("button", name="查看简历").first.click()
        page.wait_for_selector("text=简历详情", timeout=30000)
        page.wait_for_timeout(600)
        check("resume drawer shows sealed snapshot",
              page.locator("text=工作经历").count() > 0
              and page.locator("text=已封存").count() > 0)
        page.get_by_role("button", name="重新提取详情并更新快照").click()
        page.wait_for_selector("text=简历内容未变化", timeout=60000)
        page.wait_for_timeout(600)
        check("single candidate re-extract keeps the sealed snapshot",
              page.locator("text=简历内容未变化").count() > 0)
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)

        # Score the first imported candidate through the real HTTP API.
        # Fast first, then deep: the pipeline treats deep as an upgrade, so the
        # reverse order would legitimately reuse the deep result for fast.
        status, listing = http_json("GET", "/api/jobs/job-java/candidates?limit=10")
        jcid = listing["items"][0]["job_candidate"]["job_candidate_id"]
        fast_eval = http_score("job-java", jcid, "fast_mode", "frontend-e2e-fast-1")
        status, approved_fast = http_json(
            "POST", f"/api/jobs/job-java/evaluations/"
                    f"{fast_eval['pipeline_evaluation_id']}/reviews",
            {"action": "APPROVE", "reason": "frontend e2e"})
        check("fast evaluation approved over HTTP", status == 200, str(approved_fast))
        deep_eval = http_score("job-java", jcid, "deep_mode", "frontend-e2e-deep-1")
        check("deep scoring finished", deep_eval.get("scoring_status") == "DEEP_SCORED",
              str(deep_eval.get("scoring_status")))

        page.goto(f"http://127.0.0.1:{FRONTEND_PORT}/review", wait_until="networkidle")
        page.wait_for_selector(".el-table__body tr", timeout=30000)
        page.wait_for_timeout(800)
        check("review batch filter and review queue controls",
              page.locator("text=仅待核验").count() > 0
              and page.locator(".el-card").first.locator(".el-select").count() > 0)
        page.locator(".el-table__body tr").first.click()
        page.wait_for_selector("text=评估详情", timeout=30000)
        page.get_by_role("tab", name="HR 审核").click()
        page.wait_for_timeout(600)
        page.get_by_role("button", name="批准", exact=True).click()
        page.wait_for_selector("text=审核已记录", timeout=30000)
        page.wait_for_timeout(800)
        check("approve recorded in UI", page.locator("text=APPROVE").count() > 0)

        page.get_by_role("button", name="Greeting Dry-Run 预演").click()
        try:
            page.wait_for_selector("text=DRY_RUN_READY", timeout=60000)
        except Exception:
            print("DEBUG alerts:", page.locator(".el-alert").all_inner_texts())
            print("DEBUG body:", page.locator("body").inner_text()[-2500:])
            raise
        page.wait_for_timeout(600)
        check("greeting dry-run ready", page.locator("text=DRY_RUN_READY").count() > 0)
        check("dry-run did not mark contacted",
              page.locator("text=尚未联系").count() > 0)
        check("candidate-level rehearsal badge",
              page.locator("text=当前账号已预演").count() > 0)
        contact_rows = page.locator(".el-table").last.locator(
            ".el-table__body tr").all_inner_texts()
        check("dry-run persisted as PENDING",
              any("PENDING" in row and "DRY_RUN:" in row for row in contact_rows),
              str(contact_rows[-2:]))

        # A second click must reuse the existing PENDING rehearsal row.
        page.get_by_role("button", name="Greeting Dry-Run 预演").click()
        page.wait_for_selector("text=已复用既有预演记录", timeout=60000)
        page.wait_for_timeout(600)
        repeat_rows = page.locator(".el-table").last.locator(
            ".el-table__body tr").all_inner_texts()
        check("repeated dry-run is idempotent",
              len([row for row in repeat_rows if "DRY_RUN:" in row]) == 1,
              str(repeat_rows[-2:]))

        # ------------------------------------------------------------------
        # Contact-state consistency: same HR contacts via the FAST evaluation,
        # then the UI must show the candidate-level badge on BOTH fast and deep.
        # ------------------------------------------------------------------
        status, listing = http_json("GET", "/api/jobs/job-java/candidates?limit=10")
        items = {item["job_candidate"]["candidate_key"]: item
                 for item in listing["items"]}
        jcid2 = items["job-java:geek-java-2"]["job_candidate"]["job_candidate_id"]
        status, contacted = http_json(
            "POST", f"/api/jobs/job-java/evaluations/"
                    f"{fast_eval['pipeline_evaluation_id']}/contact",
            {"action": "CONTACTED", "note": "frontend e2e fast contact"})
        check("fast evaluation contacted over HTTP", status == 200, str(contacted))
        http_score("job-java", jcid2, "fast_mode", "frontend-e2e-fast-2")

        status, ranking_body = http_json("GET", "/api/jobs/job-java/ranking")
        ranking_keys = [item["candidate_key"] for item in ranking_body["ranking"]]
        check("two ranking rows for rapid switching", len(ranking_keys) >= 2,
              str(ranking_keys))

        page.goto(f"http://127.0.0.1:{FRONTEND_PORT}/review", wait_until="networkidle")
        page.wait_for_selector(".el-table__body tr", timeout=30000)
        page.wait_for_timeout(800)
        rows = page.locator(".el-table__body tr")
        first_index = ranking_keys.index("job-java:geek-java-1")
        rows.nth(first_index).click()
        page.wait_for_selector("text=评估详情", timeout=30000)
        page.wait_for_timeout(800)
        check("candidate-level contact badge from summary",
              page.get_by_text("已联系（HR：owner）", exact=False).count() > 0)
        check("current account contact tag",
              page.get_by_text("当前账号已联系", exact=False).count() > 0)
        page.get_by_role("tab", name="HR 审核").click()
        page.wait_for_timeout(500)
        check("dry-run disabled after this HR contacted",
              page.get_by_role("button", name="Greeting Dry-Run 预演").is_disabled())

        # Switch to the fast evaluation: the candidate-level badge must persist.
        detail_card = page.locator(".el-card", has_text="评估详情").first

        def switch_evaluation(mode: str) -> dict:
            try:
                detail_card.get_by_role("button", name=mode, exact=True).click(timeout=8000)
            except Exception:
                print("DEBUG header:", detail_card.inner_html()[:2500])
                print("DEBUG buttons:", page.locator("button").all_inner_texts())
                raise
            page.wait_for_timeout(800)
            return {"ok": True}

        fast_switch = switch_evaluation("fast_mode")
        check("switched to the fast evaluation", fast_switch.get("ok") is True,
              str(fast_switch))
        check("contact badge persists on fast evaluation",
              page.get_by_text("已联系（HR：owner）", exact=False).count() > 0)
        deep_switch = switch_evaluation("deep_mode")
        check("switched back to the deep evaluation", deep_switch.get("ok") is True,
              str(deep_switch))
        check("contact badge persists on deep evaluation",
              page.get_by_text("已联系（HR：owner）", exact=False).count() > 0)

        # Rapid candidate switching: a late detail response must not win.
        second_index = ranking_keys.index("job-java:geek-java-2")
        rows.nth(first_index).click()
        rows.nth(second_index).click()
        page.wait_for_timeout(1200)
        header = detail_card.inner_text()
        check("rapid detail switch keeps the latest candidate",
              "job-java:geek-java-2" in header, header[:160])
        browser.close()
    return checks


if __name__ == "__main__":
    raise SystemExit(main())
