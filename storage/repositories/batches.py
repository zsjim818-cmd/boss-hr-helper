"""Job-scoped scanning batches with an immutable published-policy binding."""
from storage.repositories.base import BaseRepository, json_dump, uid, utcnow


class ScanBatchRepository(BaseRepository):
    def create_scan_batch(self, ctx, job_id, batch_id=None):
        with self.tx(ctx, job_id, "collect") as conn:
            job = self.one(conn, "SELECT * FROM jobs WHERE org_id=? AND job_id=?", (ctx.org_id, job_id))
            if job["state"] != "OPEN" or not job["current_policy_id"]:
                raise ValueError("An open job with a published policy is required")
            policy = self.one(conn, "SELECT v.* FROM job_policy_versions v JOIN policy_publications p "
                              "ON p.org_id=v.org_id AND p.job_id=v.job_id AND p.policy_id=v.policy_id "
                              "WHERE v.org_id=? AND v.job_id=? AND v.policy_id=?",
                              (ctx.org_id, job_id, job["current_policy_id"]))
            identifier = batch_id or uid()
            conn.execute("INSERT INTO scan_batches(org_id,job_id,batch_id,policy_id,policy_version,created_by,status,started_at) "
                         "VALUES(?,?,?,?,?,?,'RUNNING',?)",
                         (ctx.org_id, job_id, identifier, policy["policy_id"], policy["policy_version"], ctx.user_id, utcnow()))
            self.audit(conn, ctx, "scan_batch.created", identifier, {"job_id": job_id, "policy_id": policy["policy_id"]})
            return self.one(conn, "SELECT * FROM scan_batches WHERE org_id=? AND job_id=? AND batch_id=?",
                            (ctx.org_id, job_id, identifier))

    def get_scan_batch(self, ctx, job_id, batch_id):
        with self.read(ctx, job_id, "view") as conn:
            return self.one(conn, "SELECT * FROM scan_batches WHERE org_id=? AND job_id=? AND batch_id=?",
                            (ctx.org_id, job_id, batch_id))

    def list_scan_batches(self, ctx, job_id, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer in [1,500]")
        with self.read(ctx, job_id, "view") as conn:
            return [dict(row) for row in conn.execute("""SELECT * FROM scan_batches
                WHERE org_id=? AND job_id=?
                ORDER BY started_at DESC, batch_id DESC LIMIT ?""",
                                                    (ctx.org_id, job_id, limit))]

    def confirm_batch_precheck(self, ctx, job_id, batch_id, *,
                               platform_job_id="", filter_summary="",
                               visible_cards=0):
        """Persist the HR's confirmed page job/filter snapshot on the batch."""
        if platform_job_id is None or not isinstance(platform_job_id, str):
            raise ValueError("platform_job_id must be a string")
        if not isinstance(filter_summary, str):
            raise ValueError("filter_summary must be a string")
        if type(visible_cards) is not int or visible_cards < 0:
            raise ValueError("visible_cards must be a non-negative integer")
        payload = json_dump({"summary": filter_summary[:500],
                             "visible_cards": visible_cards})
        with self.tx(ctx, job_id, "collect") as conn:
            batch = self.one(conn, """SELECT * FROM scan_batches
                WHERE org_id=? AND job_id=? AND batch_id=?""",
                             (ctx.org_id, job_id, batch_id))
            if batch["status"] != "RUNNING":
                raise ValueError("Only a RUNNING batch can accept a precheck confirmation")
            now = utcnow()
            conn.execute("""UPDATE scan_batches
                SET platform_job_id=?, filter_summary_json=?,
                    filters_confirmed_at=?, confirmed_by=?
                WHERE org_id=? AND job_id=? AND batch_id=?""",
                         (platform_job_id.strip()[:200], payload, now,
                          ctx.user_id, ctx.org_id, job_id, batch_id))
            self.audit(conn, ctx, "scan_batch.precheck_confirmed", batch_id,
                       {"job_id": job_id, "platform_job_id": platform_job_id,
                        "visible_cards": visible_cards})
            return self.one(conn, """SELECT * FROM scan_batches
                WHERE org_id=? AND job_id=? AND batch_id=?""",
                            (ctx.org_id, job_id, batch_id))

    def transition_scan_batch(self, ctx, job_id, batch_id, status):
        allowed = {"RUNNING": {"PAUSED", "COMPLETED", "CANCELLED"},
                   "PAUSED": {"RUNNING", "COMPLETED", "CANCELLED"}, "COMPLETED": set(), "CANCELLED": set()}
        with self.tx(ctx, job_id, "collect") as conn:
            batch = self.one(conn, "SELECT * FROM scan_batches WHERE org_id=? AND job_id=? AND batch_id=?",
                             (ctx.org_id, job_id, batch_id))
            if status == batch["status"]:
                return batch
            if status not in allowed.get(batch["status"], set()):
                raise ValueError(f"Illegal scan batch transition: {batch['status']} -> {status}")
            completed = utcnow() if status in ("COMPLETED", "CANCELLED") else None
            conn.execute("UPDATE scan_batches SET status=?,completed_at=? WHERE org_id=? AND job_id=? AND batch_id=?",
                         (status, completed, ctx.org_id, job_id, batch_id))
            self.audit(conn, ctx, "scan_batch.status_changed", batch_id,
                       {"job_id": job_id, "from": batch["status"], "to": status})
            return self.one(conn, "SELECT * FROM scan_batches WHERE org_id=? AND job_id=? AND batch_id=?",
                            (ctx.org_id, job_id, batch_id))
