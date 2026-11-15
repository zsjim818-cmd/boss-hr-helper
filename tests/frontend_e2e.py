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
        page.get_by_role("button", name="创建批次").click()
        page.wait_for_selector("text=批次已创建", timeout=30000)
        check("batch created in UI", page.locator("text=批次已创建").count() > 0)

        page.get_by_role("button", name="Mock 浏览器扫描").click()
        page.wait_for_selector("text=Mock 扫描完成", timeout=60000)
        page.wait_for_timeout(800)
        check("mock scan completed",
              page.locator("text=COMPLETED").count() > 0)
        check("scan report shows the CDP source",
              page.locator("text=cdp_mock").count() > 0)
        rows = page.locator(".el-table__body tr").all_inner_texts()
        check("scan imported mock candidates",
              any("geek-java-1" in row for row in rows), str(rows[:3]))

        # Score the first imported candidate through the real HTTP API.
        status, listing = http_json("GET", "/api/jobs/job-java/candidates?limit=10")
        jcid = listing["items"][0]["job_candidate"]["job_candidate_id"]
        status, started = http_json(
            "POST", f"/api/jobs/job-java/candidates/{jcid}/score",
            {"scoring_mode": "deep_mode", "idempotency_key": "frontend-e2e-b11"})
        task_id = started["task"]["pipeline_task_id"]
        body = {}
        for _ in range(300):
            status, body = http_json("GET", f"/api/jobs/job-java/tasks/{task_id}")
            if body.get("task", {}).get("status") in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(0.2)
        check("deep scoring finished", body.get("task", {}).get("status") == "SUCCEEDED",
              str(body.get("task", {}).get("error_type", "")))

        page.goto(f"http://127.0.0.1:{FRONTEND_PORT}/review", wait_until="networkidle")
        page.wait_for_selector(".el-table__body tr", timeout=30000)
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
        browser.close()
    return checks


if __name__ == "__main__":
    raise SystemExit(main())
