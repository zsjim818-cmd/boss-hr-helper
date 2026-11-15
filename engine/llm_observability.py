"""Safe Tier 3 metrics; never log credentials or model reasoning text."""
import hashlib
import json
import logging
from time import perf_counter

from pydantic import ValidationError
from schemas import LLMStructuredEvaluation


class LLMOutputTruncated(ValueError):
    """The provider exhausted its generation limit; the review is incomplete."""


def request_metadata(request):
    def encoded(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    messages = request["messages"]
    schema = request["response_format"]
    wire_body = {k: v for k, v in request.items() if k not in ("timeout", "extra_body")}
    wire_body.update(request.get("extra_body", {}))
    return {
        "model": request["model"], "timeout_seconds": request["timeout"],
        "temperature": request["temperature"], "max_tokens": request.get("max_tokens"),
        "max_completion_tokens": request.get("max_completion_tokens"),
        "extra_body": request.get("extra_body", {}), "stream": request.get("stream", False),
        "response_format": schema["type"], "strict": schema["json_schema"]["strict"],
        "system_chars": len(messages[0]["content"]), "user_chars": len(messages[1]["content"]),
        "schema_chars": len(encoded(schema["json_schema"]["schema"])),
        "body_utf8_bytes": len(encoded(wire_body).encode("utf-8")),
        "prompt_sha256": hashlib.sha256(encoded(messages).encode("utf-8")).hexdigest(),
        "schema_sha256": hashlib.sha256(encoded(schema).encode("utf-8")).hexdigest(),
    }


def error_chain(exc):
    chain = []
    while exc is not None and len(chain) < 6:
        chain.append(type(exc).__name__)
        exc = exc.__cause__
    return chain


def execute_review(reviewer, resume, request):
    started = perf_counter()
    metrics = {"request": request_metadata(request), "sdk_max_retries": getattr(reviewer.llm_client, "max_retries", None),
               "status": "RUNNING", "stage": "api_call", "usage": None, "reasoning_tokens": None,
               "finish_reason": None, "pydantic_valid": False, "evidence_valid": False}
    reviewer.last_llm_diagnostics = metrics
    reviewer.last_llm_response_content = None
    api_started = perf_counter()
    try:
        response = reviewer.llm_client.chat.completions.create(**request)
        metrics["api_elapsed_seconds"] = round(perf_counter() - api_started, 4)
        metrics["request_id"] = getattr(response, "_request_id", None)
        metrics["response_model"] = getattr(response, "model", None)
        usage = getattr(response, "usage", None)
        if usage is not None:
            metrics["usage"] = usage.model_dump(mode="json") if hasattr(usage, "model_dump") else dict(vars(usage))
            details = metrics["usage"].get("completion_tokens_details") or {}
            metrics["reasoning_tokens"] = details.get("reasoning_tokens")
        choice = response.choices[0]
        metrics["finish_reason"] = getattr(choice, "finish_reason", None)
        metrics["stage"] = "response_validation"
        if metrics["finish_reason"] == "length":
            raise LLMOutputTruncated("Provider generation limit reached")
        if metrics["finish_reason"] not in (None, "stop"):
            raise ValueError("Provider did not complete a normal answer")
        message = choice.message
        reviewer.last_llm_response_content = message.content
        metrics["response_content_chars"] = len(message.content or "")
        metrics["response_reasoning_chars"] = len(getattr(message, "reasoning_content", None) or "")
        if getattr(message, "refusal", None):
            raise ValueError("LLM refused evaluation")
        metrics["stage"] = "pydantic_validation"
        try:
            decoded = json.loads(message.content or "")
            metrics["response_json_type"] = type(decoded).__name__
            if isinstance(decoded, dict):
                metrics["response_json_keys"] = sorted(decoded)
        except (ValueError, TypeError):
            metrics["response_json_type"] = "INVALID_JSON"
        validation_started = perf_counter()
        evaluation = LLMStructuredEvaluation.model_validate_json(message.content or "")
        metrics["pydantic_valid"] = True
        metrics["stage"] = "evidence_validation"
        reviewer._validate_llm_evaluation(evaluation, resume)
        metrics["evidence_valid"] = True
        metrics["validation_elapsed_seconds"] = round(perf_counter() - validation_started, 4)
        metrics.update(status="SUCCEEDED", stage="completed")
        return evaluation
    except Exception as exc:
        metrics.update(status="FAILED", failure_type=type(exc).__name__, cause_chain=error_chain(exc))
        metrics["http_status"] = getattr(exc, "status_code", None)
        metrics.setdefault("api_elapsed_seconds", round(perf_counter() - api_started, 4))
        if isinstance(exc, ValidationError):
            metrics["validation_errors"] = [{"path": ".".join(map(str, e["loc"])), "type": e["type"]}
                                             for e in exc.errors(include_input=False)]
        elif isinstance(exc, ValueError):
            metrics["validation_error"] = str(exc)
        raise
    finally:
        metrics["elapsed_seconds"] = round(perf_counter() - started, 4)
        logging.getLogger("ScoringEngine").info("Tier3 call metrics: %s", json.dumps(metrics, ensure_ascii=False))
