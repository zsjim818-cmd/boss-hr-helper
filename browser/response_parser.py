"""Passive BOSS-like response parsing (synthetic fixtures only).

This module never sends requests.  It parses a response that a normal page load
would emit, applies conservative shape checks and maps only fields with
independent synthetic evidence.  Real BOSS field semantics remain UNVERIFIED.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from browser.contracts import BrowserError, CandidateCard, CandidateDetail

MAX_BODY_BYTES = 2_000_000
LIST_PATH_HINTS = ("/wapi/zpgeek/", "/wapi/zpjob/")
DETAIL_PATH_HINTS = ("/geek/info", "/geek/detail")


class ResponseObserver:
    """Pure response classifier + parser; registration belongs to the adapter."""

    def __init__(self, *, allowed_hosts=("127.0.0.1", "localhost"), path_hints=LIST_PATH_HINTS):
        self.allowed_hosts = set(allowed_hosts)
        self.path_hints = tuple(path_hints)
        self.last_cards: list[CandidateCard] = []
        self.errors: list[dict] = []

    def accept(self, url: str, status: int, content_type: str, body: bytes):
        parsed = urlparse(url)
        if parsed.hostname not in self.allowed_hosts:
            raise BrowserError("CONTRACT_MISMATCH",
                               f"response host is not allow-listed: {parsed.hostname!r}",
                               recoverable=False)
        if not any(hint in parsed.path for hint in self.path_hints):
            return None  # unrelated resource; ignore passively
        if status in (401, 403):
            raise BrowserError("AUTH_OR_ANTI_BOT_BLOCKED",
                               f"platform rejected the passive response ({status})",
                               recoverable=False)
        if status != 200:
            raise BrowserError("TIMEOUT" if status >= 500 else "CONTRACT_MISMATCH",
                               f"unexpected response status {status}", recoverable=True)
        if "json" not in (content_type or "").lower():
            raise BrowserError("CONTRACT_MISMATCH",
                               "expected a JSON response", recoverable=True)
        if len(body) > MAX_BODY_BYTES:
            raise BrowserError("CONTRACT_MISMATCH", "response body is too large",
                               recoverable=False)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise BrowserError("CONTRACT_MISMATCH",
                               f"response is not valid JSON: {exc}",
                               recoverable=True) from exc
        if not isinstance(payload, dict):
            raise BrowserError("CONTRACT_MISMATCH", "response root is not an object",
                               recoverable=True)
        if any(hint in parsed.path for hint in DETAIL_PATH_HINTS):
            return BossDetailParser.parse(payload)
        return BossListParser.parse(payload)


class BossListParser:
    @staticmethod
    def extract(payload: dict) -> list[dict]:
        zp = payload.get("zpData")
        if not isinstance(zp, dict):
            raise BrowserError("CONTRACT_MISMATCH", "zpData is missing",
                               recoverable=True)
        items = zp.get("geekList")
        if items is None:
            items = zp.get("jobList")
        if not isinstance(items, list):
            raise BrowserError("CONTRACT_MISMATCH", "geekList is not an array",
                               recoverable=True)
        return items

    @classmethod
    def parse(cls, payload: dict) -> list[CandidateCard]:
        cards = []
        for item in cls.extract(payload):
            if not isinstance(item, dict):
                raise BrowserError("CONTRACT_MISMATCH",
                                   "list item is not an object", recoverable=True)
            card = item.get("geekCard") if isinstance(item.get("geekCard"), dict) else item
            identity = str(item.get("encryptGeekId") or card.get("encryptGeekId") or "").strip()
            if not identity:
                raise BrowserError("IDENTITY_MISMATCH",
                                   "list item has no encryptGeekId", recoverable=False)
            degree = str(card.get("geekDegree") or card.get("degree") or "")
            years = str(card.get("geekWorkYear") or card.get("workYear") or "")
            cards.append(CandidateCard(
                identity=identity,
                name=str(card.get("geekName") or card.get("name") or identity),
                position=str(card.get("jobTitle") or card.get("expectPosition") or ""),
                platform_job_id=str(item.get("encryptJobId")
                                    or card.get("encryptJobId") or ""),
                degree=degree, work_years=years,
                dom={"source": "passive_json"}))
        return cards


class BossDetailParser:
    @staticmethod
    def parse(payload: dict) -> CandidateDetail:
        zp = payload.get("zpData")
        info = zp.get("geekDetailInfo") if isinstance(zp, dict) else None
        if not isinstance(info, dict):
            raise BrowserError("CONTRACT_MISMATCH", "geekDetailInfo is missing",
                               recoverable=True)
        base = info.get("geekBaseInfo") if isinstance(info.get("geekBaseInfo"), dict) else {}
        identity = str(base.get("encryptGeekId") or info.get("encryptGeekId") or "").strip()
        if not identity:
            raise BrowserError("IDENTITY_MISMATCH", "detail has no encryptGeekId",
                               recoverable=False)
        expect = info.get("geekExpect") or info.get("anonymousGeekExpect") or {}
        if isinstance(expect, str):
            expect = {"position": expect}
        raw = {
            "name": str(base.get("geekName") or base.get("name") or ""),
            "degree": str(base.get("geekDegree") or ""),
            "work_years": str(base.get("geekWorkYear") or ""),
            "job_status": base.get("geekJobStatus"),
            "active_status": base.get("geekActiveStatus"),
            "expectation": expect if isinstance(expect, dict) else {},
            "skills": str(base.get("geekSkills") or ""),
            "work_experience": [],
            "project_experience": [],
            "education": [],
            "certifications": [],
            "advantage": "",
        }
        for item in info.get("geekWorkExpList") or []:
            if isinstance(item, dict):
                raw["work_experience"].append({
                    "company": str(item.get("company") or ""),
                    "position": str(item.get("position") or ""),
                    "responsibility": str(item.get("responsibility") or ""),
                    "start": str(item.get("startDate") or ""),
                    "end": str(item.get("endDate") or ""),
                })
        for item in info.get("geekProjExpList") or []:
            if isinstance(item, dict):
                raw["project_experience"].append({
                    "name": str(item.get("projectName") or ""),
                    "role": str(item.get("role") or ""),
                    "description": str(item.get("description") or ""),
                })
        for item in info.get("geekEduExpList") or []:
            if isinstance(item, dict):
                raw["education"].append({
                    "school": str(item.get("school") or ""),
                    "degree": str(item.get("degree") or ""),
                    "major": str(item.get("major") or ""),
                })
        # Empty dictionary of all keys is not COMPLETE evidence.
        if not raw["work_experience"] and not raw["project_experience"] \
                and not raw["education"]:
            raw["data_completeness"] = "PARTIAL"
        return CandidateDetail(identity=identity, name=raw["name"], job_id="",
                               platform_job_id=str(
                                   base.get("encryptJobId")
                                   or payload.get("encryptJobId") or ""),
                               raw=raw)
