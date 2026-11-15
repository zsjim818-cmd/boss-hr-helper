"""Guards against the 2026-11-15 incident class.

That incident: the legacy console's read path silently re-seeded the business
database from ``config.json`` and rewrote the root config through a narrow
whitelist, dropping ``config_version``/``model_versions``/``cache`` and thereby
breaking every V2-engine test that templates on the root config (42 failures),
while the same process created unmanaged tables that tripped the migration
drift check.  These tests fail fast on both halves of that failure mode:

1. the root config.json must remain a valid V2 config and must match the
   committed baseline (tampering is visible, tuning goes through a commit);
2. if the main business database exists, it must stay drift-free
   (``db-check`` semantics), so policy/semantic CLIs keep working on it.

All checks are read-only.  A missing database is skipped, not failed: a fresh
checkout that has not run ``db-init`` has nothing to guard yet.
"""
import json
import subprocess
import unittest

from config.settings import DEFAULT_CONFIG_PATH, PROJECT_ROOT
from config.validation import validate_config
from storage.database import Database
from storage.database_cli import database_path


def _git_show(path: str) -> str | None:
    """Return the committed content of a tracked file, or None if unavailable."""
    try:
        result = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=str(PROJECT_ROOT),
                                capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="strict")


class RootConfigGuardTests(unittest.TestCase):
    def test_root_config_passes_v2_validation(self):
        config = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        # Raises ValueError with the exact missing section on any regression.
        validate_config(config)

    def test_root_config_matches_committed_baseline(self):
        committed = _git_show("config.json")
        if committed is None:
            self.skipTest("git baseline unavailable (not a git checkout or no HEAD)")
        current = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        if current != committed:
            # Re-seeding or manual tuning rewrote the engine's shared config;
            # restore with `git restore config.json` and route changes through
            # a commit so engines, caches and tests share one auditable source.
            self.fail("根目录 config.json 与 git 基线不一致（被覆写或手工修改）；"
                      "用 git restore config.json 恢复，配置变更应通过提交进入")


class MainDatabaseGuardTests(unittest.TestCase):
    def test_main_business_database_stays_drift_free(self):
        path = database_path(None)
        if not path.is_file():
            self.skipTest("main business database not initialized (run db-init first)")
        result = Database(path).check()
        # Same semantics as `python main.py db-check`: schema drift, pending
        # migrations, integrity or foreign-key problems all fail here.
        self.assertTrue(result["ok"],
                        "主业务库未通过 db-check 守门: " + "; ".join(result["issues"]))


if __name__ == "__main__":
    unittest.main()
