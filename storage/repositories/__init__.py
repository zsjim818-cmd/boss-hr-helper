"""Authorized business API. Raw SQLite connections are deployment/internal only."""
from .base import NotFound, PermissionDenied
from .identity import IdentityRepository
from .policies import PolicyRepository
from .batches import ScanBatchRepository
from .candidates import CandidateRepository
from .evaluations import EvaluationRepository
from .compiler import CompilerRepository
from .embeddings import EmbeddingRepository
from .pipeline import PipelineRepository
from .browser import BrowserRepository


class RecruitmentRepository(IdentityRepository, PolicyRepository, ScanBatchRepository,
                             CandidateRepository, EvaluationRepository, CompilerRepository,
                             EmbeddingRepository, PipelineRepository, BrowserRepository):
    """One transaction/authorization boundary with domain-specific implementations."""


__all__ = ["RecruitmentRepository", "NotFound", "PermissionDenied"]
