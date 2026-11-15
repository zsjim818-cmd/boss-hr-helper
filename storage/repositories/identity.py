"""Organization bootstrap, memberships, private jobs and explicit grants."""
from contextlib import closing

from storage.models import AccessContext
from .base import BaseRepository, PERMISSIONS, uid, utcnow


class IdentityRepository(BaseRepository):
    def create_user(self, system, auth_subject, *, display_name="", user_id=None):
        if not isinstance(auth_subject, str) or not auth_subject.strip():
            raise ValueError("auth_subject must be nonempty")
        user_id = user_id or uid()
        with self._provision(system) as connection:
            connection.execute("INSERT INTO users(user_id,auth_subject,display_name) VALUES(?,?,?)",
                               (user_id, auth_subject, display_name))
            return self.one(connection, "SELECT * FROM users WHERE user_id=?", (user_id,))

    def create_organization(self, system, name, owner_user_id, *, org_id=None):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Organization name must be nonempty")
        org_id = org_id or uid()
        with self._provision(system) as connection:
            connection.execute("INSERT INTO organizations(org_id,name) VALUES(?,?)", (org_id, name))
            connection.execute("""INSERT INTO organization_memberships(org_id,user_id,org_role)
                VALUES(?,?,'ORG_ADMIN')""", (org_id, owner_user_id))
            self.audit(connection, AccessContext(org_id, owner_user_id), "ORGANIZATION_CREATED", org_id)
            return self.one(connection, "SELECT * FROM organizations WHERE org_id=?", (org_id,))

    def list_local_identities(self):
        """LOCAL DEMO ONLY: enumerate active orgs/members for the mock selector.

        Never expose this over a real deployment; the API only registers the
        corresponding route in deterministic mode with trusted headers enabled.
        """
        with closing(self.database.connect()) as connection:
            rows = connection.execute("""SELECT m.org_id, m.user_id, m.org_role
                FROM organization_memberships m
                JOIN organizations o ON o.org_id=m.org_id AND o.state='ACTIVE'
                JOIN users u ON u.user_id=m.user_id AND u.state='ACTIVE'
                WHERE m.state='ACTIVE'
                ORDER BY m.org_id, m.org_role DESC, m.user_id""").fetchall()
            return [dict(row) for row in rows]

    def add_organization_member(self, context, user_id, *, org_role="HR"):
        with self.tx(context) as connection:
            self.require_admin(connection, context)
            connection.execute("INSERT INTO organization_memberships(org_id,user_id,org_role) VALUES(?,?,?)",
                               (context.org_id, user_id, org_role))
            self.audit(connection, context, "MEMBER_ADDED", user_id, {"role": org_role})
            return self.one(connection, "SELECT * FROM organization_memberships WHERE org_id=? AND user_id=?",
                            (context.org_id, user_id))

    def set_member_state(self, context, user_id, state):
        if state not in ("ACTIVE", "DISABLED"):
            raise ValueError("Invalid member state")
        with self.tx(context) as connection:
            self.require_admin(connection, context)
            member = self.one(connection, "SELECT * FROM organization_memberships WHERE org_id=? AND user_id=?",
                              (context.org_id, user_id))
            if member["org_role"] == "ORG_ADMIN" and state == "DISABLED":
                count = connection.execute("""SELECT count(*) FROM organization_memberships
                    WHERE org_id=? AND org_role='ORG_ADMIN' AND state='ACTIVE'""", (context.org_id,)).fetchone()[0]
                if count <= 1:
                    raise ValueError("Cannot disable the last organization administrator")
            connection.execute("UPDATE organization_memberships SET state=? WHERE org_id=? AND user_id=?",
                               (state, context.org_id, user_id))
            self.audit(connection, context, "MEMBER_STATE_CHANGED", user_id, {"state": state})

    def create_job(self, context, title, *, job_id=None):
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Job title must be nonempty")
        job_id = job_id or uid()
        with self.tx(context) as connection:
            connection.execute("INSERT INTO jobs(org_id,job_id,title,created_by) VALUES(?,?,?,?)",
                               (context.org_id, job_id, title, context.user_id))
            columns = ",".join(PERMISSIONS.values())
            connection.execute(f"""INSERT INTO job_access_grants
                (org_id,job_id,user_id,granted_by,{columns}) VALUES(?,?,?,?,{','.join('1' for _ in PERMISSIONS)})""",
                (context.org_id, job_id, context.user_id, context.user_id))
            self.audit(connection, context, "JOB_CREATED", job_id)
            return self.one(connection, "SELECT * FROM jobs WHERE org_id=? AND job_id=?", (context.org_id, job_id))

    def get_job(self, context, job_id):
        with self.read(context, job_id) as connection:
            return self.one(connection, "SELECT * FROM jobs WHERE org_id=? AND job_id=?", (context.org_id, job_id))

    def list_jobs(self, context):
        with self.read(context) as connection:
            role = self._authorize(connection, context)
            rows = connection.execute("""SELECT j.* FROM jobs j WHERE j.org_id=? AND
                (?='ORG_ADMIN' OR EXISTS(SELECT 1 FROM job_access_grants g WHERE
                 g.org_id=j.org_id AND g.job_id=j.job_id AND g.user_id=?
                 AND g.revoked_at IS NULL AND g.can_view_resume=1)) ORDER BY j.created_at,j.job_id""",
                (context.org_id, role, context.user_id)).fetchall()
            return [dict(row) for row in rows]

    def grant_job_access(self, context, job_id, user_id, *, permissions=("view",)):
        if not set(permissions) <= set(PERMISSIONS):
            raise ValueError("Unknown grant permission")
        if permissions and "view" not in permissions:
            raise ValueError("Job permissions require view access")
        with self.tx(context, job_id, "manage_grants") as connection:
            self.one(connection, """SELECT user_id FROM organization_memberships
                WHERE org_id=? AND user_id=? AND state='ACTIVE'""", (context.org_id, user_id))
            values = [int(name in permissions) for name in PERMISSIONS]
            columns = list(PERMISSIONS.values())
            existing = connection.execute("SELECT 1 FROM job_access_grants WHERE org_id=? AND job_id=? AND user_id=?",
                                          (context.org_id, job_id, user_id)).fetchone()
            if existing:
                assignments = ",".join(f"{column}=?" for column in columns)
                connection.execute(f"""UPDATE job_access_grants SET {assignments},granted_by=?,
                    granted_at=?,revoked_at=NULL WHERE org_id=? AND job_id=? AND user_id=?""",
                    (*values, context.user_id, utcnow(), context.org_id, job_id, user_id))
            else:
                connection.execute(f"""INSERT INTO job_access_grants
                    (org_id,job_id,user_id,granted_by,{','.join(columns)}) VALUES({','.join('?' for _ in range(12))})""",
                    (context.org_id, job_id, user_id, context.user_id, *values))
            self.audit(connection, context, "JOB_ACCESS_GRANTED", job_id, {"user_id": user_id, "permissions": sorted(permissions)})
            return self.one(connection, "SELECT * FROM job_access_grants WHERE org_id=? AND job_id=? AND user_id=?",
                            (context.org_id, job_id, user_id))

    def revoke_job_access(self, context, job_id, user_id):
        with self.tx(context, job_id, "manage_grants") as connection:
            connection.execute("UPDATE job_access_grants SET revoked_at=? WHERE org_id=? AND job_id=? AND user_id=?",
                               (utcnow(), context.org_id, job_id, user_id))
            self.audit(connection, context, "JOB_ACCESS_REVOKED", job_id, {"user_id": user_id})

    def list_audit_events(self, context, job_id=None):
        with self.read(context, job_id) as connection:
            self.require_admin(connection, context)
            return [dict(row) for row in connection.execute("SELECT * FROM audit_events WHERE org_id=? ORDER BY created_at,event_id",
                                                          (context.org_id,))]
