"""Repository-bound Tier 0/1 initial screening, without model calls or writes."""
import json
import re

from pydantic import ValidationError

from engine.policy_interpreter import INTERPRETER_VERSION, PolicyInterpreter, content_hash
from schemas import ResumeData
from schemas.prescreen import ObjectiveFact, ObjectiveFacts, PreliminaryEvaluation
from storage.repositories.base import NotFound


def resolve_source(data, path):
    """Resolve only identifier/list-index paths; never eval a supplied path."""
    value = data
    for part in path.split("."):
        match = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)(?:\[(\d+)\])?", part)
        if match is None or not isinstance(value, dict) or match[1] not in value:
            raise ValueError("Evidence path is not present in the original snapshot")
        value = value[match[1]]
        if match[2] is not None:
            if not isinstance(value, list) or int(match[2]) >= len(value):
                raise ValueError("Evidence list index is outside the original snapshot")
            value = value[int(match[2])]
    return value


def objective_facts(raw, resume):
    normalized = resume.model_dump(mode="json", by_alias=True)
    facts = {}
    for field in ("work_authorization", "current_city"):
        value = raw.get(field)
        if isinstance(value, str) and value.strip() and normalized.get(field) == value:
            facts[field] = ObjectiveFact(value=value, source_path=field, quote=value)
    return ObjectiveFacts(**facts)


def validate_evidence(evidence, raw):
    for item in evidence:
        value = resolve_source(raw, item.source_path)
        if not isinstance(value, str) or value[item.start:item.end] != item.quote:
            raise ValueError("Evidence quote/offset is not an exact substring of its original field")


