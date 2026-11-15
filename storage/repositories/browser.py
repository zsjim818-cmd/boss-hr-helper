"""Persistent browser account bindings and batch scan runs (migration 011)."""
import json

from .base import BaseRepository, NotFound, uid, utcnow


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
            now = utcnow()
            conn.execute("""INSERT INTO browser_scan_runs
                (org_id,job_id,batch_id,actor_user_id,status,source,browser_account_id,
                 collected,imported,reused,failed,issues_json,started_at,finished_at,updated_at)
                VALUES(?,?,?,?, 'RUNNING', ?, ?, 0,0,0,0,'[]',?,NULL,?)
                ON CONFLICT(org_id,job_id,batch_id,actor_user_id) DO UPDATE SET
                  status='RUNNING', source=excluded.source,
                  browser_account_id=excluded.browser_account_id,
                  collected=0, imported=0, reused=0, failed=0, issues_json='[]',
                  error_code=NULL, error_message=NULL,
                  started_at=excluded.started_at, finished_at=NULL, updated_at=excluded.updated_at""",
                         (ctx.org_id, job_id, batch_id, ctx.user_id, source,
                          browser_account_id, now, now))
            self.audit(conn, ctx, "BROWSER_SCAN_STARTED", batch_id,
                       {"source": source})
            return self.one(conn, """SELECT * FROM browser_scan_runs
                WHERE org_id=? AND job_id=? AND batch_id=? AND actor_user_id=?""",
                            (ctx.org_id, job_id, batch_id, ctx.user_id))

    def finish_browser_scan(self, ctx, job_id, batch_id, *, status, collected,
                            imported, reused, failed, issues=None,
                            error_code=None, error_message=None,
                            browser_account_id=None):
        if status not in ("COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"):
            raise ValueError("Unsupported browser scan status")
        if status == "FAILED" and not (error_code or "").strip():
            raise ValueError("FAILED scans require an error_code")
        issues_json = json.dumps(issues or [], ensure_ascii=False, sort_keys=True)
        with self.tx(ctx, job_id, "collect") as conn:
            now = utcnow()
            conn.execute("""UPDATE browser_scan_runs SET
                status=?, collected=?, imported=?, reused=?, failed=?, issues_json=?,
                error_code=?, error_message=?, browser_account_id=COALESCE(?,browser_account_id),
                finished_at=?, updated_at=?
                WHERE org_id=? AND job_id=? AND batch_id=? AND actor_user_id=?""",
                         (status, int(collected), int(imported), int(reused),
                          int(failed), issues_json, error_code, error_message,
                          browser_account_id, now, now, ctx.org_id, job_id,
                          batch_id, ctx.user_id))
            if conn.total_changes == 0:
                raise NotFound("No browser scan run for this batch/actor")
            self.audit(conn, ctx, "BROWSER_SCAN_FINISHED", batch_id,
                       {"status": status, "error_code": error_code})
            return self.one(conn, """SELECT * FROM browser_scan_runs
                WHERE org_id=? AND job_id=? AND batch_id=? AND actor_user_id=?""",
                            (ctx.org_id, job_id, batch_id, ctx.user_id))

    def get_browser_scan_run(self, ctx, job_id, batch_id):
        with self.read(ctx, job_id, "view") as conn:
            row = conn.execute("""SELECT * FROM browser_scan_runs
                WHERE org_id=? AND job_id=? AND batch_id=? AND actor_user_id=?""",
                               (ctx.org_id, job_id, batch_id, ctx.user_id)).fetchone()
            return dict(row) if row is not None else None
