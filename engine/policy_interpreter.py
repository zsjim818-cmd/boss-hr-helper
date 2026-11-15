"""Compile immutable, job-scoped policies; no scoring, model calls or writes."""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from threading import RLock
import unicodedata

from config.policy_validation import validate_publishable_policy
from schemas.job_policy import JobScoringPolicy
from schemas.prescreen import PrescreenExecutionProfile


INTERPRETER_VERSION = "policy-interpreter-v1"
DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[1] / "config" / "prescreen_profile.json"


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def keyword_key(value):
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def _contains_name(name, text):
    import re
    if all(ord(character) < 128 for character in name):
        return bool(re.search(r"(?<![A-Za-z0-9_+#])" + re.escape(name) + r"(?![A-Za-z0-9_+#])", text, re.I))
    return name.casefold() in text.casefold()


@dataclass(frozen=True)
class CompiledKeyword:
    canonical: str
    aliases: tuple[str, ...]
    weight: float
    group_ids: tuple[str, ...]
    declared_requirement_ids: tuple[str, ...]
    precise_requirement_ids: tuple[str, ...]
    dimension_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExecutablePolicy:
    org_id: str
    job_id: str
    policy_id: str
    policy_version: int
    policy_hash: str
    profile_hash: str
    policy_json: str
    profile_json: str
    root_ids: tuple[str, ...]
    keywords: tuple[CompiledKeyword, ...]
    diagnostics: tuple[str, ...]

    @property
    def policy(self):
        # Callers get independent models. Mutating one cannot poison the cache.
        return JobScoringPolicy.model_validate_json(self.policy_json)

    @property
    def profile(self):
        return PrescreenExecutionProfile.model_validate_json(self.profile_json)


@dataclass(frozen=True)
class BoundPolicy:
    plan: ExecutablePolicy
    candidate_id: str
    job_candidate_id: str


