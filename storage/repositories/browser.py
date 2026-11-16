"""Persistent browser account bindings and batch scan runs (migration 011)."""
import json
from datetime import datetime, timedelta, timezone

from .base import BaseRepository, NotFound, uid, utcnow


class ScanAlreadyRunning(RuntimeError):
    def __init__(self, batch_id: str):
        self.batch_id = batch_id
        super().__init__(f"SCAN_ALREADY_RUNNING: batch {batch_id}")


def _is_stale(started_at: str, minutes: int = 60) -> bool:
    try:
        value = datetime.fromisoformat((started_at or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    return value < datetime.now(timezone.utc) - timedelta(minutes=minutes)


class BrowserRepository(BaseRepository):
    # ------------------------------------------------------------- bindings
    def get_browser_binding(self, ctx):
        """Current HR's persisted browser account binding (no credentials)."""
        with self.read(ctx) as conn:
            row = conn.execute("""SELECT * FROM browser_account_bindings
                WHERE org_id=? AND user_id=?""", (ctx.org_id, ctx.user_id)).fetchone()
            return dict(row) if row is not None else None

    def upsert_browser_binding(self, ctx, *, browser_account_id, cdp_port,
                               profile_id, status="ACTIVE"):
        if status not in ("ACTIVE", "DISABLED"):
            raise ValueError("Unsupported browser binding status")
        with self.tx(ctx) as conn:
            existing = conn.execute("""SELECT * FROM browser_account_bindings
                WHERE org_id=? AND user_id=?""", (ctx.org_id, ctx.user_id)).fetchone()
            if existing is None:
                conn.execute("""INSERT INTO browser_account_bindings
                    (org_id,user_id,browser_account_id,cdp_port,profile_id,status,updated_at)
                    VALUES(?,?,?,?,?,?,?)""",
                             (ctx.org_id, ctx.user_id, browser_account_id,
                              int(cdp_port), profile_id, status, utcnow()))
                action = "BROWSER_BINDING_CREATED"
            else:
                conn.execute("""UPDATE browser_account_bindings
                    SET browser_account_id=?, cdp_port=?, profile_id=?, status=?, updated_at=?
                    WHERE org_id=? AND user_id=?""",
                             (browser_account_id, int(cdp_port), profile_id, status,
                              utcnow(), ctx.org_id, ctx.user_id))
                action = "BROWSER_BINDING_UPDATED"
            self.audit(conn, ctx, action, browser_account_id,
                       {"cdp_port": int(cdp_port), "profile_id": profile_id,
                        "status": status})
            return self.one(conn, """SELECT * FROM browser_account_bindings
                WHERE org_id=? AND user_id=?""", (ctx.org_id, ctx.user_id))

    # ------------------------------------------------------------ scan runs
    def start_browser_scan(self, ctx, job_id, batch_id, *, source,
                           browser_account_id=None):
        with self.tx(ctx, job_id, "collect") as conn:
            self.one(conn, """SELECT 1 FROM scan_batches
                WHERE org_id=? AND job_id=? AND batch_id=?""",
                     (ctx.org_id, job_id, batch_id))
            active = conn.execute("""SELECT * FROM browser_scan_runs
                WHERE org_id=? AND job_id=? AND batch_id=? AND actor_user_id=?""",
                                  (ctx.org_id, job_id, batch_id,
                                   ctx.user_id)).fetchone()
            if (active is not None and active["status"] == "RUNNING"
                    and not _is_stale(active["started_at"])):
                raise ScanAlreadyRunning(batch_id)
            now = utcnow()
            run_token = uid()
            conn.execute("""INSERT INTO browser_scan_runs
                (org_id,job_id,batch_id,actor_user_id,status,source,browser_account_id,
                 collected,imported,reused,failed,issues_json,run_token,
                 started_at,finished_at,updated_at)
                VALUES(?,?,?,?, 'RUNNING', ?, ?, 0,0,0,0,'[]',?,?,NULL,?)
                ON CONFLICT(org_id,job_id,batch_id,actor_user_id) DO UPDATE SET
                  status='RUNNING', source=excluded.source,
                  browser_account_id=excluded.browser_account_id,
                  collected=0, imported=0, reused=0, failed=0, issues_json='[]',
                  pages_read=0, cards_seen=0, cards_deduped=0, details_opened=0,
                  details_completed=0, details_partial=0, prescreen_skipped=0,
                  stop_reason=NULL, run_token=excluded.run_token,
                  error_code=NULL, error_message=NULL,
                  started_at=excluded.started_at, finished_at=NULL, updated_at=excluded.updated_at""",
                         (ctx.org_id, job_id, batch_id, ctx.user_id, source,
                          browser_account_id, run_token, now, now))
            self.audit(conn, ctx, "BROWSER_SCAN_STARTED", batch_id,
                       {"source": source, "run_token": run_token})
            return self.one(conn, """SELECT * FROM browser_scan_runs
                WHERE org_id=? AND job_id=? AND batch_id=? AND actor_user_id=?""",
                            (ctx.org_id, job_id, batch_id, ctx.user_id))

    def update_browser_scan_progress(self, ctx, job_id, batch_id, run_token, *,
                                     pages_read=None, cards_seen=None,
                                     cards_deduped=None, details_opened=None,
                                     details_completed=None, details_partial=None,
                                     prescreen_skipped=None, collected=None,
                                     imported=None, reused=None, failed=None,
                                     stop_reason=None):
        """Best-effort persisted progress.

        The conditional ``run_token`` predicate is the anti-overwrite fence: a
        late callback from an older run can never mutate a newer run row.
        """
        if not isinstance(run_token, str) or not run_token:
            return False
        values = {
            "pages_read": pages_read, "cards_seen": cards_seen,
            "cards_deduped": cards_deduped, "details_opened": details_opened,
            "details_completed": details_completed,
            "details_partial": details_partial,
            "prescreen_skipped": prescreen_skipped, "collected": collected,
            "imported": imported, "reused": reused, "failed": failed,
            "stop_reason": stop_reason,
        }
        assignments, params = [], []
        for column, value in values.items():
            if value is None:
                continue
            if column != "stop_reason":
                if type(value) is not int or value < 0:
                    raise ValueError(f"{column} must be a non-negative integer")
            assignments.append(f"{column}=?")
            params.append(value)
        if not assignments:
            return False
        params.extend([utcnow(), ctx.org_id, job_id, batch_id, ctx.user_id,
                       run_token])
        with self.tx(ctx, job_id, "collect") as conn:
            cursor = conn.execute(
                "UPDATE browser_scan_runs SET " + ", ".join(assignments) +
                ", updated_at=? WHERE org_id=? AND job_id=? AND batch_id=?"
                " AND actor_user_id=? AND run_token=? AND status='RUNNING'",
                params)
            return cursor.rowcount > 0

    def finish_browser_scan(self, ctx, job_id, batch_id, *, status, collected,
                            imported, reused, failed, issues=None,
                            error_code=None, error_message=None,
                            browser_account_id=None, run_token=None, metrics=None):
        if status not in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
            raise ValueError("Unsupported browser scan status")
        if status == "FAILED" and not (error_code or "").strip():
            raise ValueError("FAILED scans require an error_code")
        metrics = dict(metrics or {})
        for column in ("pages_read", "cards_seen", "cards_deduped",
                       "details_opened", "details_completed",
                       "details_partial", "prescreen_skipped"):
            value = metrics.get(column)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{column} must be a non-negative integer")
        issues_json = json.dumps(issues or [], ensure_ascii=False, sort_keys=True)
        with self.tx(ctx, job_id, "collect") as conn:
            now = utcnow()
            params = [status, int(collected), int(imported), int(reused),
                      int(failed), issues_json, error_code, error_message,
                      metrics.get("pages_read"), metrics.get("cards_seen"),
                      metrics.get("cards_deduped"), metrics.get("details_opened"),
                      metrics.get("details_completed"),
                      metrics.get("details_partial"),
                      metrics.get("prescreen_skipped"),
                      metrics.get("stop_reason"), browser_account_id, now, now,
                      ctx.org_id, job_id, batch_id, ctx.user_id]
            token_clause = ""
            if run_token is not None:
                token_clause = " AND run_token=?"
                params.append(run_token)
            cursor = conn.execute(
                """UPDATE browser_scan_runs SET
                status=?, collected=?, imported=?, reused=?, failed=?, issues_json=?,
                error_code=?, error_message=?,
                pages_read=COALESCE(?,pages_read),
                cards_seen=COALESCE(?,cards_seen),
                cards_deduped=COALESCE(?,cards_deduped),
                details_opened=COALESCE(?,details_opened),
                details_completed=COALESCE(?,details_completed),
                details_partial=COALESCE(?,details_partial),
                prescreen_skipped=COALESCE(?,prescreen_skipped),
                stop_reason=COALESCE(?,stop_reason),
                browser_account_id=COALESCE(?,browser_account_id),
                finished_at=?, updated_at=?
                WHERE org_id=? AND job_id=? AND batch_id=? AND actor_user_id=?"""
                + token_clause, params)
            if cursor.rowcount == 0:
                # A stale callback lost the token race; keep the new run intact.
                return None
            self.audit(conn, ctx, "BROWSER_SCAN_FINISHED", batch_id,
                       {"status": status, "error_code": error_code,
                        "run_token": run_token})
            return self.one(conn, """SELECT * FROM browser_scan_runs
                WHERE org_id=? AND job_id=? AND batch_id=? AND actor_user_id=?""",
                            (ctx.org_id, job_id, batch_id, ctx.user_id))

    def get_browser_scan_run(self, ctx, job_id, batch_id):
        with self.read(ctx, job_id, "view") as conn:
            row = conn.execute("""SELECT * FROM browser_scan_runs
                WHERE org_id=? AND job_id=? AND batch_id=? AND actor_user_id=?""",
                               (ctx.org_id, job_id, batch_id, ctx.user_id)).fetchone()
            return dict(row) if row is not None else None
