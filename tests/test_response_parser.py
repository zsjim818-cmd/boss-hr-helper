"""Independent synthetic response parser tests (no BOSS access, no network)."""
import json
import unittest

from browser.contracts import BrowserError
from browser.identity import CandidateDetailParser
from browser.response_parser import BossDetailParser, BossListParser, ResponseObserver

LIST_PAYLOAD = {
    "code": 0,
    "zpData": {
        "geekList": [
            {"encryptGeekId": "geek-syn-1", "encryptJobId": "boss-job-syn",
             "geekCard": {"geekName": "合成甲", "jobTitle": "Java",
                          "geekDegree": "本科", "geekWorkYear": "5年"}},
            {"encryptGeekId": "geek-syn-2", "encryptJobId": "boss-job-syn",
             "geekCard": {"geekName": "合成甲", "jobTitle": "Java",
                          "geekDegree": "大专", "geekWorkYear": "2年"}},
        ]
    },
}

DETAIL_PAYLOAD = {
    "code": 0,
    "zpData": {
        "geekDetailInfo": {
            "geekBaseInfo": {"encryptGeekId": "geek-syn-1", "encryptJobId": "boss-job-syn",
                             "geekName": "合成甲", "geekDegree": "本科",
                             "geekWorkYear": "5年", "geekSkills": "Java,SQL"},
            "geekExpect": {"position": "Java工程师", "city": "北京"},
            "geekWorkExpList": [{"company": "合成公司", "position": "Java",
                                 "responsibility": "负责订单服务开发与SQL优化",
                                 "startDate": "2020-01", "endDate": "2024-01"}],
            "geekProjExpList": [{"projectName": "合成项目", "role": "后端",
                                 "description": "完成接口与自动化测试"}],
            "geekEduExpList": [{"school": "合成大学", "degree": "本科", "major": "软件工程"}],
        }
    },
}


class ListResponseParserTests(unittest.TestCase):
    def test_same_name_different_ids_are_kept_separate(self):
        cards = BossListParser.parse(LIST_PAYLOAD)
        self.assertEqual([card.identity for card in cards],
                         ["geek-syn-1", "geek-syn-2"])
        self.assertEqual(cards[0].platform_job_id, "boss-job-syn")
        self.assertEqual(cards[0].degree, "本科")
        self.assertEqual(cards[1].degree, "大专")

    def test_missing_identity_and_bad_shape_are_structured(self):
        broken = {"zpData": {"geekList": [{"geekCard": {"geekName": "无ID"}}]}}
        with self.assertRaises(BrowserError) as captured:
            BossListParser.parse(broken)
        self.assertEqual(captured.exception.code, "IDENTITY_MISMATCH")
        with self.assertRaises(BrowserError) as captured:
            BossListParser.parse({"zpData": {}})
        self.assertEqual(captured.exception.code, "CONTRACT_MISMATCH")

    def test_observer_rejects_non_allowlisted_host_and_auth_errors(self):
        observer = ResponseObserver()
        with self.assertRaises(BrowserError) as captured:
            observer.accept("https://www.zhipin.com/wapi/zpgeek/list", 200,
                            "application/json", b"{}")
        self.assertEqual(captured.exception.code, "CONTRACT_MISMATCH")
        with self.assertRaises(BrowserError) as captured:
            observer.accept("http://127.0.0.1:1/wapi/zpgeek/list", 401,
                            "application/json", b"{}")
        self.assertEqual(captured.exception.code, "AUTH_OR_ANTI_BOT_BLOCKED")

    def test_observer_ignores_unrelated_resources(self):
        observer = ResponseObserver()
        self.assertIsNone(observer.accept("http://127.0.0.1:1/static/app.js",
                                          200, "text/javascript", b"x"))


class DetailResponseParserTests(unittest.TestCase):
    def test_full_detail_maps_to_resume_contract(self):
        detail = BossDetailParser.parse(DETAIL_PAYLOAD)
        resume = CandidateDetailParser.parse(detail, job_id="job")
        self.assertEqual(resume.meta.encrypt_geek_id, "geek-syn-1")
        self.assertEqual(resume.meta.encrypt_job_id, "boss-job-syn")
        self.assertEqual(resume.meta.candidate_key, "job:geek-syn-1")
        self.assertEqual(resume.work_experience[0].company, "合成公司")
        self.assertEqual(resume.project_experience[0].name, "合成项目")
        self.assertEqual(resume.education[0].school, "合成大学")
        self.assertEqual(resume.data_completeness, "COMPLETE")

    def test_empty_detail_is_partial_not_complete(self):
        payload = {"zpData": {"geekDetailInfo": {
            "geekBaseInfo": {"encryptGeekId": "geek-empty", "geekName": "空详情",
                             "geekDegree": "", "geekWorkYear": ""},
            "geekWorkExpList": [], "geekProjExpList": [], "geekEduExpList": []}}}
        detail = BossDetailParser.parse(payload)
        resume = CandidateDetailParser.parse(detail, job_id="job")
        self.assertNotEqual(resume.data_completeness, "COMPLETE")
        self.assertEqual(resume.data_completeness, "PARTIAL")

    def test_missing_detail_root_is_contract_mismatch(self):
        with self.assertRaises(BrowserError) as captured:
            BossDetailParser.parse({"zpData": {}})
        self.assertEqual(captured.exception.code, "CONTRACT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
