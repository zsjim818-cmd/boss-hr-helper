"""Persist an HR -> BOSS browser account binding (no credentials are stored).

Example:
    .\.venv\Scripts\python.exe scripts\bind_browser_account.py \
        --org org --user owner --account boss-account-001 --port 9222 \
        --profile-id owner-port-9222
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from storage.database import Database  # noqa: E402
from storage.database_cli import database_path  # noqa: E402
from storage.models import AccessContext  # noqa: E402
from storage.repositories import RecruitmentRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--account", required=True, help="BOSS browser account id")
    parser.add_argument("--port", type=int, required=True, help="dedicated CDP port")
    parser.add_argument("--profile-id", required=True,
                        help="relative dedicated profile identifier (never a path)")
    parser.add_argument("--status", choices=("ACTIVE", "DISABLED"), default="ACTIVE")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in [1,65535]")

    repository = RecruitmentRepository(Database(database_path()))
    row = repository.upsert_browser_binding(
        AccessContext(args.org, args.user),
        browser_account_id=args.account, cdp_port=args.port,
        profile_id=args.profile_id, status=args.status)
    print(f"bound {args.org}/{args.user} -> {row['browser_account_id']} "
          f"port={row['cdp_port']} profile_id={row['profile_id']} status={row['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
