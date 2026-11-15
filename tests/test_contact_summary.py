"""Candidate-level contact summary, multi-HR rules and cross-job independence."""
import unittest

from tests.test_api_pipeline import ApiCase


class ContactSummaryCase(ApiCase):
    def import_keyed(self, key, *, job_id="job"):
        batch = self.client.post(f"/api/jobs/{job_id}/batches", headers=self.headers)
        self.assertEqual(batch.status_code, 200, batch.text)
        imported = self.client.post(
            f"/api/jobs/{job_id}/batches/{batch.json()['batch_id']}/candidates",
            headers=self.headers, json={"candidate_key": key, "resume": self._resume(key)})
        self.assertEqual(imported.status_code, 200, imported.text)
        return imported.json()["job_candidate"]["job_candidate_id"]

    @staticmethod
    def _resume(key):
        from tests.pipeline_support import resume_for
        return resume_for(key)

    def score(self, jcid, mode="deep_mode", *, job_id="job"):
        started = self.client.post(
            f"/api/jobs/{job_id}/candidates/{jcid}/score", headers=self.headers,
            json={"scoring_mode": mode, "idempotency_key": f"summary-{mode}-{jcid}"})
        self.assertEqual(started.status_code, 200, started.text)
        body = started.json()
        if body.get("reused"):
            return body["evaluation"]
        finished = self.wait_task(body["task"]["pipeline_task_id"], job_id=job_id)
        self.assertEqual(finished["task"]["status"], "SUCCEEDED", finished)
        return finished["evaluation"]

    def approve(self, evaluation_id, *, job_id="job", headers=None):
        response = self.client.post(
            f"/api/jobs/{job_id}/evaluations/{evaluation_id}/reviews",
            headers=headers or self.headers,
            json={"action": "APPROVE", "reason": "summary acceptance"})
        self.assertEqual(response.status_code, 200, response.text)

    def contact(self, evaluation_id, actor_headers=None, *, job_id="job",
                action="CONTACTED"):
        response = self.client.post(
            f"/api/jobs/{job_id}/evaluations/{evaluation_id}/contact",
            headers=actor_headers or self.headers,
            json={"action": action, "note": "manual"})
        self.assertEqual(response.status_code, 200, response.text)

    def dry_run(self, evaluation_id, *, job_id="job", headers=None):
        return self.client.post(
            f"/api/jobs/{job_id}/evaluations/{evaluation_id}/greeting-dry-run",
            headers=headers or self.headers, json={"message": "hello"})

    def summary(self, jcid, *, job_id="job", headers=None):
        response = self.client.get(
            f"/api/jobs/{job_id}/candidates/{jcid}/contact-summary",
            headers=headers or self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def add_admin(self, user_id):
        self.repo.create_user(self.system, f"test:{user_id}", user_id=user_id)
        self.repo.add_organization_member(self.ctx, user_id, org_role="ORG_ADMIN")
        return {"X-Org-Id": "org", "X-User-Id": user_id}


class ContactSummaryTests(ContactSummaryCase):
    def setUp(self):
        super().setUp()
        self.jcid = self.import_keyed("job:geek-java-1")
        self.fast = self.score(self.jcid, "fast_mode")
        self.deep = self.score(self.jcid, "deep_mode")
        self.approve(self.fast["pipeline_evaluation_id"])
        self.approve(self.deep["pipeline_evaluation_id"])

    def test_summary_is_candidate_level_across_fast_and_deep(self):
        fast_id = self.fast["pipeline_evaluation_id"]
        deep_id = self.deep["pipeline_evaluation_id"]
        empty = self.summary(self.jcid)
        self.assertFalse(empty["contacted"])
        self.assertEqual(empty["contacted_by"], [])
        self.assertFalse(empty["current_user_contacted"])

        self.contact(fast_id)
        summary = self.summary(self.jcid)
        self.assertTrue(summary["contacted"])
        self.assertEqual(summary["contacted_by"], ["owner"])
        self.assertTrue(summary["current_user_contacted"])
        self.assertEqual(summary["latest_contact"]["pipeline_evaluation_id"], fast_id)
        self.assertEqual(summary["latest_contact"]["scoring_mode"], "fast_mode")
        # Per-evaluation events stay separate; the summary is candidate-level.
        deep_events = self.client.get(
            f"/api/jobs/job/evaluations/{deep_id}/contact", headers=self.headers).json()
        self.assertEqual(deep_events["events"], [])

        detail = self.client.get(
            f"/api/jobs/job/candidates/{self.jcid}", headers=self.headers).json()
        self.assertTrue(detail["contact_summary"]["contacted"])
        self.assertEqual(detail["contact_summary"]["contacted_by"], ["owner"])

    def test_different_hr_accounts_may_each_contact_once(self):
        hr2 = self.add_admin("hr2")
        self.contact(self.fast["pipeline_evaluation_id"])

        hr2_summary = self.summary(self.jcid, headers=hr2)
        self.assertTrue(hr2_summary["contacted"])
        self.assertFalse(hr2_summary["current_user_contacted"])
        self.assertEqual(hr2_summary["contacted_by"], ["owner"])

        # A different HR is not blocked by the owner's contact.
        allowed = self.dry_run(self.deep["pipeline_evaluation_id"], headers=hr2)
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()["state"], "DRY_RUN_READY")
        self.contact(self.deep["pipeline_evaluation_id"], hr2)

        merged = self.summary(self.jcid)
        self.assertEqual(merged["contacted_by"], ["hr2", "owner"])
        self.assertEqual(merged["pending_dry_run_count"], 1)
        self.assertFalse(merged["current_user_pending_dry_run"])
        self.assertEqual(merged["latest_pending_dry_run"]["actor_id"], "hr2")
        # The owner already contacted the candidate from their own account.
        blocked = self.dry_run(self.deep["pipeline_evaluation_id"])
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["state"], "ALREADY_SENT")

    def test_cross_job_contact_is_independent(self):
        self.publish_case("java_backend", job_id="job2")
        jcid2 = self.import_keyed("job2:geek-java-1", job_id="job2")
        evaluation2 = self.score(jcid2, job_id="job2")
        self.approve(evaluation2["pipeline_evaluation_id"], job_id="job2")

        self.contact(self.fast["pipeline_evaluation_id"])
        self.assertTrue(self.summary(self.jcid)["contacted"])
        self.assertFalse(self.summary(jcid2, job_id="job2")["contacted"])

        allowed = self.dry_run(evaluation2["pipeline_evaluation_id"], job_id="job2")
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json()["state"], "DRY_RUN_READY")

    def test_dry_run_is_idempotent_and_scoped_per_actor(self):
        deep_id = self.deep["pipeline_evaluation_id"]
        first = self.dry_run(deep_id)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()["idempotent"])
        event_id = first.json()["contact_event"]["contact_event_id"]

        again = self.dry_run(deep_id)
        self.assertTrue(again.json()["idempotent"])
        self.assertEqual(again.json()["contact_event"]["contact_event_id"], event_id)
        self.assertEqual(self.summary(self.jcid)["pending_dry_run_count"], 1)
        self.assertTrue(self.summary(self.jcid)["current_user_pending_dry_run"])

        hr2 = self.add_admin("hr2")
        other = self.dry_run(deep_id, headers=hr2)
        self.assertEqual(other.status_code, 200, other.text)
        self.assertFalse(other.json()["idempotent"])
        self.assertNotEqual(other.json()["contact_event"]["contact_event_id"], event_id)
        self.assertEqual(self.summary(self.jcid)["pending_dry_run_count"], 2)

    def test_summary_requires_job_access(self):
        import json as _json
        self.repo.create_user(self.system, "test:outsider", user_id="outsider")
        self.repo.add_organization_member(self.ctx, "outsider", org_role="HR")
        response = self.client.get(
            f"/api/jobs/job/candidates/{self.jcid}/contact-summary",
            headers={"X-Org-Id": "org", "X-User-Id": "outsider"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("PermissionDenied", _json.dumps(response.json()))


if __name__ == "__main__":
    unittest.main()
