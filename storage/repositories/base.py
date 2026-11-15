"""Repository-only SQL sessions, tenant authorization, canonical JSON and audit."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from storage.models import AccessContext, ProvisioningContext


class NotFound(LookupError):
    pass


class PermissionDenied(PermissionError):
    pass


PERMISSIONS = {
    "view": "can_view_resume", "edit": "can_edit_policy", "publish": "can_publish_policy",
    "collect": "can_collect", "evaluate": "can_score", "review": "can_review",
    "export": "can_export", "manage_grants": "can_manage_grants",
}


def uid():
    return uuid4().hex


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(json_dump(value).encode("utf-8")).hexdigest()


def one(connection, sql, parameters=()):
    row = connection.execute(sql, parameters).fetchone()
    if row is None:
        raise NotFound("Record not found in the authorized scope")
    return dict(row)


def check_snapshot(value):
    if not isinstance(value, dict):
        raise ValueError("Snapshot must be a JSON object")
    def visit(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if str(key).lower() in {"api_key", "llm_api_key", "authorization", "password", "access_token"}:
                    raise ValueError("Credentials must not be stored in business snapshots")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
    visit(value)
    json_dump(value)


class BaseRepository:
    """Only business methods are public API; tx/read are internal extension hooks."""
    def __init__(self, database):
        self.database = database

    one = staticmethod(one)

    def _authorize(self, connection, context, job_id=None, permission="view"):
        if not isinstance(context, AccessContext):
            raise PermissionDenied("A trusted AccessContext is required")
        member = connection.execute("""SELECT m.org_role FROM organization_memberships m
            JOIN organizations o ON o.org_id=m.org_id
            JOIN users u ON u.user_id=m.user_id
            WHERE m.org_id=? AND m.user_id=? AND m.state='ACTIVE'
              AND o.state='ACTIVE' AND u.state='ACTIVE'""", (context.org_id, context.user_id)).fetchone()
        if member is None:
            raise PermissionDenied("No active membership in this organization")
        if job_id is None:
            return member["org_role"]
        if permission not in PERMISSIONS:
            raise ValueError("Unknown repository permission")
        one(connection, "SELECT job_id FROM jobs WHERE org_id=? AND job_id=?", (context.org_id, job_id))
        if member["org_role"] == "ORG_ADMIN":
            return member["org_role"]
        column = PERMISSIONS[permission]  # Column comes from this constant whitelist.
        grant = connection.execute(f"""SELECT 1 FROM job_access_grants
            WHERE org_id=? AND job_id=? AND user_id=? AND revoked_at IS NULL
              AND can_view_resume=1 AND {column}=1""",
            (context.org_id, job_id, context.user_id)).fetchone()
        if grant is None:
            raise PermissionDenied("No active permission for this job")
        return member["org_role"]

    @contextmanager
    def _session(self, context, job_id, permission, write):
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                self._authorize(connection, context, job_id, permission)
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def tx(self, context, job_id=None, permission="view"):
        return self._session(context, job_id, permission, True)

    def read(self, context, job_id=None, permission="view"):
        return self._session(context, job_id, permission, False)

    @contextmanager
    def _provision(self, context):
        if not isinstance(context, ProvisioningContext):
            raise PermissionDenied("Deployment provisioning context required")
        with closing(self.database.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def require_admin(self, connection, context):
        if self._authorize(connection, context) != "ORG_ADMIN":
            raise PermissionDenied("Organization administrator required")

    def audit(self, connection, context, action, entity_id, payload=None):
        connection.execute("""INSERT INTO audit_events
            (org_id,event_id,actor_id,action,object_type,object_id,details_json,created_at)
            VALUES(?,?,?,?,?,?,?,?)""", (context.org_id, uid(), context.user_id, action,
                                          action.lower().split("_")[0], entity_id,
                                          json_dump(payload or {}), utcnow()))
