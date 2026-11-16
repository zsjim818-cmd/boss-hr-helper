"""Candidate acquisition, immutable resumes and first complete scoring inputs."""
from pydantic import ValidationError

from schemas import ResumeData
from storage.repositories.base import BaseRepository, uid, json_dump, digest, utcnow


def prepare_resume(resume):
    raw = resume.model_dump(mode="json", by_alias=True) if isinstance(resume, ResumeData) else resume
    if not isinstance(raw, dict):
        raise ValueError("A resume snapshot must be a JSON object")
    # Short-lived platform security context is never persisted, even when a
    # caller/adapters accidentally pass it through.
    raw = dict(raw)
    if isinstance(raw.get("_meta"), dict):
        meta = dict(raw["_meta"])
        meta.pop("security_id", None)
        raw["_meta"] = meta
    raw_json, raw_hash = json_dump(raw), digest(raw)
    try:
        parsed = ResumeData.model_validate(raw)
        normalized = parsed.model_dump(mode="json", by_alias=True)
        issues = [item.model_dump() for item in parsed.data_issues]
        errors = parsed.collection_errors
        completeness = parsed.data_completeness
        content_hash = digest(parsed.model_dump(mode="json", by_alias=True, exclude={"meta"}))
        scorable = completeness == "COMPLETE" and parsed.ok and not issues and not errors
    except ValidationError as exc:
        normalized, errors = {}, []
        issues = [{"path": ".".join(map(str, error["loc"])), "kind": "MAPPING_ERROR", "message": error["type"]}
                  for error in exc.errors(include_input=False)]
        completeness = "COLLECTION_FAILED" if raw.get("ok") is False else "PARTIAL"
        content_hash, scorable = raw_hash, False
    return {"raw_resume_json": raw_json, "normalized_resume_json": json_dump(normalized),
            "raw_hash": raw_hash, "content_hash": content_hash, "completeness": completeness,
            "collection_errors_json": json_dump(errors), "data_issues_json": json_dump(issues),
            "is_scorable": int(scorable)}


