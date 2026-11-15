import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storage.scoring_cache import ScoringCache, stable_hash


class StableHashTests(unittest.TestCase):
    def test_dictionary_order_does_not_change_hash(self):
        self.assertEqual(
            stable_hash({"candidate": "候选人", "versions": {"a": 1, "b": 2}}),
            stable_hash({"versions": {"b": 2, "a": 1}, "candidate": "候选人"}),
        )

    def test_each_key_component_changes_identity(self):
        key = {
            "candidate_key": "candidate-1",
            "resume_content_hash": "resume-v1",
            "job_config_version": "job-v1",
            "embedding_model_version": "embedding-v1",
            "llm_model_version": "llm-v1",
            "prompt_version": "prompt-v1",
            "scoring_mode": "deep_mode",
            "engine_version": "engine-v2",
        }
        initial = stable_hash(key)
        for component in key:
            with self.subTest(component=component):
                changed = dict(key)
                changed[component] += "-changed"
                self.assertNotEqual(initial, stable_hash(changed))

    def test_invalid_json_identity_is_explicit(self):
        with self.assertRaises(ValueError):
            stable_hash({"score": float("nan")})
        with self.assertRaises(TypeError):
            stable_hash({"invalid": object()})


class ScoringCacheTests(unittest.TestCase):
    def test_memory_miss_and_deep_copy(self):
        cache = ScoringCache()
        self.assertIsNone(cache.get("missing"))
        value = {"scores": {"a": 90}, "evidence": ["原文"]}
        cache.put("one", value)
        value["scores"]["a"] = 0
        result = cache.get("one")
        result["evidence"].append("mutation")
        self.assertEqual(cache.get("one"), {"scores": {"a": 90}, "evidence": ["原文"]})

    def test_disk_round_trip_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = ScoringCache(temporary)
            cache.put("one", {"score": 72, "reason": "人工核验"})
            cache.put("one", {"score": 80, "reason": "人工核验"})
            fresh = ScoringCache(temporary)
            self.assertEqual(fresh.get("one"), {"score": 80, "reason": "人工核验"})
            self.assertEqual(len(list(Path(temporary).iterdir())), 1)
            self.assertFalse(cache.issues)
            self.assertFalse(fresh.issues)

    def test_path_like_key_cannot_escape_cache_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = ScoringCache(temporary)
            cache.put("../../outside", {"score": 1})
            files = list(Path(temporary).iterdir())
            self.assertEqual([item.name for item in files], [stable_hash("../../outside") + ".json"])
            self.assertEqual(cache.get("../../outside"), {"score": 1})

    def test_corrupted_disk_is_diagnosable_miss(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / (stable_hash("one") + ".json")
            path.write_text("{broken", encoding="utf-8")
            cache = ScoringCache(temporary)
            self.assertIsNone(cache.get("one"))
            self.assertTrue(cache.issues[0].startswith("cache_read_failed:"))

    def test_mismatched_or_invalid_envelope_is_miss(self):
        envelopes = [
            [],
            {"format_version": 2, "key_hash": stable_hash("one"), "value": {}},
            {"format_version": 1, "key_hash": "other", "value": {}},
            {"format_version": 1, "key_hash": stable_hash("one"), "value": []},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / (stable_hash("one") + ".json")
            for envelope in envelopes:
                with self.subTest(envelope=envelope):
                    path.write_text(json.dumps(envelope), encoding="utf-8")
                    cache = ScoringCache(temporary)
                    self.assertIsNone(cache.get("one"))
                    self.assertTrue(cache.issues)

    def test_write_failure_retains_memory_and_cleans_temp_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = ScoringCache(temporary)
            with patch("storage.scoring_cache.os.replace", side_effect=PermissionError("locked")):
                cache.put("one", {"score": 90})
            self.assertEqual(cache.get("one"), {"score": 90})
            self.assertTrue(cache.issues[0].startswith("cache_write_failed:"))
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_unavailable_directory_falls_back_to_memory(self):
        with tempfile.TemporaryDirectory() as temporary:
            file_path = Path(temporary) / "regular-file"
            file_path.write_text("occupied", encoding="utf-8")
            cache = ScoringCache(file_path)
            cache.put("one", {"score": 90})
            self.assertEqual(cache.get("one"), {"score": 90})
            self.assertTrue(cache.issues[0].startswith("cache_directory_unavailable:"))

    def test_invalid_payload_does_not_replace_cached_value(self):
        cache = ScoringCache()
        cache.put("one", {"score": 90})
        with self.assertRaises(ValueError):
            cache.put("one", {"score": float("inf")})
        self.assertEqual(cache.get("one"), {"score": 90})


if __name__ == "__main__":
    unittest.main()