class PolicyInterpreter:
    def __init__(self, repository=None, *, profile=None, profile_path=None, max_cache_entries=128):
        if profile is not None and profile_path is not None:
            raise ValueError("Select profile object or path, not both")
        if type(max_cache_entries) is not int or max_cache_entries < 1:
            raise ValueError("Cache size must be positive")
        if profile is None:
            profile = PrescreenExecutionProfile.model_validate_json(
                Path(profile_path or DEFAULT_PROFILE_PATH).read_text(encoding="utf-8"))
        else:
            profile = PrescreenExecutionProfile.model_validate_json(
                profile.model_dump_json() if isinstance(profile, PrescreenExecutionProfile) else canonical_json(profile))
        self._profile_json = canonical_json(profile.model_dump(mode="json"))
        self.profile_hash = content_hash(profile.model_dump(mode="json"))
        self.repository = repository
        self.max_cache_entries = max_cache_entries
        self._cache = OrderedDict()
        self._lock = RLock()
        self.cache_hits = 0
        self.cache_misses = 0

    def load_for_candidate(self, context, job_id, job_candidate_id, batch_id=None):
        if self.repository is None:
            raise ValueError("A repository is required to load candidate-bound policies")
        candidate = self.repository.get_job_candidate(context, job_id, job_candidate_id)
        if batch_id:
            binding = self.repository.resolve_batch_policy(
                context, job_id, batch_id, job_candidate_id)
            policy_id = binding["policy_id"]
            expected_version = binding["policy_version"]
            candidate_id = binding["candidate_id"]
        else:
            policy_id = candidate["policy_id"]
            expected_version = candidate["policy_version"]
            candidate_id = candidate["candidate_id"]
        version = self.repository.get_policy_version(context, job_id, policy_id=policy_id)
        if (version["policy_version"] != expected_version
                or version["policy_schema_version"] != "job_scoring_policy_v1"):
            raise ValueError("Candidate-bound policy version/protocol is not executable by this interpreter")
        policy = json.loads(version["policy_json"])
        source = json.loads(version["source_snapshot_json"])
        plan = self.compile_policy(policy, org_id=context.org_id, job_id=job_id,
            policy_id=version["policy_id"], policy_version=version["policy_version"],
            policy_hash=version["policy_hash"], source_input=source)
        return BoundPolicy(plan, candidate_id, job_candidate_id)

    def compile_policy(self, policy, *, org_id, job_id, policy_id, policy_version,
                       policy_hash=None, source_input=None):
        if not all(isinstance(value, str) and value.strip() for value in (org_id, job_id, policy_id)):
            raise ValueError("Policy identity must include organization and job scope")
        if type(policy_version) is not int or policy_version < 1:
            raise ValueError("Policy version must be a positive integer")
        raw_snapshot = policy.model_dump(mode="json") if isinstance(policy, JobScoringPolicy) else policy
        actual_hash = content_hash(raw_snapshot)
        parsed = JobScoringPolicy.model_validate(policy)
        snapshot = parsed.model_dump(mode="json")
        if policy_hash is not None and actual_hash != policy_hash:
            raise ValueError("Immutable policy hash mismatch")
        key = (org_id, job_id, policy_id, policy_version, self.profile_hash)
        with self._lock:
            existing = self._cache.get(key)
            if existing is not None:
                if existing.policy_hash != actual_hash:
                    raise ValueError("An immutable policy identity was reused with different content")
                # Source snapshots are checked even for a cache hit.
                if source_input is not None:
                    if (source_input.get("jd_text") != parsed.sources.jd_text
                            or source_input.get("hr_requirements") != parsed.sources.hr_requirements
                            or source_input.get("job_title") != parsed.job.title):
                        raise ValueError("Frozen source snapshot mismatch")
                self.cache_hits += 1
                self._cache.move_to_end(key)
                return existing
        validate_publishable_policy(parsed, source_input)
        requirements = {item.requirement_id: item for item in parsed.requirements}
        all_nodes = {*requirements, *(item.group_id for item in parsed.requirement_groups)}
        children = {member for group in parsed.requirement_groups for member in group.members}
        roots = tuple(sorted(all_nodes - children))
        rows = []
        for group in parsed.keyword_groups:
            for word in group.keywords:
                rows.append({"name": word.canonical, "aliases": list(word.aliases), "weight": word.weight,
                             "groups": {group.group_id}, "refs": set(group.requirement_ids)})
        # Alias-equivalent canonical declarations share one signal and max weight.
        parent = list(range(len(rows)))
        def find(index):
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index
        owner = {}
        for index, row in enumerate(rows):
            for name in (row["name"], *row["aliases"]):
                token = keyword_key(name)
                if token in owner:
                    parent[find(index)] = find(owner[token])
                owner[token] = index
        clusters = {}
        for index, row in enumerate(rows):
            clusters.setdefault(find(index), []).append(row)
        compiled, diagnostics = [], []
        for items in clusters.values():
            first = items[0]
            variants = list(dict.fromkeys(name for item in items for name in (item["name"], *item["aliases"])))
            refs = set().union(*(item["refs"] for item in items))
            matches = [key for key in sorted(refs) if any(_contains_name(name, requirements[key].label) for name in variants)]
            if not matches:
                matches = [key for key in sorted(refs) if any(_contains_name(name, requirements[key].source_text) for name in variants)]
            precise = tuple(matches) if len(matches) == 1 else ()
            if not precise:
                diagnostics.append(f"GROUP_SCOPED_KEYWORD:{first['name']}:declared={','.join(sorted(refs))}; no unique atomic proof")
            dimensions = tuple(d.dimension_id for d in parsed.scoring_dimensions if refs.intersection(d.requirement_ids))
            compiled.append(CompiledKeyword(first["name"], tuple(variants[1:]), max(item["weight"] for item in items),
                tuple(sorted(set().union(*(item["groups"] for item in items)))), tuple(sorted(refs)), precise, dimensions))
        plan = ExecutablePolicy(org_id, job_id, policy_id, policy_version, actual_hash, self.profile_hash,
            canonical_json(snapshot), self._profile_json, roots, tuple(sorted(compiled, key=lambda item: keyword_key(item.canonical))),
            tuple(diagnostics))
        with self._lock:
            current = self._cache.get(key)
            if current is not None and current.policy_hash != actual_hash:
                raise ValueError("Concurrent immutable policy identity conflict")
            self._cache[key] = plan
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_cache_entries:
                self._cache.popitem(last=False)
            self.cache_misses += 1
        return plan

    @property
    def cache_size(self):
        with self._lock:
            return len(self._cache)
