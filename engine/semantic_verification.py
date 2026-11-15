"""Prove that a loaded embedding model's runtime behaviour matches its declared profile.

``pooling_strategy`` participates in the immutable EmbeddingProfile hash, but a
hash is still only a claim: nothing else in the pipeline inspects the actually
loaded sentence-transformers model.  This module turns "the profile says CLS"
into a verifiable fact by reading the real pooling module of an already loaded
model, and is used by the ``embedding-check`` command, the Stage 5 service layer
and the pooling regression tests.
"""
from __future__ import annotations


# Recent sentence-transformers exposes one lowercase ``pooling_mode`` string per
# Pooling module; older releases expose boolean flags.  Both map to the profile
# vocabulary declared in schemas.semantic.EmbeddingProfile.pooling_strategy.
_POOLING_MODE_ALIASES = {
    "cls": "CLS",
    "mean": "MEAN",
    "lasttoken": "LAST_TOKEN",
    "last_token": "LAST_TOKEN",
    "weightedmean": "MEAN",
}


def _profile_payload(profile) -> dict:
    if hasattr(profile, "model_dump"):
        return profile.model_dump(mode="json")
    if isinstance(profile, dict):
        return dict(profile)
    raise TypeError("Embedding profile must be a Pydantic model or object mapping")


def _modules(model):
    """SentenceTransformer is a torch Sequential; fall back to an empty list."""
    try:
        return list(model)
    except TypeError:
        return []


def detect_pooling_strategy(model) -> str:
    """Return the model's actual pooling strategy in profile vocabulary."""
    for module in _modules(model):
        mode = getattr(module, "pooling_mode", None)
        if isinstance(mode, str) and mode.strip():
            key = mode.strip().lower()
            if key in _POOLING_MODE_ALIASES:
                return _POOLING_MODE_ALIASES[key]
        flags = {
            "CLS": getattr(module, "pooling_mode_cls_token", False),
            "MEAN": getattr(module, "pooling_mode_mean_tokens", False),
            "LAST_TOKEN": getattr(module, "pooling_mode_lasttoken", False),
        }
        active = [name for name, enabled in flags.items() if enabled]
        if len(active) == 1:
            return active[0]
        if len(active) > 1:
            raise ValueError("Loaded model enables multiple pooling modes; the profile claim is unverifiable")
    raise ValueError("Loaded model exposes no recognisable pooling module to verify the profile claim")


def detect_embedding_dimension(model) -> int:
    # Newer sentence-transformers renamed the getter; support both without warning.
    for name in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        getter = getattr(model, name, None)
        if callable(getter):
            dimension = getter()
            if isinstance(dimension, int) and dimension > 0:
                return dimension
            break
    for module in _modules(model):
        dimension = getattr(module, "embedding_dimension", None)
        if isinstance(dimension, int) and dimension > 0:
            return dimension
    raise ValueError("Loaded model exposes no verifiable embedding dimension")


def detect_max_seq_length(model):
    value = getattr(model, "max_seq_length", None)
    return value if isinstance(value, int) and value > 0 else None


def verify_embedding_model(model, profile) -> dict:
    """Compare a loaded model against an EmbeddingProfile without mutating anything."""
    payload = _profile_payload(profile)
    declared = {
        "pooling": payload.get("pooling_strategy", payload.get("pooling")),
        "dimension": payload.get("embedding_dimension", payload.get("dimension")),
        "max_length": payload.get("max_length"),
    }
    actual = {
        "pooling": detect_pooling_strategy(model),
        "dimension": detect_embedding_dimension(model),
        "max_length": detect_max_seq_length(model),
    }
    checks = {}
    for name in ("pooling", "dimension"):
        checks[name] = {"declared": declared[name], "actual": actual[name],
                        "ok": declared[name] == actual[name]}
    # max_length is only comparable when the loaded model actually reports one.
    if declared["max_length"] is not None and actual["max_length"] is not None:
        checks["max_length"] = {"declared": declared["max_length"],
                                "actual": actual["max_length"],
                                "ok": declared["max_length"] == actual["max_length"]}
    return {"ok": all(item["ok"] for item in checks.values()), "checks": checks}


def assert_embedding_model_matches(model, profile) -> dict:
    """Raise when the loaded model does not match the declared immutable profile."""
    report = verify_embedding_model(model, profile)
    if not report["ok"]:
        mismatches = {name: item for name, item in report["checks"].items() if not item["ok"]}
        raise ValueError(f"Loaded embedding model does not match the declared profile: {mismatches}")
    return report
