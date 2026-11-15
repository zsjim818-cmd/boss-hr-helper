import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Tuple, Union, Optional
from openai import OpenAI
from config.settings import DEFAULT_CONFIG_PATH, load_runtime_settings

from engine.rule_matcher import RuleMatcherMixin
from engine.semantic_ranker import SemanticRankerMixin
from engine.llm_reviewer import LLMReviewerMixin
from engine.scoring_session import ScoringSessionMixin
from storage.scoring_cache import stable_hash
from config.validation import validate_config

from engine.hard_filter import parse_salary, check_hard_filters

from schemas import (
    ResumeData, LLMStructuredEvaluation, EvaluationOutput,
    DebugTrace, ChunkSimDetail
)

logger = logging.getLogger("ScoringEngine")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

class HybridScoringEngine(ScoringSessionMixin, RuleMatcherMixin, SemanticRankerMixin, LLMReviewerMixin):
    def __init__(
        self,
        config_path: Union[str, Path] = DEFAULT_CONFIG_PATH,
        embedding_model_path: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_model_name: Optional[str] = None,
        *, embedder=None, llm_client=None, cache=None
    ):
        self.config_path = Path(config_path).resolve()
        self._config = self._load_config()
        injected = embedder is not None or llm_client is not None

        # 优先使用传入参数，未传则读取 .env 环境变量
        settings = load_runtime_settings()
        emb_path = embedding_model_path or settings.embedding_model_path
        base_url = llm_base_url or settings.llm_base_url
        api_key = llm_api_key or settings.llm_api_key
        model_name = llm_model_name or settings.llm_model_name

        logger.info("初始化语义模型%s", " (injected)" if embedder is not None else " (local)")
        if embedder is None:
            from sentence_transformers import SentenceTransformer
            embedder = SentenceTransformer(emb_path)
        self.embedder = embedder
        self._refresh_anchor_embeddings()

        logger.info("初始化 LLM 客户端%s", " (injected)" if llm_client is not None else "")
        self.llm_client = llm_client if llm_client is not None else OpenAI(base_url=base_url, api_key=api_key, max_retries=0)
        self.llm_model_name = model_name
        self._embedding_identity = emb_path
        self._provider_identity = stable_hash(base_url)
        self._init_session(cache, injected)

    def _load_config(self) -> Dict[str, Any]:
        with open(self.config_path, "r", encoding="utf-8") as f:
            return validate_config(json.load(f))

    def reload_config(self) -> None:
        with self._session_lock:
            if self._batch_depth:
                raise RuntimeError("Cannot reload config inside an active scoring batch")
            new_config = self._load_config()
            old_config = self._config
            old_anchors = {k: v for k, v in self.__dict__.items() if k.startswith(("_pos", "_neg", "_route"))}
            self._config = new_config
            try:
                self._refresh_anchor_embeddings()
            except Exception:
                self._config = old_config
                self.__dict__.update(old_anchors)
                raise

    @staticmethod
    def _parse_salary_lower_bound_k(salary_str: str) -> Optional[float]:
        parsed = parse_salary(salary_str)
        return parsed.lower_k if parsed.period == "month" else None

    def _run_tier0_hard_filter(self, resume: ResumeData) -> Tuple[bool, str]:
        state, checks = check_hard_filters(resume, self.config)
        return state != "FAIL", "; ".join(f"{k}:{v[0]}({v[1]})" for k, v in checks.items())

    # ==================== Tier 1: 结构化位置加权规则匹配 ====================
    def _decide_routing(self, score, *, mode="fast_mode", status="COARSE_ONLY", complete=False, hard_status="UNKNOWN"):
        thresholds = self.config["scoring_config"]["thresholds"]
        if (mode != "deep_mode" or status != "DEEP_SCORED" or not complete
                or hard_status != "PASS" or score is None):
            return "PENDING_REVIEW"
        if score < float(thresholds.get("review_threshold", 60)):
            return "NOT_RECOMMENDED"
        if score >= float(thresholds.get("auto_greet_threshold", 82)) and self.config["quotas_and_switches"].get("enable_auto_greet", False):
            return "AUTO_GREET"
        return "PENDING_REVIEW"

    def _deep_routing(self, score, evaluation, resume, hard_status):
        route = self._decide_routing(score, mode="deep_mode", status="DEEP_SCORED",
                                     complete=resume.data_completeness == "COMPLETE" and not resume.data_issues and not resume.collection_errors,
                                     hard_status=hard_status)
        minimum = self.config["scoring_config"]["llm_review"]["auto_greet_min_confidence"]
        if route == "AUTO_GREET" and any(getattr(evaluation, key).confidence < minimum or not getattr(evaluation, key).evidence
                                         for key in ("ai_automation", "engineering", "efficiency", "job_fit")):
            return "PENDING_REVIEW"
        return route

    def _evaluate_resume(self, resume, force_mode=None) -> EvaluationOutput:
        mode = force_mode or self.config["quotas_and_switches"].get("scoring_mode", "deep_mode")
        if mode not in ("fast_mode", "deep_mode"):
            raise ValueError(f"Unsupported scoring mode: {mode}")
        trace = DebugTrace()
        out = EvaluationOutput(candidate_key=resume.meta.candidate_key, name=resume.name,
                               scoring_mode=mode, routing_decision="PENDING_REVIEW", debug_trace=trace,
                               data_completeness=resume.data_completeness)
        if not resume.ok or resume.data_completeness == "COLLECTION_FAILED":
            out.scoring_status = "SCORING_FAILED"
            out.risk_point = trace.route_reason = "采集失败，不能评分，需重新采集"
            return out
        state, checks = check_hard_filters(resume, self.config)
        trace.hard_filter_status = state
        trace.hard_filter_checks = {k: list(v) for k, v in checks.items()}
        passed, reason = self._run_tier0_hard_filter(resume)
        trace.hard_filter_passed, trace.hard_filter_reason = passed, reason
        out.passed_hard_filter = passed
        if not passed:
            out.scoring_status = "HARD_FILTERED"
            out.routing_decision = "NOT_RECOMMENDED"
            out.risk_point = trace.route_reason = reason + "；仍保留HR人工判断"
            return out
        try:
            raw, capped, tags, hits = self._run_tier1_rules(resume)
            trace.tier1_raw_score, trace.tier1_capped_score, trace.tier1_hit_details = raw, capped, hits
            out.matched_tags = tags
            sem, penalty, metrics, chunks = self._run_tier2_semantic(resume)
            trace.tier2_semantic_score, trace.tier2_penalty_applied, trace.tier2_chunk_details = sem, penalty, chunks
            out.semantic_similarity = metrics
            out.coarse_score = round(min(100.0, capped + sem), 2)
            threshold = float(self.config["scoring_config"]["thresholds"].get("coarse_pass_score", 52))
            out.passed_tier2_coarse = out.coarse_score >= threshold
            if resume.data_completeness != "COMPLETE" or resume.collection_errors or resume.data_issues:
                out.scoring_status = "DATA_INCOMPLETE"
                out.risk_point = trace.route_reason = "简历不完整，仅供人工核验"
                return out
            if mode == "fast_mode" or not out.passed_tier2_coarse:
                out.scoring_status = "COARSE_ONLY"
                out.routing_decision = "PENDING_REVIEW" if out.passed_tier2_coarse else "NOT_RECOMMENDED"
                out.risk_point = trace.route_reason = "仅粗排，不生成正式深度分；低分仅不推荐，交HR判断"
                return out
            llm_eval = self._run_tier3_llm(resume, tags)
            out.total_score, out.dimension_scores = self._compute_weighted_dimensions(llm_eval)
            trace.tier3_llm_raw_scores = {k: getattr(llm_eval, k).score for k in ("ai_automation", "engineering", "efficiency", "job_fit")}
            trace.tier3_dimension_evidence = {k: getattr(llm_eval, k) for k in trace.tier3_llm_raw_scores}
            out.scoring_status = "DEEP_SCORED"
            out.highlights, out.risk_point = llm_eval.highlights, llm_eval.risk_point
            out.routing_decision = self._deep_routing(out.total_score, llm_eval, resume, state)
            trace.route_reason = f"{mode}/{out.scoring_status}; hard={state}; route={out.routing_decision}；低分保留HR判断"
        except Exception as exc:
            logger.warning("评分失败: %s", type(exc).__name__)
            out.total_score = None
            out.dimension_scores = {}
            out.scoring_status = "SCORING_FAILED"
            out.routing_decision = "PENDING_REVIEW"
            out.risk_point = trace.route_reason = f"评分失败({type(exc).__name__})；粗排不作为最终分；需人工核验"
        return out
