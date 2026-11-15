"""Immutable legacy snapshots and validated generic compiler policy versions."""
import json

from .base import BaseRepository, check_snapshot, digest, json_dump, uid, utcnow


class PolicyRepository(BaseRepository):
    def create_execution_profile(self, context, profile_json, *, profile_id=None):
        check_snapshot(profile_json)
        profile_id = profile_id or uid()
        with self.tx(context) as connection:
            connection.execute("""INSERT INTO execution_profiles
                (org_id,profile_id,profile_json,profile_hash,created_by) VALUES(?,?,?,?,?)""",
                (context.org_id, profile_id, json_dump(profile_json), digest(profile_json), context.user_id))
            self.audit(connection, context, "EXECUTION_PROFILE_CREATED", profile_id)
            return self.one(connection, "SELECT * FROM execution_profiles WHERE org_id=? AND profile_id=?",
                            (context.org_id, profile_id))

    def get_execution_profile(self, context, profile_id):
        with self.read(context) as connection:
            return self.one(connection, "SELECT * FROM execution_profiles WHERE org_id=? AND profile_id=?",
                            (context.org_id, profile_id))

    def create_policy_draft(self, context, job_id, policy_json, *, source_json=None,
                            diagnostics=None, draft_id=None):
        check_snapshot(policy_json)
        source_json = {} if source_json is None else source_json
        check_snapshot(source_json)
        diagnostics = [] if diagnostics is None else diagnostics
        if not isinstance(diagnostics, list):
            raise ValueError("Draft diagnostics must be an array")
        draft_id = draft_id or uid()
        fingerprint = digest({"source": source_json, "policy": policy_json, "diagnostics": diagnostics})
        with self.tx(context, job_id, "edit") as connection:
            connection.execute("""INSERT INTO policy_drafts
                (org_id,job_id,draft_id,source_json,policy_json,diagnostics_json,draft_hash,edited_by)
                VALUES(?,?,?,?,?,?,?,?)""", (context.org_id, job_id, draft_id,
                    json_dump(source_json), json_dump(policy_json), json_dump(diagnostics), fingerprint, context.user_id))
            self.audit(connection, context, "POLICY_DRAFT_CREATED", draft_id, {"hash": fingerprint})
            return self.one(connection, "SELECT * FROM policy_drafts WHERE org_id=? AND job_id=? AND draft_id=?",
                            (context.org_id, job_id, draft_id))

    def update_policy_draft(self, context, job_id, draft_id, policy_json, *, expected_revision,
                            source_json=None, diagnostics=None):
        check_snapshot(policy_json)
        with self.tx(context, job_id, "edit") as connection:
            draft = self.one(connection, "SELECT * FROM policy_drafts WHERE org_id=? AND job_id=? AND draft_id=?",
                             (context.org_id, job_id, draft_id))
            if draft["revision"] != expected_revision:
                raise ValueError("Draft revision conflict")
            source = json.loads(draft["source_json"]) if source_json is None else source_json
            issues = json.loads(draft["diagnostics_json"]) if diagnostics is None else diagnostics
            check_snapshot(source)
            if not isinstance(issues, list):
                raise ValueError("Draft diagnostics must be an array")
            fingerprint = digest({"source": source, "policy": policy_json, "diagnostics": issues})
            connection.execute("""UPDATE policy_drafts SET revision=revision+1,source_json=?,policy_json=?,
                diagnostics_json=?,draft_hash=?,edited_by=?,updated_at=?
                WHERE org_id=? AND job_id=? AND draft_id=? AND revision=?""",
                (json_dump(source), json_dump(policy_json), json_dump(issues), fingerprint,
                 context.user_id, utcnow(), context.org_id, job_id, draft_id, expected_revision))
            self.audit(connection, context, "POLICY_DRAFT_EDITED", draft_id, {"hash": fingerprint})
            return self.one(connection, "SELECT * FROM policy_drafts WHERE org_id=? AND job_id=? AND draft_id=?",
                            (context.org_id, job_id, draft_id))

    def get_policy_draft(self, context, job_id, draft_id):
        with self.read(context, job_id) as connection:
            return self.one(connection, "SELECT * FROM policy_drafts WHERE org_id=? AND job_id=? AND draft_id=?",
                            (context.org_id, job_id, draft_id))

    def create_policy_version(self, context, job_id, policy_json, *, execution_profile_id,
                              policy_id=None, original_jd=None, hr_requirements=None,
                              policy_schema_version="storage_snapshot_v1", source_draft_id=None,
                              expected_draft_revision=None, compilation_run_id=None, confirmation_id=None):
        check_snapshot(policy_json)
        generic = policy_json.get("schema_version") == "job_scoring_policy_v1"
        if policy_schema_version not in ("storage_snapshot_v1", "legacy_ai_v2", "job_scoring_policy_v1"):
            raise ValueError("Unsupported policy storage protocol")
        if generic != (policy_schema_version == "job_scoring_policy_v1"):
            raise ValueError("Generic policies require the matching policy storage protocol")
        if generic and source_draft_id is None:
            raise ValueError("Generic policy versions require a validated source draft")
        if (compilation_run_id is None) != (confirmation_id is None):
            raise ValueError("Compilation run and confirmation must be supplied together")
        if not generic and compilation_run_id is not None:
            raise ValueError("Compilation attestation applies only to generic policies")
        policy_id = policy_id or uid()
        job_target = policy_json.get("job_target", {})
        if not isinstance(job_target, dict):
            raise ValueError("job_target must be an object when present")
        with self.tx(context, job_id, "edit") as connection:
            job = self.one(connection, "SELECT * FROM jobs WHERE org_id=? AND job_id=?", (context.org_id, job_id))
            self.one(connection, "SELECT profile_id FROM execution_profiles WHERE org_id=? AND profile_id=?",
                     (context.org_id, execution_profile_id))
            source_draft_hash = None
            source_snapshot = {}
            if source_draft_id is not None:
                draft = self.one(connection, "SELECT * FROM policy_drafts WHERE org_id=? AND job_id=? AND draft_id=?",
                                 (context.org_id, job_id, source_draft_id))
                if expected_draft_revision != draft["revision"] or draft["policy_json"] != json_dump(policy_json):
                    raise ValueError("Policy must match the explicitly selected draft revision")
                if draft["diagnostics_json"] != "[]":
                    raise ValueError("Resolve draft diagnostics before creating a publishable version")
                source_draft_hash = draft["draft_hash"]
                source_snapshot = json.loads(draft["source_json"])
            elif expected_draft_revision is not None:
                raise ValueError("Draft revision requires source_draft_id")
            if generic:
                from config.policy_validation import validate_publishable_policy
                validate_publishable_policy(policy_json, source_snapshot)
            if original_jd is None:
                original_jd = source_snapshot.get("jd_text" if generic else "original_jd", job_target.get("raw_jd_text", ""))
            if hr_requirements is None:
                hr_requirements = source_snapshot.get("hr_requirements", job_target.get("hr_custom_requirements", ""))
            if not isinstance(original_jd, str) or not isinstance(hr_requirements, str):
                raise ValueError("JD and HR requirements must be strings")
            connection.execute("""INSERT INTO job_policy_versions
                (org_id,job_id,policy_id,policy_version,policy_json,policy_hash,original_jd,hr_requirements,
                 execution_profile_id,policy_schema_version,source_draft_id,draft_revision,source_draft_hash,source_snapshot_json,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (context.org_id, job_id, policy_id, job["next_policy_version"], json_dump(policy_json),
                 digest(policy_json), original_jd, hr_requirements, execution_profile_id, policy_schema_version,
                 source_draft_id, expected_draft_revision, source_draft_hash, json_dump(source_snapshot), context.user_id))
            if compilation_run_id is not None:
                self._attest_policy_version(connection, context, job_id, policy_id, compilation_run_id, confirmation_id)
            self.audit(connection, context, "POLICY_VERSION_CREATED", policy_id,
                       {"version": job["next_policy_version"], "hash": digest(policy_json)})
            return self.one(connection, "SELECT * FROM job_policy_versions WHERE org_id=? AND job_id=? AND policy_id=?",
                            (context.org_id, job_id, policy_id))

    def publish_policy(self, context, job_id, policy_id, *, confirmation_hash):
        with self.tx(context, job_id, "publish") as connection:
            policy = self.one(connection, "SELECT * FROM job_policy_versions WHERE org_id=? AND job_id=? AND policy_id=?",
                              (context.org_id, job_id, policy_id))
            required_hash = policy["source_draft_hash"] if policy["source_draft_id"] is not None else policy["policy_hash"]
            if confirmation_hash != required_hash:
                raise ValueError("Explicit confirmation of the complete source draft or manual snapshot hash is required")
            if policy["source_draft_id"] is not None:
                draft = self.one(connection, "SELECT * FROM policy_drafts WHERE org_id=? AND job_id=? AND draft_id=?",
                                 (context.org_id, job_id, policy["source_draft_id"]))
                if (draft["revision"] != policy["draft_revision"] or draft["draft_hash"] != policy["source_draft_hash"]
                        or draft["policy_json"] != policy["policy_json"] or draft["diagnostics_json"] != "[]"):
                    raise ValueError("Draft changed or has unresolved diagnostics; confirmation is stale")
            connection.execute("""INSERT INTO policy_publications
                (org_id,job_id,policy_id,policy_version,execution_profile_id,confirmation_hash,published_by)
                VALUES(?,?,?,?,?,?,?)""", (context.org_id, job_id, policy_id, policy["policy_version"],
                    policy["execution_profile_id"], confirmation_hash, context.user_id))
            self.audit(connection, context, "POLICY_PUBLISHED", policy_id,
                       {"version": policy["policy_version"], "hash": confirmation_hash})
            return self.one(connection, "SELECT * FROM policy_publications WHERE org_id=? AND job_id=? AND policy_id=?",
                            (context.org_id, job_id, policy_id))

    def get_current_policy(self, context, job_id):
        with self.read(context, job_id) as connection:
            return self.one(connection, """SELECT p.* FROM jobs j JOIN job_policy_versions p
                ON p.org_id=j.org_id AND p.job_id=j.job_id AND p.policy_id=j.current_policy_id
                WHERE j.org_id=? AND j.job_id=?""", (context.org_id, job_id))

    def get_policy_version(self, context, job_id, policy_id=None, *, policy_version=None):
        if (policy_id is None) == (policy_version is None):
            raise ValueError("Select exactly one policy_id or policy_version")
        with self.read(context, job_id) as connection:
            field, value = ("policy_id", policy_id) if policy_id is not None else ("policy_version", policy_version)
            return self.one(connection, f"SELECT * FROM job_policy_versions WHERE org_id=? AND job_id=? AND {field}=?",
                            (context.org_id, job_id, value))