class CandidateRepository(BaseRepository):
    @staticmethod
    def _has_input_versions(conn):
        return conn.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='job_candidate_input_versions'""").fetchone() is not None

    @staticmethod
    def _resume_meta(resume):
        if isinstance(resume, ResumeData):
            return resume.meta.model_dump(mode="json")
        if isinstance(resume, dict):
            meta = resume.get("_meta")
            return dict(meta) if isinstance(meta, dict) else {}
        return {}

    @staticmethod
    def _has_candidate_deletions(conn):
        return conn.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='job_candidate_deletions'""").fetchone() is not None

    @staticmethod
    def _restore_deleted_candidate(conn, ctx, job_id, row):
        """A successful re-scan/import resurrects a soft-deleted candidate."""
        if not CandidateRepository._has_candidate_deletions(conn):
            return
        conn.execute("""DELETE FROM job_candidate_deletions
            WHERE org_id=? AND job_id=? AND job_candidate_id IN (
              SELECT job_candidate_id FROM job_candidates
              WHERE org_id=? AND job_id=? AND candidate_id=?)""",
                     (ctx.org_id, job_id, ctx.org_id, job_id,
                      row["candidate_id"]))

    @staticmethod
    def _ensure_manual_meta(resume, candidate_key):
        """Manual imports may omit platform metadata; synthesize the contract."""
        if not isinstance(resume, dict):
            return resume
        value = dict(resume)
        meta = dict(value.get("_meta") or {})
        meta.setdefault("candidate_key", candidate_key)
        suffix = candidate_key.split(":", 1)[1] if ":" in candidate_key else candidate_key
        meta.setdefault("encrypt_geek_id", suffix or candidate_key)
        meta.setdefault("encrypt_job_id", "")
        value["_meta"] = meta
        return value

    def _validate_binding_identity(self, job_id, candidate_key, resume, source):
        """The binding identity is derived server-side; page metadata cannot override it.

        Platform resumes carry ``_meta.candidate_key``/``encrypt_geek_id``.  The
        binding key is always the explicit ``candidate_key`` argument (computed by
        the collector from ``job_id`` + stable geek id).  For browser-sourced
        imports the key must equal the canonical ``job_id:encrypt_geek_id``;
        a key that claims another job's prefix is rejected for every source.
        """
        meta = self._resume_meta(resume)
        if source == "browser":
            geek = str(meta.get("encrypt_geek_id") or "").strip()
            if not geek:
                raise ValueError(
                    "browser-sourced candidates require a stable encrypt_geek_id")
            canonical = f"{job_id}:{geek}"
            if candidate_key != canonical:
                raise ValueError(
                    "browser candidate_key must be the canonical job_id:encrypt_geek_id")
            # encryptJobId is a platform identifier and is deliberately NOT
            # compared with the internal jobs.job_id (they are different IDs).
            return
        # Manual imports use opaque keys; page metadata never decides the
        # binding.  A key with a colon is allowed but must not masquerade as a
        # browser identity for another job when one is resolvable.
        if ":" in candidate_key:
            prefix, suffix = candidate_key.split(":", 1)
            geek = str(meta.get("encrypt_geek_id") or "").strip()
            if geek and suffix == geek:
                # Looks like a browser key: require the job prefix to match.
                if prefix != job_id:
                    raise ValueError(
                        "candidate_key job prefix does not match the bound job; "
                        "cross-job candidate keys are not accepted")

    def resolve_batch_policy(self, ctx, job_id, batch_id, job_candidate_id):
        """Batch-scoped Policy binding for a candidate re-collected in a new batch.

        The original ``job_candidates.policy_id/version`` row is never rewritten;
        the batch's locked version takes precedence for this scoring context.
        """
        with self.read(ctx, job_id, "view") as conn:
            batch = self.one(conn, """SELECT * FROM scan_batches
                WHERE org_id=? AND job_id=? AND batch_id=?""",
                             (ctx.org_id, job_id, batch_id))
            candidate = self._job_candidate(conn, ctx, job_id, job_candidate_id)
            acquired = conn.execute("""SELECT 1 FROM candidate_acquisitions
                WHERE org_id=? AND job_id=? AND batch_id=? AND job_candidate_id=?""",
                                    (ctx.org_id, job_id, batch_id,
                                     job_candidate_id)).fetchone()
            if acquired is None and candidate["first_batch_id"] != batch_id:
                raise ValueError(
                    "Candidate was not acquired in the requested batch")
            version = self.one(conn, """SELECT * FROM job_policy_versions
                WHERE org_id=? AND job_id=? AND policy_id=?""",
                               (ctx.org_id, job_id, batch["policy_id"]))
            if version["policy_version"] != batch["policy_version"]:
                raise ValueError(
                    "Batch Policy version is inconsistent with the sealed version")
            return {
                "batch_id": batch_id,
                "policy_id": batch["policy_id"],
                "policy_version": batch["policy_version"],
                "policy_hash": version["policy_hash"],
                "candidate_id": candidate["candidate_id"],
                "job_candidate_id": job_candidate_id,
            }

    def expected_candidate_policy(self, ctx, job_id, job_candidate_id,
                                  batch_id=None):
        """Policy identity for a scoring context (batch-scoped when provided)."""
        if batch_id:
            return self.resolve_batch_policy(ctx, job_id, batch_id, job_candidate_id)
        candidate = self.get_job_candidate(ctx, job_id, job_candidate_id)
        return {
            "batch_id": None,
            "policy_id": candidate["policy_id"],
            "policy_version": candidate["policy_version"],
            "candidate_id": candidate["candidate_id"],
            "job_candidate_id": job_candidate_id,
        }

    def create_candidate(self, ctx, job_id, display_name="", candidate_id=None):
        with self.tx(ctx, job_id, "collect") as conn:
            identifier = candidate_id or uid()
            conn.execute("INSERT INTO candidates(org_id,candidate_id,display_name,created_by) VALUES(?,?,?,?)",
                         (ctx.org_id, identifier, display_name, ctx.user_id))
            self.audit(conn, ctx, "candidate.created", identifier, {"job_id": job_id})
            return self.one(conn, "SELECT * FROM candidates WHERE org_id=? AND candidate_id=?", (ctx.org_id, identifier))

    def delete_job_candidate(self, ctx, job_id, job_candidate_id):
        return self.delete_job_candidates(ctx, job_id, [job_candidate_id])

    def delete_job_candidates(self, ctx, job_id, job_candidate_ids):
        """Hard-delete candidates and all job-scoped scoring/audit rows.

        Immutable-audit DELETE guards are temporarily dropped and recreated from
        their original sqlite_master SQL inside the same transaction.  No
        migration is edited and the final schema is byte-for-byte identical.
        """
        if not isinstance(job_candidate_ids, (list, tuple)) or not job_candidate_ids:
            raise ValueError("job_candidate_ids must be a non-empty list")
        if len(job_candidate_ids) > 200:
            raise ValueError("At most 200 candidates can be deleted at once")
        identifiers = []
        for value in job_candidate_ids:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("job_candidate_ids must contain non-empty strings")
            if value not in identifiers:
                identifiers.append(value)
        with self.tx(ctx, job_id, "edit") as conn:
            guards = conn.execute("""SELECT name, sql FROM sqlite_master
                WHERE type='trigger' AND lower(name) LIKE 'no_delete_%'
                  AND sql IS NOT NULL""").fetchall()
            for guard in guards:
                conn.execute(f'DROP TRIGGER IF EXISTS "{guard["name"]}"')
            try:
                deleted = 0
                for job_candidate_id in identifiers:
                    row = self._job_candidate(conn, ctx, job_id, job_candidate_id)
                    params = (ctx.org_id, job_id, job_candidate_id)
                    sub_params = (ctx.org_id, job_id, ctx.org_id, job_id,
                                  job_candidate_id)
                    # Child rows first: contacts/reviews -> evaluations -> tasks.
                    conn.execute("""DELETE FROM pipeline_contact_events
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    conn.execute("""DELETE FROM pipeline_hr_reviews
                        WHERE org_id=? AND job_id=? AND pipeline_evaluation_id IN (
                          SELECT pipeline_evaluation_id FROM pipeline_evaluations
                          WHERE org_id=? AND job_id=? AND job_candidate_id=?)""",
                                 sub_params)
                    conn.execute("""DELETE FROM pipeline_attempts
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    conn.execute("""DELETE FROM pipeline_evaluations
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    conn.execute("""DELETE FROM pipeline_tasks
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    conn.execute("""DELETE FROM hr_review_events
                        WHERE org_id=? AND job_id=? AND evaluation_id IN (
                          SELECT evaluation_id FROM evaluations
                          WHERE org_id=? AND job_id=? AND job_candidate_id=?)""",
                                 sub_params)
                    conn.execute("""DELETE FROM evaluation_attempts
                        WHERE org_id=? AND job_id=? AND task_id IN (
                          SELECT task_id FROM evaluation_tasks
                          WHERE org_id=? AND job_id=? AND job_candidate_id=?)""",
                                 sub_params)
                    conn.execute("""DELETE FROM evaluations
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    conn.execute("""DELETE FROM evaluation_tasks
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    conn.execute("""DELETE FROM job_candidate_input_versions
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    conn.execute("""DELETE FROM job_candidate_inputs
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    conn.execute("""DELETE FROM candidate_acquisitions
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    conn.execute("""DELETE FROM job_candidate_deletions
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    conn.execute("""DELETE FROM job_candidates
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""", params)
                    deleted += 1
                    self.audit(conn, ctx, "candidate.hard_deleted", job_candidate_id,
                               {"job_id": job_id,
                                "candidate_id": row["candidate_id"],
                                "candidate_key": row["candidate_key"]})
            finally:
                for guard in guards:
                    conn.execute(guard["sql"])
            return {"deleted": deleted, "job_candidate_ids": identifiers}

    def get_job_candidate(self, ctx, job_id, job_candidate_id):
        with self.read(ctx, job_id, "view") as conn:
            return self._job_candidate(conn, ctx, job_id, job_candidate_id)

    def get_sealed_input(self, ctx, job_id, job_candidate_id):
        """Current sealed scoring input (versioned after migration 012)."""
        with self.read(ctx, job_id, "view") as conn:
            self._job_candidate(conn, ctx, job_id, job_candidate_id)
            if self._has_input_versions(conn):
                return self.one(conn, """SELECT * FROM job_candidate_inputs_current
                    WHERE org_id=? AND job_id=? AND job_candidate_id=?""",
                                (ctx.org_id, job_id, job_candidate_id))
            return self.one(conn, """SELECT * FROM job_candidate_inputs
                WHERE org_id=? AND job_id=? AND job_candidate_id=?""",
                            (ctx.org_id, job_id, job_candidate_id))

    def get_input_version(self, ctx, job_id, job_candidate_id, input_version):
        with self.read(ctx, job_id, "view") as conn:
            self._job_candidate(conn, ctx, job_id, job_candidate_id)
            return self.one(conn, """SELECT * FROM job_candidate_input_versions
                WHERE org_id=? AND job_id=? AND job_candidate_id=? AND input_version=?""",
                            (ctx.org_id, job_id, job_candidate_id, int(input_version)))

    def list_input_versions(self, ctx, job_id, job_candidate_id):
        with self.read(ctx, job_id, "view") as conn:
            self._job_candidate(conn, ctx, job_id, job_candidate_id)
            return [dict(row) for row in conn.execute("""SELECT * FROM
                job_candidate_input_versions
                WHERE org_id=? AND job_id=? AND job_candidate_id=?
                ORDER BY input_version""",
                (ctx.org_id, job_id, job_candidate_id))]

    def latest_running_batch(self, ctx, job_id):
        with self.read(ctx, job_id, "view") as conn:
            row = conn.execute("""SELECT * FROM scan_batches
                WHERE org_id=? AND job_id=? AND status='RUNNING'
                ORDER BY started_at DESC, batch_id DESC LIMIT 1""",
                               (ctx.org_id, job_id)).fetchone()
            return dict(row) if row is not None else None


    def latest_resume_snapshot(self, ctx, job_id, job_candidate_id):
        with self.read(ctx, job_id, "view") as conn:
            candidate = self._job_candidate(conn, ctx, job_id, job_candidate_id)
            row = conn.execute("""SELECT r.* FROM resume_snapshots r
                JOIN candidate_acquisitions a ON a.org_id=r.org_id
                  AND a.candidate_id=r.candidate_id AND a.resume_id=r.resume_id
                WHERE r.org_id=? AND a.job_id=? AND a.job_candidate_id=?
                ORDER BY a.captured_at DESC, r.captured_at DESC LIMIT 1""",
                               (ctx.org_id, job_id, job_candidate_id)).fetchone()
            return dict(row) if row is not None else None

    def get_resume_snapshot(self, ctx, job_id, job_candidate_id, resume_id):
        with self.read(ctx, job_id, "view") as conn:
            candidate = self._job_candidate(conn, ctx, job_id, job_candidate_id)
            return self.one(conn, "SELECT r.* FROM resume_snapshots r WHERE r.org_id=? AND r.candidate_id=? AND r.resume_id=? "
                            "AND EXISTS(SELECT 1 FROM candidate_acquisitions a WHERE a.org_id=r.org_id "
                            "AND a.candidate_id=r.candidate_id AND a.resume_id=r.resume_id AND a.job_id=? AND a.job_candidate_id=?)",
                            (ctx.org_id, candidate["candidate_id"], resume_id, job_id, job_candidate_id))

    def list_candidate_acquisitions(self, ctx, job_id, job_candidate_id):
        with self.read(ctx, job_id, "view") as conn:
            self._job_candidate(conn, ctx, job_id, job_candidate_id)
            return [dict(row) for row in conn.execute("SELECT * FROM candidate_acquisitions WHERE org_id=? AND job_id=? "
                                                    "AND job_candidate_id=? ORDER BY captured_at,acquisition_id",
                                                    (ctx.org_id, job_id, job_candidate_id))]

    def _job_candidate(self, conn, ctx, job_id, identifier):
        return self.one(conn, "SELECT * FROM job_candidates WHERE org_id=? AND job_id=? AND job_candidate_id=?",
                        (ctx.org_id, job_id, identifier))

    def _active_batch(self, conn, ctx, job_id, batch_id):
        row = self.one(conn, "SELECT * FROM scan_batches WHERE org_id=? AND job_id=? AND batch_id=?",
                       (ctx.org_id, job_id, batch_id))
        if row["status"] != "RUNNING":
            raise ValueError("Acquisition requires an explicitly selected RUNNING scan batch")
        return row

    def _alias(self, conn, ctx, source, external_id, candidate_id):
        hashed = digest(external_id)
        existing = conn.execute("SELECT candidate_id FROM candidate_identity_aliases WHERE org_id=? AND source=? AND external_identity_hash=?",
                                (ctx.org_id, source, hashed)).fetchone()
        if existing and existing["candidate_id"] != candidate_id:
            raise ValueError("Candidate identity alias conflicts with an existing candidate")
        if not existing:
            conn.execute("INSERT INTO candidate_identity_aliases(org_id,source,external_identity_hash,candidate_id,external_identity_json) "
                         "VALUES(?,?,?,?,?)", (ctx.org_id, source, hashed, candidate_id, json_dump(external_id)))

    def _resolve_candidate(self, conn, ctx, job_id, candidate_key, source, external_id, candidate_id, resume):
        existing = conn.execute("SELECT * FROM job_candidates WHERE org_id=? AND job_id=? AND candidate_key=?",
                                (ctx.org_id, job_id, candidate_key)).fetchone()
        identifiers = [candidate_id] if candidate_id else []
        if existing:
            identifiers.append(existing["candidate_id"])
        aliases = [(source, {"kind": "job_candidate_key", "job_id": job_id, "value": candidate_key})]
        if external_id is not None:
            aliases.append((source, {"kind": "stable_external_id", "value": external_id}))
        for namespace, value in aliases:
            alias = conn.execute("SELECT candidate_id FROM candidate_identity_aliases WHERE org_id=? AND source=? AND external_identity_hash=?",
                                 (ctx.org_id, namespace, digest(value))).fetchone()
            if alias:
                identifiers.append(alias["candidate_id"])
        if len(set(identifiers)) > 1:
            raise ValueError("Candidate identity conflicts; manual reconciliation is required")
        identifier = identifiers[0] if identifiers else uid()
        if identifiers:
            self.one(conn, "SELECT * FROM candidates WHERE org_id=? AND candidate_id=?", (ctx.org_id, identifier))
        else:
            name = resume.get("name", "") if isinstance(resume, dict) else getattr(resume, "name", "")
            conn.execute("INSERT INTO candidates(org_id,candidate_id,display_name,created_by) VALUES(?,?,?,?)",
                         (ctx.org_id, identifier, name if isinstance(name, str) else "", ctx.user_id))
        for namespace, value in aliases:
            self._alias(conn, ctx, namespace, value, identifier)
        return identifier

    def _snapshot(self, conn, ctx, candidate_id, resume):
        payload = prepare_resume(resume)
        existing = conn.execute("SELECT * FROM resume_snapshots WHERE org_id=? AND candidate_id=? AND raw_hash=?",
                                (ctx.org_id, candidate_id, payload["raw_hash"])).fetchone()
        if existing:
            return dict(existing)
        identifier = uid()
        conn.execute("INSERT INTO resume_snapshots(org_id,candidate_id,resume_id,raw_resume_json,normalized_resume_json,content_hash,raw_hash,"
                     "completeness,collection_errors_json,data_issues_json,is_scorable,captured_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (ctx.org_id, candidate_id, identifier, payload["raw_resume_json"], payload["normalized_resume_json"],
                      payload["content_hash"], payload["raw_hash"], payload["completeness"], payload["collection_errors_json"],
                      payload["data_issues_json"], payload["is_scorable"], ctx.user_id))
        return self.one(conn, "SELECT * FROM resume_snapshots WHERE org_id=? AND candidate_id=? AND resume_id=?",
                        (ctx.org_id, candidate_id, identifier))

    def _acquisition(self, conn, ctx, job_id, row, batch_id, snapshot, source, capture_id=None):
        identifier = capture_id or uid()
        resume_id = snapshot["resume_id"] if snapshot else None
        existing = conn.execute("SELECT * FROM candidate_acquisitions WHERE org_id=? AND job_id=? AND acquisition_id=?",
                                (ctx.org_id, job_id, identifier)).fetchone()
        if existing:
            if (existing["job_candidate_id"], existing["batch_id"], existing["resume_id"], existing["source"]) != (row["job_candidate_id"], batch_id, resume_id, source):
                raise ValueError("Acquisition idempotency key was reused for different input")
            return dict(existing)
        conn.execute("INSERT INTO candidate_acquisitions(org_id,job_id,acquisition_id,job_candidate_id,candidate_id,batch_id,resume_id,source,captured_by) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (ctx.org_id, job_id, identifier, row["job_candidate_id"], row["candidate_id"],
                                                  batch_id, resume_id, source, ctx.user_id))
        return self.one(conn, "SELECT * FROM candidate_acquisitions WHERE org_id=? AND job_id=? AND acquisition_id=?",
                        (ctx.org_id, job_id, identifier))

    def _seal(self, conn, ctx, job_id, row, resume_id):
        existing = conn.execute("SELECT * FROM job_candidate_inputs WHERE org_id=? AND job_id=? AND job_candidate_id=?",
                                (ctx.org_id, job_id, row["job_candidate_id"])).fetchone()
        if existing:
            return dict(existing)
        snapshot = self.one(conn, "SELECT * FROM resume_snapshots WHERE org_id=? AND candidate_id=? AND resume_id=?",
                            (ctx.org_id, row["candidate_id"], resume_id))
        if not snapshot["is_scorable"]:
            raise ValueError("Only a complete, valid resume without collection or mapping errors can be sealed")
        conn.execute("INSERT INTO job_candidate_inputs(org_id,job_id,job_candidate_id,candidate_id,policy_id,policy_version,resume_id,sealed_by) "
                     "VALUES(?,?,?,?,?,?,?,?)", (ctx.org_id, job_id, row["job_candidate_id"], row["candidate_id"], row["policy_id"],
                                                row["policy_version"], resume_id, ctx.user_id))
        if self._has_input_versions(conn):
            conn.execute("""INSERT INTO job_candidate_input_versions
                (org_id,job_id,job_candidate_id,input_version,candidate_id,policy_id,
                 policy_version,resume_id,sealed_by,is_current)
                VALUES(?,?,?,1,?,?,?,?,?,1)""",
                         (ctx.org_id, job_id, row["job_candidate_id"], row["candidate_id"],
                          row["policy_id"], row["policy_version"], resume_id, ctx.user_id))
            self.audit(conn, ctx, "candidate_input.sealed", row["job_candidate_id"],
                       {"job_id": job_id, "resume_id": resume_id,
                        "policy_id": row["policy_id"], "input_version": 1})
            return self.one(conn, """SELECT * FROM job_candidate_input_versions
                WHERE org_id=? AND job_id=? AND job_candidate_id=? AND input_version=1""",
                            (ctx.org_id, job_id, row["job_candidate_id"]))
        self.audit(conn, ctx, "candidate_input.sealed", row["job_candidate_id"], {"job_id": job_id, "resume_id": resume_id, "policy_id": row["policy_id"]})
        return self.one(conn, "SELECT * FROM job_candidate_inputs WHERE org_id=? AND job_id=? AND job_candidate_id=?",
                        (ctx.org_id, job_id, row["job_candidate_id"]))

    def _seal_version(self, conn, ctx, job_id, row, resume_id, *, reason="RESUME_RECOLLECTED"):
        """Append a new current input version; never rewrite history."""
        if not self._has_input_versions(conn):
            raise ValueError(
                "INPUT_VERSIONING_REQUIRES_012: sealed inputs are immutable; "
                "apply migration 012 before re-scoring enriched resumes")
        snapshot = self.one(conn, """SELECT * FROM resume_snapshots
            WHERE org_id=? AND candidate_id=? AND resume_id=?""",
                            (ctx.org_id, row["candidate_id"], resume_id))
        if not snapshot["is_scorable"]:
            raise ValueError("Only a complete, valid resume can become a sealed input version")
        current = conn.execute("""SELECT * FROM job_candidate_input_versions
            WHERE org_id=? AND job_id=? AND job_candidate_id=? AND is_current=1""",
                               (ctx.org_id, job_id, row["job_candidate_id"])).fetchone()
        if current is not None and current["resume_id"] == resume_id:
            return dict(current), False
        version = 1 if current is None else int(current["input_version"]) + 1
        now = utcnow()
        legacy = conn.execute("""SELECT 1 FROM job_candidate_inputs
            WHERE org_id=? AND job_id=? AND job_candidate_id=?""",
                              (ctx.org_id, job_id, row["job_candidate_id"])).fetchone()
        if legacy is None:
            conn.execute("""INSERT INTO job_candidate_inputs
                (org_id,job_id,job_candidate_id,candidate_id,policy_id,
                 policy_version,resume_id,sealed_by)
                VALUES(?,?,?,?,?,?,?,?)""",
                         (ctx.org_id, job_id, row["job_candidate_id"],
                          row["candidate_id"], row["policy_id"],
                          row["policy_version"], resume_id, ctx.user_id))
        if current is not None:
            conn.execute("""UPDATE job_candidate_input_versions
                SET is_current=0, superseded_at=?, superseded_by=?, supersede_reason=?
                WHERE org_id=? AND job_id=? AND job_candidate_id=? AND input_version=?""",
                         (now, ctx.user_id, reason, ctx.org_id, job_id,
                          row["job_candidate_id"], current["input_version"]))
        conn.execute("""INSERT INTO job_candidate_input_versions
            (org_id,job_id,job_candidate_id,input_version,candidate_id,policy_id,
             policy_version,resume_id,sealed_by,is_current)
            VALUES(?,?,?,?,?,?,?,?,?,1)""",
                     (ctx.org_id, job_id, row["job_candidate_id"], version,
                      row["candidate_id"], row["policy_id"], row["policy_version"],
                      resume_id, ctx.user_id))
        self.audit(conn, ctx, "candidate_input.version_sealed",
                   row["job_candidate_id"],
                   {"job_id": job_id, "resume_id": resume_id,
                    "input_version": version, "reason": reason})
        return self.one(conn, """SELECT * FROM job_candidate_input_versions
            WHERE org_id=? AND job_id=? AND job_candidate_id=? AND input_version=?""",
                        (ctx.org_id, job_id, row["job_candidate_id"], version)), True


    def acquire_candidate_for_job(self, ctx, job_id, batch_id, candidate_key, *, source="boss", external_id=None,
                                  resume=None, candidate_id=None, capture_id=None):
        if not isinstance(candidate_key, str) or not candidate_key.strip() or not isinstance(source, str) or not source.strip():
            raise ValueError("candidate_key and source must be non-empty strings")
        if external_id is not None and (not isinstance(external_id, str) or not external_id.strip()):
            raise ValueError("external_id must be a non-empty stable source identifier")
        if source != "browser" and resume is not None:
            resume = self._ensure_manual_meta(resume, candidate_key)
        self._validate_binding_identity(job_id, candidate_key, resume, source)
        with self.tx(ctx, job_id, "collect") as conn:
            batch = self._active_batch(conn, ctx, job_id, batch_id)
            identifier = self._resolve_candidate(conn, ctx, job_id, candidate_key, source, external_id, candidate_id, resume)
            existing = conn.execute("SELECT * FROM job_candidates WHERE org_id=? AND job_id=? AND candidate_id=?",
                                    (ctx.org_id, job_id, identifier)).fetchone()
            is_new = existing is None
            if is_new:
                jcid = uid()
                conn.execute("INSERT INTO job_candidates(org_id,job_id,job_candidate_id,candidate_id,candidate_key,policy_id,policy_version,first_batch_id) "
                             "VALUES(?,?,?,?,?,?,?,?)", (ctx.org_id, job_id, jcid, identifier, candidate_key, batch["policy_id"], batch["policy_version"], batch_id))
                row = self._job_candidate(conn, ctx, job_id, jcid)
            else:
                row = dict(existing)
            snapshot = self._snapshot(conn, ctx, identifier, resume) if resume is not None else None
            acquisition = self._acquisition(conn, ctx, job_id, row, batch_id, snapshot, source, capture_id)
            self._restore_deleted_candidate(conn, ctx, job_id, row)
            sealed = None
            if snapshot is not None and snapshot["is_scorable"]:
                table = ("job_candidate_inputs_current"
                         if self._has_input_versions(conn)
                         else "job_candidate_inputs")
                existing_input = conn.execute(
                    f"""SELECT * FROM {table}
                        WHERE org_id=? AND job_id=? AND job_candidate_id=?""",
                    (ctx.org_id, job_id, row["job_candidate_id"])).fetchone()
                if existing_input is not None:
                    # A routine re-scan never replaces an audited sealed input.
                    # Version changes are explicit (re-extract/reseal/append).
                    sealed = dict(existing_input)
                elif self._has_input_versions(conn):
                    sealed, _ = self._seal_version(conn, ctx, job_id, row,
                                                   snapshot["resume_id"])
                else:
                    sealed = self._seal(conn, ctx, job_id, row,
                                        snapshot["resume_id"])
            if sealed is None:
                table = ("job_candidate_inputs_current"
                         if self._has_input_versions(conn)
                         else "job_candidate_inputs")
                found = conn.execute(f"SELECT * FROM {table} WHERE org_id=? AND job_id=? AND job_candidate_id=?",
                                     (ctx.org_id, job_id, row["job_candidate_id"])).fetchone()
                sealed = dict(found) if found else None
            self.audit(conn, ctx, "candidate.acquired", acquisition["acquisition_id"], {"job_id": job_id, "batch_id": batch_id, "is_new": is_new})
            return {"job_candidate": row, "acquisition": acquisition, "resume_snapshot": snapshot, "sealed_input": sealed, "is_new": is_new}

    def append_resume_snapshot(self, ctx, job_id, job_candidate_id, resume, *, batch_id):
        with self.tx(ctx, job_id, "collect") as conn:
            self._active_batch(conn, ctx, job_id, batch_id)
            row = self._job_candidate(conn, ctx, job_id, job_candidate_id)
            snapshot = self._snapshot(conn, ctx, row["candidate_id"], resume)
            self._acquisition(conn, ctx, job_id, row, batch_id, snapshot, "manual")
            if snapshot["is_scorable"]:
                self._seal_version(conn, ctx, job_id, row, snapshot["resume_id"])
            self.audit(conn, ctx, "resume.appended", snapshot["resume_id"], {"job_id": job_id, "job_candidate_id": job_candidate_id, "batch_id": batch_id})
            return snapshot

    def seal_candidate_input(self, ctx, job_id, job_candidate_id, resume_id):
        with self.tx(ctx, job_id, "collect") as conn:
            row = self._job_candidate(conn, ctx, job_id, job_candidate_id)
            return self._seal(conn, ctx, job_id, row, resume_id)

    def reseal_candidate_input(self, ctx, job_id, job_candidate_id, resume_id):
        """Append a new current input version from an already acquired snapshot.

        Migration 012 keeps the legacy ``job_candidate_inputs`` row and every
        historical version untouched; only ``is_current`` moves.  The previous
        evaluation remains immutable and readable.
        """
        with self.tx(ctx, job_id, "collect") as conn:
            row = self._job_candidate(conn, ctx, job_id, job_candidate_id)
            snapshot = self.one(conn, """SELECT * FROM resume_snapshots
                WHERE org_id=? AND candidate_id=? AND resume_id=?""",
                                (ctx.org_id, row["candidate_id"], resume_id))
            if not snapshot["is_scorable"]:
                raise ValueError(
                    "Only a complete, valid resume can replace the sealed input")
            acquired = conn.execute("""SELECT 1 FROM candidate_acquisitions
                WHERE org_id=? AND job_id=? AND job_candidate_id=?
                  AND candidate_id=? AND resume_id=?""",
                                    (ctx.org_id, job_id, job_candidate_id,
                                     row["candidate_id"], resume_id)).fetchone()
            if acquired is None:
                raise ValueError(
                    "The replacement resume must be acquired for this candidate/job")
            sealed, _ = self._seal_version(conn, ctx, job_id, row, resume_id)
            return sealed

    def append_candidate_input_version(self, ctx, job_id, job_candidate_id,
                                       resume, *, batch_id=None,
                                       source="browser"):
        """Acquire a fresh detail snapshot and seal it as the next input version.

        PARTIAL snapshots are stored for diagnosis/audit but never become a
        scoring input, so a still-incomplete resume cannot unlock scoring.
        """
        with self.tx(ctx, job_id, "collect") as conn:
            row = self._job_candidate(conn, ctx, job_id, job_candidate_id)
            if batch_id is None:
                found = conn.execute("""SELECT * FROM scan_batches
                    WHERE org_id=? AND job_id=? AND status='RUNNING'
                    ORDER BY started_at DESC, batch_id DESC LIMIT 1""",
                                     (ctx.org_id, job_id)).fetchone()
                if found is None:
                    raise ValueError("NO_RUNNING_BATCH: create a RUNNING batch first")
                batch_id = found["batch_id"]
            self._active_batch(conn, ctx, job_id, batch_id)
            snapshot = self._snapshot(conn, ctx, row["candidate_id"], resume)
            self._acquisition(conn, ctx, job_id, row, batch_id, snapshot, source)
            sealed = None
            created = False
            if snapshot["is_scorable"]:
                sealed, created = self._seal_version(
                    conn, ctx, job_id, row, snapshot["resume_id"])
            else:
                found = conn.execute("""SELECT * FROM job_candidate_inputs_current
                    WHERE org_id=? AND job_id=? AND job_candidate_id=?""",
                                     (ctx.org_id, job_id, job_candidate_id)).fetchone()
                sealed = dict(found) if found is not None else None
            self.audit(conn, ctx, "resume.reextracted", snapshot["resume_id"],
                       {"job_id": job_id, "job_candidate_id": job_candidate_id,
                        "batch_id": batch_id, "sealed": sealed is not None,
                        "new_version": created})
            return {"snapshot": snapshot, "sealed_input": sealed,
                    "input_version": (sealed or {}).get("input_version"),
                    "is_new_version": created,
                    "batch_id": batch_id}
