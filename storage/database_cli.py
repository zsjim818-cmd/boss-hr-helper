"""Local deployment commands for the isolated SQLite business database."""
import argparse
import json
import os
import sqlite3
from pathlib import Path

from config.settings import PROJECT_ROOT


def database_path(value=None):
    path = Path(value or os.getenv("RECRUITMENT_DB_PATH", "data/recruitment.sqlite3")).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def main(command, argv=None):
    from storage.database import Database
    parser = argparse.ArgumentParser(description="SQLite业务库部署管理；不读取或迁移JSON评分缓存")
    parser.add_argument("--database", help="覆盖RECRUITMENT_DB_PATH；相对路径按项目根目录解析")
    parser.add_argument("--busy-timeout-ms", type=int,
                        default=os.getenv("RECRUITMENT_DB_BUSY_TIMEOUT_MS", "5000"))
    args = parser.parse_args(argv)
    target = database_path(args.database)
    try:
        database = Database(target, busy_timeout_ms=args.busy_timeout_ms)
        if command in ("db-init", "db-migrate"):
            result = database.migrate()
        elif command == "db-check":
            if not target.is_file():
                raise FileNotFoundError("Database does not exist; run db-init first")
            result = database.check()
        else:
            parser.error("Unknown database operation")
        print(json.dumps({"command": command, "database": str(target), "result": result}, ensure_ascii=False, indent=2))
        return 0 if result.get("ok", True) else 1
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(json.dumps({"command": command, "database": str(target), "ok": False,
                          "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit("Use python main.py db-init/db-check/db-migrate")
