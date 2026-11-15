"""Stage 3 offline acceptance, preserving separate Stage 1/2 historical reports."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import time
import unittest

from config.settings import PROJECT_ROOT


def main():
    output = PROJECT_ROOT / "reports/phase1_7_stage3"
    output.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((PROJECT_ROOT / "reports/phase1_7_stage1/source_manifest.json").read_text(encoding="utf-8"))
    original = sorted(name[:-3].replace("/", ".") for name in baseline["files"]
                      if name.startswith("tests/test_") and name.endswith(".py"))
    stage2 = sorted("tests." + path.stem for path in (PROJECT_ROOT / "tests").glob("test_database_*.py"))
    compiler = sorted("tests." + path.stem for path in (PROJECT_ROOT / "tests").glob("test_policy_*.py"))
    summary = {"executed_at": datetime.now(timezone.utc).isoformat(), "model_calls": "OFFLINE_ONLY",
               "groups": {}, "unchanged_legacy_files": {}, "failures": []}
    for name, fingerprint in baseline["files"].items():
        if name in ("main.py", "README.md"):
            continue
        summary["unchanged_legacy_files"][name] = hashlib.sha256((PROJECT_ROOT / name).read_bytes()).hexdigest() == fingerprint
    for label, modules in (("original_regression", original), ("stage2_database", stage2), ("compiler", compiler)):
        with (output / (label + ".log")).open("w", encoding="utf-8") as stream:
            with redirect_stdout(stream), redirect_stderr(stream):
                suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
                start = time.perf_counter()
                result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        summary["groups"][label] = {"modules": modules, "run": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
            "successful": result.wasSuccessful(), "elapsed_seconds": round(time.perf_counter() - start, 3)}
        summary["failures"].extend({"test": str(test), "detail": detail} for test, detail in [*result.errors, *result.failures])
    summary["ok"] = (all(group["successful"] and group["skipped"] == 0 for group in summary["groups"].values())
        and summary["groups"]["original_regression"]["run"] == 90
        and summary["groups"]["stage2_database"]["run"] == 59
        and all(summary["unchanged_legacy_files"].values()))
    (output / "test_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": summary["ok"], "groups": summary["groups"],
                      "unchanged_legacy_files": sum(summary["unchanged_legacy_files"].values())}, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
