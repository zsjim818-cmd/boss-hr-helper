"""Compiler application service; all persistence goes through Repositories."""
import json

from config.policy_compiler import CompilationError
from config.policy_validation import VALIDATOR_VERSION, diagnose_policy, validate_publishable_policy
from schemas.job_policy import JobScoringPolicy, PolicyCompileInput, PolicyProvenance


class PolicyCompilerService:
    def __init__(self, repository, compiler=None):
        self.repository = repository
        self.compiler = compiler

    def compile_draft(self, context, job_id, upload):
        if self.compiler is None:
            raise ValueError("A compiler must be explicitly supplied")
        upload = PolicyCompileInput.model_validate(upload)
        # Authorization is checked before an expensive external call and again
        # when writing. No SQLite write transaction waits on the model.
        with self.repository.read(context, job_id, "edit"):
            pass
        try:
            result = self.compiler.compile(upload)
        except CompilationError as exc:
            self.repository.record_policy_compilation(context, job_id, upload.model_dump(mode="json"),
                status="FAILED", provenance=self.compiler.provenance(), metrics=exc.metrics,
                error_type=exc.metrics.get("failure_type", "CompilationError"))
            raise
        diagnostics = [item.model_dump(mode="json") for item in result.diagnostics]
        blocking = [item for item in diagnostics if item["severity"] in ("ERROR", "CONFIRM")]
        draft = self.repository.create_policy_draft(context, job_id, result.policy.model_dump(mode="json"),
            source_json=upload.model_dump(mode="json"), diagnostics=blocking)
        run = self.repository.record_policy_compilation(context, job_id, upload.model_dump(mode="json"),
            status="SUCCEEDED" if result.publishable else "FAILED", provenance=result.policy.provenance.model_dump(mode="json"),
            metrics=result.metrics, policy_json=result.policy.model_dump(mode="json"),
            draft_id=draft["draft_id"], draft_revision=draft["revision"], diagnostics=diagnostics,
            error_type=None if result.publishable else "PolicyDiagnosticsBlocked")
        return {"draft": draft, "compilation_run": run, "diagnostics": diagnostics,
                "publishable": result.publishable, "metrics": result.metrics}

    def update_draft(self, context, job_id, draft_id, policy_json, *, expected_revision,
                     source_input=None, parent_run_id=None):
        if parent_run_id is None:
            raise ValueError("A human-edited draft must retain its parent compilation run")
        parent = self.repository.get_policy_compilation(context, job_id, parent_run_id)
        if parent["draft_id"] != draft_id:
            raise ValueError("A validation run must retain the same draft's compilation lineage")
        draft = self.repository.get_policy_draft(context, job_id, draft_id)
        source = json.loads(draft["source_json"]) if source_input is None else PolicyCompileInput.model_validate(source_input).model_dump(mode="json")
        policy = JobScoringPolicy.model_validate(policy_json)
        from config.policy_normalization import normalize_policy, NORMALIZER_VERSION
        policy = policy.model_copy(update={"provenance": PolicyProvenance(
            compiler_version=parent["compiler_version"], prompt_version=parent["prompt_version"],
            validator_version=VALIDATOR_VERSION, model_version=parent["model_version"])})
        policy = normalize_policy(policy)
        diagnostics = [item.model_dump(mode="json") for item in diagnose_policy(policy, source)]
        blocking = [item for item in diagnostics if item["severity"] in ("ERROR", "CONFIRM")]
        updated = self.repository.update_policy_draft(context, job_id, draft_id, policy.model_dump(mode="json"),
            expected_revision=expected_revision, source_json=source, diagnostics=blocking)
        run = self.repository.record_policy_compilation(context, job_id, source,
            status="FAILED" if blocking else "SUCCEEDED", provenance=policy.provenance.model_dump(mode="json"),
            policy_json=policy.model_dump(mode="json"), draft_id=draft_id, draft_revision=updated["revision"],
            diagnostics=diagnostics, error_type="PolicyDiagnosticsBlocked" if blocking else None,
            run_kind="VALIDATION", parent_run_id=parent_run_id, metrics={"normalizer_version": NORMALIZER_VERSION})
        return {"draft": updated, "compilation_run": run, "diagnostics": diagnostics, "publishable": not blocking}

    def confirm_draft(self, context, job_id, draft_id, run_id, *, confirmation_hash):
        return self.repository.confirm_policy_draft(context, job_id, draft_id, run_id,
                                                     confirmation_hash=confirmation_hash)

    def create_version(self, context, job_id, draft_id, run_id, confirmation_id, *, execution_profile_id):
        draft = self.repository.get_policy_draft(context, job_id, draft_id)
        policy = json.loads(draft["policy_json"])
        validate_publishable_policy(policy, json.loads(draft["source_json"]))
        # Allocation, source freezing and attestation share one transaction;
        # stale confirmations cannot leave an allocated partial version.
        version = self.repository.create_policy_version(context, job_id, policy,
            execution_profile_id=execution_profile_id, policy_schema_version="job_scoring_policy_v1",
            source_draft_id=draft_id, expected_draft_revision=draft["revision"],
            compilation_run_id=run_id, confirmation_id=confirmation_id)
        return {"version": version}

    def publish_version(self, context, job_id, policy_id, *, confirmation_hash):
        return self.repository.publish_policy(context, job_id, policy_id, confirmation_hash=confirmation_hash)
