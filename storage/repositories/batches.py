"""Job-scoped scanning batches with an immutable published-policy binding."""
from storage.repositories.base import BaseRepository, uid, utcnow


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
