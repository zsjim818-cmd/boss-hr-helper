"""Browser abstraction layer for the Boss HR pipeline (Sprint 4 Batch 1).

This package intentionally contains no BOSS business database of its own: the
collector/greeting layers write only through ``storage.repositories``.  The
only adapter implemented here targets a local Mock recruitment page or an
in-memory site; real-platform selectors remain unverified (see
reports/sprint4/source_adaptation_audit.md).
"""
