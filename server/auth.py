"""Trusted-identity HTTP dependency.

This deployment expects an upstream gateway (or the local owner console) to
inject ``X-Org-Id`` / ``X-User-Id`` after real authentication.  The API never
accepts an org/user identity from the request body, and every repository call
re-validates membership and job permissions afterwards.
"""
from fastapi import HTTPException, Request

from storage.models import AccessContext


def get_context(request: Request) -> AccessContext:
    org_id = (request.headers.get("X-Org-Id") or "").strip()
    user_id = (request.headers.get("X-User-Id") or "").strip()
    if not org_id or not user_id:
        raise HTTPException(status_code=401, detail="Trusted identity headers X-Org-Id and X-User-Id are required")
    return AccessContext(org_id, user_id)


def get_optional_context(request: Request) -> AccessContext | None:
    """Identity for summary endpoints: absent/incomplete headers => anonymous."""
    org_id = (request.headers.get("X-Org-Id") or "").strip()
    user_id = (request.headers.get("X-User-Id") or "").strip()
    if not org_id or not user_id:
        return None
    return AccessContext(org_id, user_id)