class PreliminaryScreeningEngine:
    def __init__(self, repository, *, profile=None, profile_path=None, interpreter=None):
        if interpreter is not None and (profile is not None or profile_path is not None):
            raise ValueError("An injected interpreter owns its execution profile")
        if interpreter is not None and interpreter.repository is not repository:
            raise ValueError("An injected interpreter must use this service's repository")
        self.repository = repository
        self.interpreter = interpreter or PolicyInterpreter(repository, profile=profile, profile_path=profile_path)

    def evaluate(self, context, job_id, job_candidate_id, *, resume_id=None):
        # Auth failure is an access error, never a candidate score or identity leak.
        with self.repository.read(context, job_id, "evaluate"):
            pass
        candidate = self.repository.get_job_candidate(context, job_id, job_candidate_id)
        bound = self.interpreter.load_for_candidate(context, job_id, job_candidate_id)
        plan = bound.plan
        if ((plan.org_id, plan.job_id, bound.job_candidate_id, bound.candidate_id,
             plan.policy_id, plan.policy_version) !=
                (context.org_id, job_id, job_candidate_id, candidate["candidate_id"],
                 candidate["policy_id"], candidate["policy_version"])):
            raise ValueError("Interpreter binding does not match the authorized candidate association")
        try:
            sealed = self.repository.get_sealed_input(context, job_id, job_candidate_id)
        except NotFound:
            sealed = None
        if sealed is not None:
            if resume_id is not None and resume_id != sealed["resume_id"]:
                raise ValueError("A sealed scoring input cannot be replaced by another acquisition")
            selected_id, input_kind = sealed["resume_id"], "SEALED"
        elif resume_id is None:
            raise ValueError("An unsealed preliminary preview requires an explicit acquired resume_id")
        else:
            selected_id, input_kind = resume_id, "PARTIAL_PREVIEW"
        snapshot = self.repository.get_resume_snapshot(context, job_id, job_candidate_id, selected_id)
        if (snapshot["org_id"] != context.org_id or snapshot["candidate_id"] != candidate["candidate_id"]
                or snapshot["resume_id"] != selected_id):
            raise ValueError("Resume snapshot identity does not match the authorized association")
        policy, profile = plan.policy, plan.profile
        identity = dict(org_id=context.org_id, candidate_id=bound.candidate_id, job_id=job_id,
            job_candidate_id=job_candidate_id, policy_id=plan.policy_id, policy_version=plan.policy_version,
            policy_hash=plan.policy_hash, resume_id=selected_id, resume_content_hash=snapshot["content_hash"],
            input_kind=input_kind, data_completeness=snapshot["completeness"], interpreter_version=INTERPRETER_VERSION,
            input_has_issues=bool(json.loads(snapshot["data_issues_json"]) or json.loads(snapshot["collection_errors_json"])),
            execution_profile_version=profile.profile_version, execution_profile_hash=plan.profile_hash,
            thresholds=policy.thresholds)
        try:
            raw = json.loads(snapshot["raw_resume_json"])
            resume = ResumeData.model_validate_json(snapshot["normalized_resume_json"])
            if (content_hash(raw) != snapshot["raw_hash"]
                    or content_hash(resume.model_dump(mode="json", by_alias=True, exclude={"meta"})) != snapshot["content_hash"]
                    or resume.data_completeness != snapshot["completeness"]):
                raise ValueError("Stored input hash/completeness is inconsistent with its snapshot")
            if not resume.ok or resume.data_completeness == "COLLECTION_FAILED":
                return PreliminaryEvaluation(**identity, hard_filter_status="UNKNOWN", scoring_status="SCORING_FAILED",
                    review_reasons=["COLLECTION_FAILED:重新采集后人工核验；不生成初筛分"])
            from engine.policy_hard_filter import evaluate_hard_filters
            from engine.policy_rule_matcher import evaluate_policy_rules
            hard = evaluate_hard_filters(plan, resume, facts=objective_facts(raw, resume))
            rules = evaluate_policy_rules(plan, resume, hard_result=hard)
            all_evidence = [*hard.evidence, *rules.keyword_evidence]
            validate_evidence(all_evidence, raw)
            for dimension in rules.dimension_scores:
                validate_evidence(dimension.evidence, raw)
            expected_ids = [item.dimension_id for item in policy.scoring_dimensions]
            if [item.dimension_id for item in rules.dimension_scores] != expected_ids:
                raise ValueError("Tier 1 dimensions differ from the bound policy")
            if any((actual.dimension_label != expected.label or actual.dimension_weight != expected.weight)
                   for actual, expected in zip(rules.dimension_scores, policy.scoring_dimensions)):
                raise ValueError("Tier 1 labels/weights must come from the bound policy")
            coarse = rules.preliminary_score >= policy.thresholds.coarse_pass
            review = list(dict.fromkeys([*hard.review_reasons, *rules.review_reasons,
                "初筛仅提供排序与核验信号，最终招聘判断由HR作出；Tier2/Tier3尚未执行"]))
            incomplete = (resume.data_completeness != "COMPLETE" or bool(resume.collection_errors or resume.data_issues))
            if hard.hard_filter_status == "FAIL":
                status = "HARD_FILTERED"
            elif incomplete:
                status = "DATA_INCOMPLETE"
                review.append("DATA_INCOMPLETE:数据/映射问题尚未解决，只供初筛预览")
            elif hard.hard_filter_status == "UNKNOWN":
                status = "HARD_UNKNOWN"
            elif not coarse:
                status = "BELOW_COARSE"
                review.append("RULE_SCORE_BELOW_COARSE:规则初筛分低于本岗位门槛，不代表最终淘汰")
            else:
                status = "READY_FOR_DEEP"
                review.append("待后续深评；deep_review/deep_recommend尚未应用，不授权发送")
            return PreliminaryEvaluation(**identity, hard_filter_status=hard.hard_filter_status,
                preliminary_score=rules.preliminary_score, dimension_scores=rules.dimension_scores,
                matched_requirements=list(dict.fromkeys([*hard.matched_requirements, *rules.matched_requirements])),
                failed_requirements=hard.failed_requirements,
                unknown_requirements=list(dict.fromkeys([*hard.unknown_requirements, *rules.unknown_requirements])),
                keyword_evidence=rules.keyword_evidence, evidence=all_evidence, review_reasons=review,
                scoring_status=status, coarse_passed=coarse, tier0=hard, tier1=rules,
                diagnostics=[*plan.diagnostics, *(f"INPUT_ISSUE:{issue.path}:{issue.kind}" for issue in resume.data_issues)])
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            return PreliminaryEvaluation(**identity, hard_filter_status="UNKNOWN", scoring_status="SCORING_FAILED",
                review_reasons=[f"PRESCREEN_FAILED:{type(exc).__name__}:输入/规则执行或证据校验失败，需核验"],
                diagnostics=[type(exc).__name__])
