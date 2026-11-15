"""Stage 4 grouped offline acceptance; original phase reports stay immutable."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import time
import unittest

from config.settings import PROJECT_ROOT


def main():
    output = PROJECT_ROOT / "reports/phase1_7_stage4"
    output.mkdir(parents=True, exist_ok=True)
    stage1 = json.loads((PROJECT_ROOT / "reports/phase1_7_stage1/source_manifest.json").read_text(encoding="utf-8"))
    stage3 = json.loads((PROJECT_ROOT / "reports/phase1_7_stage3/source_manifest.json").read_text(encoding="utf-8"))
    original = sorted(name[:-3].replace("/", ".") for name in stage1["files"]
                      if name.startswith("tests/test_") and name.endswith(".py"))
    groups = {"original_regression": original}
    for label, pattern in (("stage2_database", "test_database_*.py"),
                           ("stage3_compiler", "test_policy_*.py"), ("stage4_prescreen", "test_prescreen_*.py")):
        groups[label] = sorted("tests." + path.stem for path in (PROJECT_ROOT / "tests").glob(pattern))
    allowed = {"main.py", "README.md", "config/policy_validation.py"}
    unchanged = {}
    for name, previous in stage3["files"].items():
        if name not in allowed:
            path = PROJECT_ROOT / name
            unchanged[name] = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == previous
    result = {"executed_at": datetime.now(timezone.utc).isoformat(), "model_calls_this_run": 0,
              "groups": {}, "protected_stage3_files": unchanged, "failures": []}
    for label, modules in groups.items():
        with (output / (label + ".log")).open("w", encoding="utf-8") as stream:
            with redirect_stdout(stream), redirect_stderr(stream):
                suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
                started = time.perf_counter()
                tested = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        result["groups"][label] = {"modules": modules, "run": tested.testsRun,
            "failures": len(tested.failures), "errors": len(tested.errors), "skipped": len(tested.skipped),
            "successful": tested.wasSuccessful(), "elapsed_seconds": round(time.perf_counter() - started, 3)}
        result["failures"].extend({"test": str(test), "detail": detail} for test, detail in [*tested.failures, *tested.errors])
    result["ok"] = (all(item["successful"] and item["skipped"] == 0 for item in result["groups"].values())
        and result["groups"]["original_regression"]["run"] == 90
        and result["groups"]["stage2_database"]["run"] == 59
        and result["groups"]["stage3_compiler"]["run"] == 105
        and result["groups"]["stage4_prescreen"]["run"] > 0 and all(unchanged.values()))
    (output / "test_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "groups": result["groups"],
                      "protected_files_unchanged": sum(unchanged.values())}, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
