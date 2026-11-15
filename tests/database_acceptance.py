"""Reproducible Stage 2 offline acceptance on actual migrations/repositories."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import time
import unittest

from config.settings import PROJECT_ROOT


def main():
    target = PROJECT_ROOT / "reports" / "phase1_7_stage2"
    target.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((PROJECT_ROOT / "reports/phase1_7_stage1/source_manifest.json").read_text(encoding="utf-8"))
    original = sorted(path[:-3].replace("/", ".") for path in baseline["files"]
                      if path.startswith("tests/test_") and path.endswith(".py"))
    database = sorted("tests." + path.stem for path in (PROJECT_ROOT / "tests").glob("test_database_*.py"))
    summary = {"executed_at": datetime.now(timezone.utc).isoformat(), "real_models_called": False,
               "test_scope": "Actual project migrations and repositories; independent temporary file databases",
               "groups": {}, "source_integrity": {}}
    for name, old_hash in baseline["files"].items():
        if name in ("main.py", "README.md"):
            continue
        path = PROJECT_ROOT / name
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        summary["source_integrity"][name] = {"unchanged": current_hash == old_hash,
                                                 "baseline_sha256": old_hash, "current_sha256": current_hash}
    for group, modules in (("original_regression", original), ("database_integration", database)):
        with (target / (group + ".log")).open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
                started = time.monotonic()
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
        summary["groups"][group] = {"modules": modules, "run": result.testsRun,
                                     "failures": len(result.failures), "errors": len(result.errors),
                                     "skipped": len(result.skipped), "successful": result.wasSuccessful(),
                                     "elapsed_seconds": round(time.monotonic() - started, 3)}
    summary["ok"] = (all(group["successful"] for group in summary["groups"].values())
                      and all(item["unchanged"] for item in summary["source_integrity"].values()))
    (target / "test_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": summary["ok"], "groups": summary["groups"],
                      "unchanged_baseline_files": sum(item["unchanged"] for item in summary["source_integrity"].values())},
                     ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
