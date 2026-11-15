"""Qwen transport and deterministic validation for generic job policy drafts."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from time import perf_counter

from openai import OpenAI
from pydantic import ValidationError

from config.policy_validation import VALIDATOR_VERSION, diagnose_policy
from config.settings import load_runtime_settings
from schemas.job_policy import JobScoringPolicy, PolicyCompileInput, PolicyProvenance


COMPILER_VERSION = "job-policy-compiler-v1.1"
PROMPT_VERSION = "job-policy-prompt-v1.4"
PROMPT_PATH = Path(__file__).parent / "prompts" / "job_policy_v1.txt"


class CompilationError(RuntimeError):
    def __init__(self, metrics, response_content=None):
        self.metrics = metrics
        self.response_content = response_content
        super().__init__("Policy compilation failed at " + metrics.get("stage", "unknown") +
                         ": " + metrics.get("failure_type", "unknown"))


@dataclass(frozen=True)
class CompilationResult:
    policy: JobScoringPolicy
    diagnostics: list
    metrics: dict

    @property
    def publishable(self):
        """Structurally valid: only ERROR diagnostics block confirmation.

        CONFIRM diagnostics require explicit HR acknowledgement and are handled
        by the policy service; quality (Stage 5.5) gates publication separately.
        """
        return not any(item.severity == "ERROR" for item in self.diagnostics)


class JobPolicyCompiler:
    """One request, no automatic retry/repair, no database or publishing authority."""
    def __init__(self, *, client=None, model_name=None, timeout_seconds=90, max_tokens=12288):
        if not 1 <= timeout_seconds <= 180 or not 512 <= max_tokens <= 32768:
            raise ValueError("Compiler request limits are out of bounds")
        settings = load_runtime_settings()
        self.client = client if client is not None else OpenAI(
            base_url=settings.llm_base_url, api_key=settings.llm_api_key,
            timeout=timeout_seconds, max_retries=0)
        self.model_name = model_name or settings.llm_model_name
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.last_metrics = None
        self.last_response_content = None

    def provenance(self, response_model=None):
        return {"compiler_version": COMPILER_VERSION, "prompt_version": PROMPT_VERSION,
                "validator_version": VALIDATOR_VERSION, "model_version": response_model or self.model_name}

    def build_request(self, upload):
        upload = PolicyCompileInput.model_validate(upload)
        return {"model": self.model_name, "temperature": 0, "timeout": self.timeout_seconds,
                "max_tokens": self.max_tokens, "stream": False,
                "extra_body": {"enable_thinking": False},
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "job_scoring_policy", "strict": True,
                    "schema": JobScoringPolicy.model_json_schema()}},
                "messages": [{"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
                             {"role": "user", "content": json.dumps({
                                 "untrusted_job_input": upload.model_dump(mode="json"),
                                 "expected_provenance": self.provenance()}, ensure_ascii=False)}]}

    def compile(self, upload):
        upload = PolicyCompileInput.model_validate(upload)
        request = self.build_request(upload)
        encoded = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        started = perf_counter()
        metrics = {"status": "RUNNING", "stage": "api_call", "model": self.model_name,
                   "timeout_seconds": self.timeout_seconds, "max_tokens": self.max_tokens,
                   "temperature": 0, "strict_schema": True, "enable_thinking": False,
                   "sdk_max_retries": getattr(self.client, "max_retries", None),
                   "system_chars": len(request["messages"][0]["content"]),
                   "user_chars": len(request["messages"][1]["content"]),
                   "schema_chars": len(encoded(request["response_format"]["json_schema"]["schema"])),
                   "prompt_hash": hashlib.sha256(encoded(request["messages"]).encode("utf-8")).hexdigest(),
                   "schema_hash": hashlib.sha256(encoded(request["response_format"]).encode("utf-8")).hexdigest(),
                   "usage": None, "reasoning_tokens": None, "pydantic_valid": False,
                   "business_valid": False, "finish_reason": None}
        self.last_metrics, self.last_response_content = metrics, None
        try:
            response = self.client.chat.completions.create(**request)
            metrics["api_elapsed_seconds"] = round(perf_counter() - started, 4)
            metrics["request_id"] = getattr(response, "_request_id", None)
            metrics["response_model"] = getattr(response, "model", None)
            usage = getattr(response, "usage", None)
            if usage is not None:
                metrics["usage"] = usage.model_dump(mode="json") if hasattr(usage, "model_dump") else vars(usage)
                metrics["reasoning_tokens"] = (metrics["usage"].get("completion_tokens_details") or {}).get("reasoning_tokens")
            metrics["stage"] = "response_validation"
            choice = response.choices[0]
            metrics["finish_reason"] = choice.finish_reason
            self.last_response_content = choice.message.content
            metrics["response_chars"] = len(choice.message.content or "")
            metrics["raw_response_sha256"] = hashlib.sha256((choice.message.content or "").encode("utf-8")).hexdigest()
            if choice.finish_reason != "stop" or getattr(choice.message, "refusal", None):
                raise ValueError("Response was truncated, refused or did not finish normally")
            metrics["stage"] = "pydantic_validation"
            policy = JobScoringPolicy.model_validate_json(choice.message.content or "")
            metrics["pydantic_valid"] = True
            # Model-supplied sources remain unchanged and are compared with upload.
            # Compiler metadata is server-owned, not an assertion made by the model.
            policy = policy.model_copy(update={"provenance": PolicyProvenance(**self.provenance(metrics["response_model"]))})
            policy = JobScoringPolicy.model_validate(policy.model_dump(mode="json"))
            metrics["stage"] = "policy_normalization"
            from config.policy_normalization import NORMALIZER_VERSION, normalize_policy
            raw_diagnostics = [item.model_dump(mode="json") for item in policy.diagnostics]
            before = policy.model_dump(mode="json")
            policy = normalize_policy(policy)
            metrics["normalizer_version"] = NORMALIZER_VERSION
            metrics["policy_normalized"] = before != policy.model_dump(mode="json")
            metrics["normalization_actions"] = [item.model_dump(mode="json") for item in policy.diagnostics
                                                if item.model_dump(mode="json") not in raw_diagnostics]
            metrics["compiled_policy_sha256"] = hashlib.sha256(encoded(policy.model_dump(mode="json")).encode("utf-8")).hexdigest()
            metrics["stage"] = "business_validation"
            diagnostics = diagnose_policy(policy, upload)
            metrics["business_valid"] = not any(item.severity == "ERROR" for item in diagnostics)
            metrics["needs_confirmation"] = any(item.severity == "CONFIRM" for item in diagnostics)
            metrics["diagnostic_codes"] = [item.code for item in diagnostics]
            metrics.update(status="DRAFT_READY", stage="completed")
            return CompilationResult(policy, diagnostics, metrics)
        except Exception as exc:
            metrics.update(status="FAILED", failure_type=type(exc).__name__, http_status=getattr(exc, "status_code", None))
            metrics.setdefault("api_elapsed_seconds", round(perf_counter() - started, 4))
            cause, chain = exc, []
            while cause is not None and len(chain) < 6:
                chain.append(type(cause).__name__)
                cause = cause.__cause__
            metrics["cause_chain"] = chain
            if isinstance(exc, ValidationError):
                metrics["validation_errors"] = [{"path": ".".join(map(str, item["loc"])), "type": item["type"]}
                                                for item in exc.errors(include_input=False)]
            raise CompilationError(metrics, self.last_response_content) from exc
        finally:
            metrics["elapsed_seconds"] = round(perf_counter() - started, 4)
