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
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from config.policy_compiler import JobPolicyCompiler
from config.policy_service import PolicyQualityBlocked
from engine.demo_models import MOCK_MODEL, DeterministicLLMClient, demo_tier3_response
from engine.policy_llm_reviewer import PolicyReviewService
from engine.policy_scoring_pipeline import PolicyScoringPipeline
from browser.boss_readonly_adapter import BossReadonlyCdpAdapter
from browser.contracts import BrowserError
from browser.mock_adapter import MockBossAdapter
from browser.mock_data import mock_cards
from browser.runtime import BrowserRuntime
from browser.sessions import BrowserSessionRegistry
from browser.source import BrowserSourceConfig, load_browser_source
from server.api import (browser_router, candidates_router, jobs_router,
                        policies_router, reviews_router, scoring_router)
from server.auth import get_context, get_optional_context
from server.demo_compiler import DemoJobPolicyCompiler
from server.responses import RuntimeModeOut
from server.semantic_runtime import (RUNTIME_MODES, DeterministicSemanticRuntime,
                                     LiveSemanticRuntime)
from storage.models import AccessContext
from storage.repositories import NotFound, PermissionDenied
from storage.repositories.browser import ScanAlreadyRunning
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


def _job_kind(repository, context, job_id):
    title = repository.get_job(context, job_id)["title"]
    return ("ai" if ("AI" in title or "自动化" in title)
            else "sales" if "销售" in title else "java")


def _default_browser_adapter(repository, context, job_id):
    """LOCAL MOCK ONLY: deterministic in-memory recruitment page."""
    kind = _job_kind(repository, context, job_id)
    return MockBossAdapter(mock_cards(job_id, kind, 3), page_size=5)


def _browser_factory(source: BrowserSourceConfig, runtime: BrowserRuntime | None,
                     registry=None):
    def factory(repository, context, job_id):
        if source.name == "mock":
            return _default_browser_adapter(repository, context, job_id)
        if source.name == "cdp_mock":
            kind = _job_kind(repository, context, job_id)
            if registry is not None:
                session = registry.runtime_for(context)
                adapter = session.adapter(mock_cards(job_id, kind, 3))
                binding = registry.binding_for(context)
                adapter.browser_account_id = binding.browser_account_id
            else:
                adapter = runtime.adapter(mock_cards(job_id, kind, 3))
            # Connection is owned by the HTTP route (single connect per request).
            return adapter
        if source.name == "boss_readonly":
            # Real BOSS collection stays hard-disabled until an authorised HR
            # account and a verified DOM/API contract exist.  A local flag alone
            # must never open real collection.
            raise BrowserError(
                "REAL_CONTRACT_UNVERIFIED",
                "boss_readonly real collection is disabled: no verified BOSS "
                "DOM/API contract or authorised account. Use mock/cdp_mock.",
                recoverable=False)
        raise BrowserError("UNSUPPORTED", f"browser source {source.name!r} is disabled",
                           recoverable=False)
    return factory


def _resolve_browser_source(mode, browser_source):
    if browser_source is None:
        return load_browser_source(mode)
    if isinstance(browser_source, BrowserSourceConfig):
        return browser_source
    env = dict(os.environ)
    env["BROWSER_SOURCE"] = str(browser_source)
    return load_browser_source(mode, environ=env)


def create_app(repository, *, mode=None, compiler_factory=None, pipeline_factory=None,
               semantic_runtime=None, max_workers=2, allow_trusted_headers=None,
               browser_adapter_factory=None, browser_source=None,
               browser_runtime=None, browser_session_registry=None):
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
    source = _resolve_browser_source(mode, browser_source)
    runtime = browser_runtime
    registry = browser_session_registry
    if registry is None and runtime is None and source.name in ("cdp_mock", "boss_readonly"):
        registry = BrowserSessionRegistry(source, store=repository)
    if runtime is None and source.name in ("cdp_mock", "boss_readonly") and registry is None:
        runtime = BrowserRuntime(source)

    @asynccontextmanager
    async def lifespan(application):
        yield
        # Only closes browsers this process launched; external CDP browsers are
        # merely disconnected (see CDPConnectionManager.close).
        if registry is not None:
            registry.close()
        if runtime is not None:
            runtime.close()

    app = FastAPI(title="Boss HR Helper API", version="3.1.0",
                  description="Multi-job dynamic resume matching (Tier 0-3) with HR review",
                  lifespan=lifespan)
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
    app.state.browser_source = source
    app.state.browser_runtime = runtime
    app.state.browser_session_registry = registry
    app.state.browser_adapter_factory = (
        browser_adapter_factory or _browser_factory(source, runtime, registry))
    app.state.browser_scan_results = {}
    app.state.browser_scan_cancel = set()
    app.state.executor = ThreadPoolExecutor(max_workers=max_workers)

    for router in (jobs_router, policies_router, candidates_router,
                   scoring_router, reviews_router):
        app.include_router(router)
    if source.enabled:
        # Browser integration is independent of the model run mode; the Mock
        # sources never touch a real BOSS session.
        app.include_router(browser_router)

    @app.get("/health", tags=["health"])
    @app.get("/api/health", tags=["health"])
    def health():
        return {"ok": True, "status": "CONNECTED", "service": "boss-hr-helper-api",
                "mode": mode}

    @app.get("/api/runtime-mode", tags=["health"], response_model=RuntimeModeOut)
    def runtime_mode(ctx: AccessContext | None = Depends(get_optional_context)):
        body = dict(semantic_runtime.describe())
        body.update({"auth_mode": "trusted-headers" if allow_trusted_headers
                     else "gateway-required",
                     "local_only": mode == "deterministic",
                     "browser_source": source.describe()})
        if registry is not None:
            # Least privilege: anonymous callers get no session details; an
            # authenticated HR sees only their own session (no other org/user,
            # no local absolute paths).
            body["browser_source"]["session"] = None
            if allow_trusted_headers and ctx is not None:
                body["browser_source"]["session"] = registry.describe_for(ctx)
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

    @app.exception_handler(BrowserError)
    def browser_error(request: Request, exc: BrowserError):
        # Connection/DOM loss is reported as a structured failure, never as an
        # empty-but-successful scan.
        unavailable = exc.code in ("BROWSER_UNAVAILABLE", "CDP_CONNECT_FAILED",
                                   "BROWSER_CLOSED", "TIMEOUT", "PAGE_UNAVAILABLE",
                                   "LOGIN_REQUIRED", "TARGET_MISSING",
                                   "UNEXPECTED_STATE", "EMPTY_UNVERIFIED")
        return JSONResponse(status_code=503 if unavailable else 409, content={
            "error_type": "BrowserError", "code": exc.code,
            "message": str(exc), "recoverable": exc.recoverable})

    @app.exception_handler(PipelineConflict)
    def pipeline_conflict(request: Request, exc: PipelineConflict):
        return JSONResponse(status_code=409, content={
            "error_type": "PipelineConflict", "message": str(exc)})

    @app.exception_handler(ScanAlreadyRunning)
    def scan_already_running(request: Request, exc: ScanAlreadyRunning):
        return JSONResponse(status_code=409, content={
            "error_type": "ScanAlreadyRunning",
            "code": "SCAN_ALREADY_RUNNING", "message": str(exc)})

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
