"""Append-only compiler provenance, human confirmation and publication evidence."""
import json

from .base import BaseRepository, check_snapshot, digest, json_dump, uid


class CompilerRepository(BaseRepository):
    @staticmethod
    def _validate_compiled_policy(policy_json, source_json):
        from config.policy_validation import validate_publishable_policy
        return validate_publishable_policy(policy_json, source_json)

    def _current_compilation_draft(self, connection, ctx, job_id, draft_id, run):
        draft = self.one(connection,
            "SELECT * FROM policy_drafts WHERE org_id=? AND job_id=? AND draft_id=?",
            (ctx.org_id, job_id, draft_id))
        if (run["status"] != "SUCCEEDED" or not run["publishable"]
                or run["draft_id"] != draft_id or run["draft_revision"] != draft["revision"]
                or run["draft_hash"] != draft["draft_hash"]
                or run["policy_json"] != draft["policy_json"]
                or run["source_json"] != draft["source_json"]):
            raise ValueError("Compilation does not validate the exact current draft revision and source")
        self._validate_compiled_policy(json.loads(draft["policy_json"]), json.loads(draft["source_json"]))
        issues = json.loads(draft["diagnostics_json"])
        if any(not isinstance(item, dict) or item.get("severity") in ("ERROR", "CONFIRM") for item in issues):
            raise ValueError("The current draft still has blocking diagnostics")
        return draft

    def record_policy_compilation(self, ctx, job_id, source_json, *, status, provenance,
                                  metrics=None, policy_json=None, draft_id=None, draft_revision=None,
                                  diagnostics=None, error_type=None, run_kind="LLM", parent_run_id=None,
                                  run_id=None):
        """Persist a completed call/validation; never call a model in a transaction.

        SUCCEEDED means structurally valid and publishable. A failed business
        validation can retain a draft and policy payload as a FAILED run.
        """
        check_snapshot(source_json)
        check_snapshot(provenance)
        check_snapshot(metrics if metrics is not None else {})
        if status not in ("SUCCEEDED", "FAILED"):
            raise ValueError("Compilation status must be SUCCEEDED or FAILED")
        if run_kind not in ("LLM", "VALIDATION") or ((run_kind == "VALIDATION") != (parent_run_id is not None)):
            raise ValueError("Revalidation requires its original compilation run")
        keys = ("compiler_version", "prompt_version", "validator_version", "model_version")
        if any(not isinstance(provenance.get(key), str) or not provenance[key].strip() for key in keys):
            raise ValueError("Compiler, prompt, validator and model versions are required")
        if status == "FAILED" and (not isinstance(error_type, str) or not error_type.strip()):
            raise ValueError("A failed compilation requires a concrete error type")
        if status == "SUCCEEDED" and error_type is not None:
            raise ValueError("A successful compilation cannot have an error type")
        if policy_json is not None:
            check_snapshot(policy_json)
        issues = [] if diagnostics is None else diagnostics
        if not isinstance(issues, list):
            raise ValueError("Diagnostics must be an array")
        json_dump(issues)
        identifier = run_id or uid()
        with self.tx(ctx, job_id, "edit") as connection:
            if parent_run_id is not None:
                parent = self.one(connection, "SELECT * FROM policy_compilation_runs WHERE org_id=? AND job_id=? AND run_id=?",
                                  (ctx.org_id, job_id, parent_run_id))
                if (draft_id is None or parent["draft_id"] != draft_id or parent["draft_revision"] is None
                        or draft_revision is None or draft_revision <= parent["draft_revision"]):
                    raise ValueError("Revalidation must retain an earlier compilation of the same draft")
            draft_hash = None
            if draft_id is not None:
                draft = self.one(connection,
                    "SELECT * FROM policy_drafts WHERE org_id=? AND job_id=? AND draft_id=?",
                    (ctx.org_id, job_id, draft_id))
                if draft_revision != draft["revision"] or draft["source_json"] != json_dump(source_json):
                    raise ValueError("Compilation input must match the current draft revision")
                if policy_json is not None and draft["policy_json"] != json_dump(policy_json):
                    raise ValueError("Compilation policy must match the current draft")
                draft_hash = draft["draft_hash"]
            elif draft_revision is not None:
                raise ValueError("Draft revision requires draft_id")
            if status == "SUCCEEDED":
                if draft_id is None or policy_json is None:
                    raise ValueError("Successful compilation requires its persisted draft")
                from config.policy_validation import diagnose_policy
                parsed = self._validate_compiled_policy(policy_json, source_json)
                if parsed.provenance.model_dump() != {key: provenance[key] for key in keys}:
                    raise ValueError("Recorded provenance must match the frozen policy provenance")
                issues = [item.model_dump() for item in diagnose_policy(parsed, source_json)]
                draft_issues = json.loads(draft["diagnostics_json"])
                if any(not isinstance(item, dict) or item.get("severity") in ("ERROR", "CONFIRM") for item in draft_issues):
                    raise ValueError("Draft diagnostics still block successful validation")
            connection.execute("""INSERT INTO policy_compilation_runs
                (org_id,job_id,run_id,run_kind,parent_run_id,source_json,input_hash,draft_id,draft_revision,draft_hash,
                 policy_json,policy_hash,status,publishable,diagnostics_json,compiler_version,prompt_version,
                 validator_version,policy_schema_version,model_version,metrics_json,error_type,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ctx.org_id, job_id, identifier, run_kind, parent_run_id, json_dump(source_json), digest(source_json),
                 draft_id, draft_revision, draft_hash, json_dump(policy_json) if policy_json is not None else None,
                 digest(policy_json) if policy_json is not None else None, status, int(status == "SUCCEEDED"),
                 json_dump(issues), provenance["compiler_version"], provenance["prompt_version"],
                 provenance["validator_version"], "job_scoring_policy_v1", provenance["model_version"],
                 json_dump(metrics if metrics is not None else {}), error_type, ctx.user_id))
            self.audit(connection, ctx, "POLICY_COMPILATION_RECORDED", identifier,
                       {"job_id": job_id, "status": status, "draft_id": draft_id, "run_kind": run_kind})
            return self.one(connection,
                "SELECT * FROM policy_compilation_runs WHERE org_id=? AND job_id=? AND run_id=?",
                (ctx.org_id, job_id, identifier))

    def get_policy_compilation(self, ctx, job_id, run_id):
        with self.read(ctx, job_id) as connection:
            return self.one(connection,
                "SELECT * FROM policy_compilation_runs WHERE org_id=? AND job_id=? AND run_id=?",
                (ctx.org_id, job_id, run_id))

    def list_policy_compilations(self, ctx, job_id, draft_id=None):
        with self.read(ctx, job_id) as connection:
            rows = connection.execute("""SELECT * FROM policy_compilation_runs WHERE org_id=? AND job_id=?
                AND (? IS NULL OR draft_id=?) ORDER BY created_at,run_id""",
                (ctx.org_id, job_id, draft_id, draft_id))
            return [dict(row) for row in rows]

    def confirm_policy_draft(self, ctx, job_id, draft_id, run_id, confirmation_hash, *, confirmation_id=None):
        with self.tx(ctx, job_id, "publish") as connection:
            run = self.one(connection,
                "SELECT * FROM policy_compilation_runs WHERE org_id=? AND job_id=? AND run_id=?",
                (ctx.org_id, job_id, run_id))
            draft = self._current_compilation_draft(connection, ctx, job_id, draft_id, run)
            if confirmation_hash != draft["draft_hash"]:
                raise ValueError("HR must explicitly confirm the complete current draft hash")
            identifier = confirmation_id or uid()
            connection.execute("""INSERT INTO policy_confirmations
                (org_id,job_id,confirmation_id,draft_id,draft_revision,draft_hash,policy_hash,run_id,confirmed_by)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (ctx.org_id, job_id, identifier, draft_id, draft["revision"], draft["draft_hash"],
                 run["policy_hash"], run_id, ctx.user_id))
            self.audit(connection, ctx, "POLICY_DRAFT_CONFIRMED", identifier,
                       {"job_id": job_id, "draft_id": draft_id, "revision": draft["revision"], "draft_hash": draft["draft_hash"]})
            return self.one(connection,
                "SELECT * FROM policy_confirmations WHERE org_id=? AND job_id=? AND confirmation_id=?",
                (ctx.org_id, job_id, identifier))

    def get_policy_confirmation(self, ctx, job_id, confirmation_id):
        with self.read(ctx, job_id) as connection:
            return self.one(connection,
                "SELECT * FROM policy_confirmations WHERE org_id=? AND job_id=? AND confirmation_id=?",
                (ctx.org_id, job_id, confirmation_id))

    def attest_policy_version(self, ctx, job_id, policy_id, run_id, confirmation_id):
        """Link a sealed version to validation and HR facts before publication."""
        with self.tx(ctx, job_id, "publish") as connection:
            return self._attest_policy_version(connection, ctx, job_id, policy_id, run_id, confirmation_id)

    def _attest_policy_version(self, connection, ctx, job_id, policy_id, run_id, confirmation_id):
        """Internal hook for atomically sealing a version and its attestations."""
        self._authorize(connection, ctx, job_id, "publish")
        version = self.one(connection,
            "SELECT * FROM job_policy_versions WHERE org_id=? AND job_id=? AND policy_id=?",
            (ctx.org_id, job_id, policy_id))
        run = self.one(connection,
            "SELECT * FROM policy_compilation_runs WHERE org_id=? AND job_id=? AND run_id=?",
            (ctx.org_id, job_id, run_id))
        confirmation = self.one(connection,
            "SELECT * FROM policy_confirmations WHERE org_id=? AND job_id=? AND confirmation_id=?",
            (ctx.org_id, job_id, confirmation_id))
        self._current_compilation_draft(connection, ctx, job_id, version["source_draft_id"], run)
        if (version["policy_schema_version"] != "job_scoring_policy_v1"
                or version["source_draft_hash"] != run["draft_hash"]
                or version["draft_revision"] != run["draft_revision"]
                or version["policy_json"] != run["policy_json"]
                or version["source_snapshot_json"] != run["source_json"]
                or confirmation["run_id"] != run_id or confirmation["draft_hash"] != run["draft_hash"]):
            raise ValueError("Version, validation and HR confirmation must refer to the same exact draft")
        connection.execute("""INSERT INTO policy_version_compilations
            (org_id,job_id,policy_id,run_id,confirmation_id,created_by) VALUES(?,?,?,?,?,?)""",
            (ctx.org_id, job_id, policy_id, run_id, confirmation_id, ctx.user_id))
        self.audit(connection, ctx, "POLICY_VERSION_ATTESTED", policy_id,
                   {"job_id": job_id, "run_id": run_id, "confirmation_id": confirmation_id})
        return self.one(connection,
            "SELECT * FROM policy_version_compilations WHERE org_id=? AND job_id=? AND policy_id=?",
            (ctx.org_id, job_id, policy_id))
