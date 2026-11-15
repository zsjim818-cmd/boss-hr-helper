"""FastAPI application factory; all business logic is delegated to services.

Run modes (``APP_RUN_MODE`` env or explicit ``mode=``):

- ``deterministic`` (default): demo compiler + deterministic Tier 2/Tier 3
  doubles.  No BGE/Qwen required; outputs are clearly mock-labelled.
- ``live``: real Qwen compiler/Tier 3 and local BGE embeddings, prepared
  on demand through ``POST /api/jobs/{job_id}/semantic-prepare`` or before a
  deep pipeline run.
"""
import os
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from config.policy_compiler import JobPolicyCompiler
from config.policy_service import PolicyQualityBlocked
from engine.demo_models import MOCK_MODEL, DeterministicLLMClient, demo_tier3_response
from engine.policy_llm_reviewer import PolicyReviewService
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from server.api import (candidates_router, jobs_router, policies_router,
                        reviews_router, scoring_router)
from server.auth import get_context
from server.demo_compiler import DemoJobPolicyCompiler
from server.responses import RuntimeModeOut
from server.semantic_runtime import (RUNTIME_MODES, DeterministicSemanticRuntime,
                                     LiveSemanticRuntime)
from storage.repositories import NotFound, PermissionDenied
from storage.repositories.pipeline import PipelineConflict


def _deterministic_pipeline(semantic_runtime):
    def factory(repository):
        return PolicyScoringPipeline(
            repository, semantic_service=semantic_runtime.semantic_service(repository),
            review_service=PolicyReviewService(
                repository, client=DeterministicLLMClient(response=demo_tier3_response),
                model_name=MOCK_MODEL))
    return factory


def _live_pipeline(semantic_runtime):
    def factory(repository):
        return PolicyScoringPipeline(
            repository, semantic_service=semantic_runtime.semantic_service(repository),
            review_service=PolicyReviewService(repository))
    return factory


def create_app(repository, *, mode=None, compiler_factory=None, pipeline_factory=None,
               semantic_runtime=None, max_workers=2, allow_trusted_headers=None):
    mode = (mode or os.getenv("APP_RUN_MODE", "deterministic")).strip().lower()
    if mode not in RUNTIME_MODES:
        raise ValueError(f"APP_RUN_MODE must be one of {RUNTIME_MODES}")
    # Trusted browser headers are a LOCAL demo convenience.  Real deployments
    # must inject identity from an authenticated gateway; live mode disables
    # implicit trust unless explicitly opted in for a local dev machine.
    if allow_trusted_headers is None:
        allow_trusted_headers = (mode == "deterministic"
                                 or os.getenv("APP_ALLOW_TRUSTED_HEADERS", "") == "1")
    if semantic_runtime is None:
        semantic_runtime = (DeterministicSemanticRuntime() if mode == "deterministic"
                            else LiveSemanticRuntime(repository))
    if compiler_factory is None:
        compiler_factory = (DemoJobPolicyCompiler if mode == "deterministic"
                            else JobPolicyCompiler)
    if pipeline_factory is None:
        pipeline_factory = (_deterministic_pipeline(semantic_runtime) if mode == "deterministic"
                            else _live_pipeline(semantic_runtime))
    app = FastAPI(title="Boss HR Helper API", version="3.1.0",
                  description="Multi-job dynamic resume matching (Tier 0-3) with HR review")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.state.repository = repository
    app.state.mode = mode
    app.state.allow_trusted_headers = allow_trusted_headers
    app.state.semantic_runtime = semantic_runtime
    app.state.compiler_factory = compiler_factory
    app.state.pipeline_factory = pipeline_factory
    app.state.executor = ThreadPoolExecutor(max_workers=max_workers)

    for router in (jobs_router, policies_router, candidates_router,
                   scoring_router, reviews_router):
        app.include_router(router)

    @app.get("/health", tags=["health"])
    @app.get("/api/health", tags=["health"])
    def health():
        return {"ok": True, "status": "CONNECTED", "service": "boss-hr-helper-api",
                "mode": mode}

    @app.get("/api/runtime-mode", tags=["health"], response_model=RuntimeModeOut)
    def runtime_mode():
        body = dict(semantic_runtime.describe())
        body.update({"auth_mode": "trusted-headers" if allow_trusted_headers
                     else "gateway-required",
                     "local_only": mode == "deterministic"})
        return body

    if not allow_trusted_headers:
        def _identity_unavailable(request: Request):
            raise HTTPException(
                status_code=503,
                detail="Trusted-header authentication is disabled in this deployment; "
                       "configure a real authentication gateway before exposing the API")
        app.dependency_overrides[get_context] = _identity_unavailable
    else:
        @app.get("/api/local-identities", tags=["health"])
        def local_identities():
            """LOCAL DEMO ONLY: identity presets for the mock HR selector."""
            return {"identities": repository.list_local_identities()}

    @app.exception_handler(PermissionDenied)
    def permission_denied(request: Request, exc: PermissionDenied):
        return JSONResponse(status_code=403, content={"error_type": "PermissionDenied",
                                                      "message": str(exc)})

    @app.exception_handler(NotFound)
    def not_found(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"error_type": "NotFound",
                                                      "message": str(exc)})

    @app.exception_handler(PipelineConflict)
    def pipeline_conflict(request: Request, exc: PipelineConflict):
        return JSONResponse(status_code=409, content={
            "error_type": "PipelineConflict", "message": str(exc)})

    @app.exception_handler(PolicyQualityBlocked)
    def quality_blocked(request: Request, exc: PolicyQualityBlocked):
        return JSONResponse(status_code=409, content={
            "error_type": "PolicyQualityBlocked", "message": str(exc),
            "quality_issues": [item.model_dump(mode="json") for item in exc.issues]})

    @app.exception_handler(ValidationError)
    def validation_error(request: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={
            "error_type": "ValidationError",
            "errors": [{"path": ".".join(map(str, item["loc"])), "type": item["type"]}
                       for item in exc.errors(include_input=False)]})

    @app.exception_handler(ValueError)
    def value_error(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"error_type": type(exc).__name__,
                                                      "message": str(exc)})

    return app


def default_app():
    from storage.database import Database
    from storage.database_cli import database_path
    from storage.repositories import RecruitmentRepository
    return create_app(RecruitmentRepository(Database(database_path())))


# `uvicorn server.app:app` entry point (no connection is opened until a request).
app = default_app()
