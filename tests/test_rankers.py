from config.settings import DEFAULT_CONFIG_PATH
import copy
import json
from pathlib import Path
import unittest

import numpy as np

from engine.rule_matcher import DEFAULT_KEYWORD_CONFIG, DEFAULT_SOURCE_WEIGHTS, RuleMatcherMixin
from engine.semantic_ranker import DEFAULT_SEMANTIC_CONFIG, SemanticRankerMixin
from schemas import ResumeData
from tests.mock_data import BENCHMARK_CANDIDATES
from tests.mock_models import DeterministicEmbedder


class RankerHarness(RuleMatcherMixin, SemanticRankerMixin):
    def __init__(self, config=None, embedder=None):
        self.config = copy.deepcopy(config or json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))
        scoring = self.config["scoring_config"]
        scoring.setdefault("keyword_aggregation", copy.deepcopy(DEFAULT_KEYWORD_CONFIG))
        scoring.setdefault("evidence_source_weights", copy.deepcopy(DEFAULT_SOURCE_WEIGHTS))
        scoring.setdefault("semantic_scoring", copy.deepcopy(DEFAULT_SEMANTIC_CONFIG))
        anchors = scoring["semantic_anchors"]
        if "positive_anchor_groups" not in anchors:
            flat = anchors["positive_anchors"]
            anchors["positive_anchor_groups"] = {"agent": [flat[0], flat[1], flat[3]], "rpa": [flat[2]]}
        self.embedder = embedder or DeterministicEmbedder()
        self._refresh_anchor_embeddings()


def resume(**changes):
    data = copy.deepcopy(BENCHMARK_CANDIDATES[0])
    data.update(advantage="", skills="", work_experience=[], project_experience=[], education=[])
    data.update(changes)
    return ResumeData.model_validate(data)


class TableEmbedder:
    """Hand-authored unit vectors test the formula without learned semantics."""
    vectors = {
        "AGENT_ANCHOR": [1, 0, 0, 0], "RPA_ANCHOR": [0, 1, 0, 0], "NEG_ANCHOR": [0, 0, 1, 0],
        "STRONG_AGENT_EXPERIENCE": [.9, 0, 0, np.sqrt(.19)],
        "MODERATE_AGENT_EXPERIENCE": [.6, 0, 0, .8],
        "STRONG_RPA_EXPERIENCE": [0, .9, 0, np.sqrt(.19)],
        "NEGATIVE_CONTENT_EXPERIENCE": [0, 0, 1, 0],
        "UNRELATED_EXPERIENCE": [0, 0, 0, 1],
        "MIXED_AGENT_AND_NEGATIVE": [.65, 0, .75, np.sqrt(.015)],
    }

    def encode(self, texts, normalize_embeddings=True):
        return np.array([self.vectors[text] for text in texts], dtype=float)


def formula_engine():
    e = RankerHarness()
    # Table labels are mathematical vector fixtures, not natural-language claims.
    # The separate label-only test exercises the enabled production safeguard.
    e.config["scoring_config"]["evidence_quality"] = {"detect_label_only_practice": False}
    e.config["scoring_config"]["semantic_anchors"] = {
        "positive_anchor_groups": {"agent": ["AGENT_ANCHOR"], "rpa": ["RPA_ANCHOR"]},
        "negative_anchors": ["NEG_ANCHOR"],
    }
    e.embedder = TableEmbedder()
    e._refresh_anchor_embeddings()
    return e


class RuleMatcherTests(unittest.TestCase):
    def test_global_alias_and_source_deduplication(self):
        e = RankerHarness()
        once = resume(work_experience=[{"responsibility": "使用 LangGraph 和 Agent 实现工具调用"}])
        repeated = resume(
            work_experience=[{"responsibility": "使用 LangGraph 和 Agent 实现工具调用", "keywords": ["LangGraph", "智能体"]}],
            skills="LangGraph, Lang Graph, Agent, Agents, 智能体",
            advantage="掌握LangGraph、Agent与智能体",
        )
        self.assertEqual(e._run_tier1_rules(once)[:2], e._run_tier1_rules(repeated)[:2])
        _, _, tags, _ = e._run_tier1_rules(repeated)
        self.assertEqual(tags.count("Agent"), 1)
        self.assertNotIn("智能体", tags)

    def test_duplicate_rules_do_not_double_score(self):
        e = RankerHarness()
        r = resume(skills="Python")
        before = e._run_tier1_rules(r)
        e.config["scoring_config"]["keyword_rules"]["high_value_tags"].extend([{"word": "python", "score": 8}, {"word": "Python", "score": 3}])
        self.assertEqual(before[:3], e._run_tier1_rules(r)[:3])

    def test_auxiliary_labels_cannot_get_practice_weight(self):
        e = RankerHarness()
        labels = e._run_tier1_rules(resume(work_experience=[{"keywords": ["LangGraph", "Python"]}]))
        actual = e._run_tier1_rules(resume(work_experience=[{"responsibility": "基于LangGraph与Python实现节点编排服务"}]))
        self.assertLess(labels[0], actual[0])
        self.assertTrue(all("work_tags:" in line or line.startswith("category:") for line in labels[3]))

    def test_label_list_in_responsibility_stays_auxiliary(self):
        e = RankerHarness()
        labels = e._run_tier1_rules(resume(work_experience=[{"keywords": ["Python", "LangGraph", "RAG"]}]))
        mapped = e._run_tier1_rules(resume(work_experience=[{"responsibility": "技术栈：Python, LangGraph, RAG"}]))
        self.assertEqual(labels[:3], mapped[:3])
        _, _, _, details = e._run_tier2_semantic(resume(work_experience=[{"responsibility": "Python, LangGraph, RAG"}]))
        self.assertTrue(all(d.source == "work_tags" for d in details))
        self.assertTrue(all(d.source_weight == .15 for d in details))

    def test_category_budget_and_saturation_preserve_distinction(self):
        e = RankerHarness()
        cfg = e.config["scoring_config"]
        cfg["keyword_rules"] = {"high_value_tags": [{"word": f"tech{i}", "score": 12, "category": "development_practice"} for i in range(100)], "medium_value_tags": []}
        small = e._run_tier1_rules(resume(skills="tech0 tech1 tech2"))[1]
        large = e._run_tier1_rules(resume(skills=" ".join(f"tech{i}" for i in range(100))))[1]
        self.assertLess(small, large)
        self.assertLessEqual(large, 28)

    def test_original_high_samples_no_longer_both_45(self):
        e = RankerHarness()
        scores = [e._run_tier1_rules(ResumeData.model_validate(x))[1] for x in BENCHMARK_CANDIDATES[:2]]
        self.assertNotEqual(scores[0], scores[1])
        self.assertTrue(all(0 <= value <= 45 for value in scores))


class SemanticRankerTests(unittest.TestCase):
    def test_agent_and_rpa_routes_each_pass_independently(self):
        e = formula_engine()
        for text, route in [("STRONG_AGENT_EXPERIENCE", "agent"), ("STRONG_RPA_EXPERIENCE", "rpa")]:
            with self.subTest(route=route):
                score, penalty, metrics, details = e._run_tier2_semantic(resume(work_experience=[{"responsibility": text}]))
                self.assertEqual(score, 55)
                self.assertEqual(penalty, 0)
                self.assertEqual(metrics[f"route_{route}_score"], 55)
                other = "rpa" if route == "agent" else "agent"
                self.assertEqual(metrics[f"route_{other}_score"], 0)
                self.assertIn(route, details[0].selected_routes)

    def test_top_k_uses_more_than_global_maximum(self):
        e = formula_engine()
        r = resume(work_experience=[{"responsibility": "STRONG_AGENT_EXPERIENCE"}, {"responsibility": "MODERATE_AGENT_EXPERIENCE"}])
        score, _, metrics, details = e._run_tier2_semantic(r)
        self.assertLess(score, 55)
        self.assertGreater(score, 40)
        self.assertEqual(len([d for d in details if "agent" in d.selected_routes]), 2)

    def test_unrelated_negative_history_does_not_penalize_technical_project(self):
        e = formula_engine()
        base = resume(project_experience=[{"description": "STRONG_AGENT_EXPERIENCE"}])
        mixed = resume(project_experience=[{"description": "STRONG_AGENT_EXPERIENCE"}], work_experience=[{"responsibility": "NEGATIVE_CONTENT_EXPERIENCE"}])
        self.assertEqual(e._run_tier2_semantic(base)[:2], e._run_tier2_semantic(mixed)[:2])
        self.assertEqual(e._run_tier2_semantic(mixed)[1], 0)

    def test_penalty_is_same_block_and_bounded(self):
        e = formula_engine()
        score, penalty, _, details = e._run_tier2_semantic(resume(work_experience=[{"responsibility": "MIXED_AGENT_AND_NEGATIVE"}]))
        self.assertGreater(penalty, 0)
        self.assertLessEqual(penalty, 12)
        self.assertLess(score, (.65 - .42) / (.8 - .42) * 55)
        self.assertGreater(details[0].route_penalties["agent"], 0)

    def test_duplicate_and_unrelated_length_do_not_change_score(self):
        e = formula_engine()
        original = resume(work_experience=[{"responsibility": "STRONG_AGENT_EXPERIENCE"}])
        repeated = resume(work_experience=[{"responsibility": "STRONG_AGENT_EXPERIENCE\n" * 30}, {"responsibility": "UNRELATED_EXPERIENCE"}], skills="STRONG_AGENT_EXPERIENCE")
        self.assertEqual(e._run_tier2_semantic(original)[:2], e._run_tier2_semantic(repeated)[:2])

    def test_long_field_cannot_fill_all_top_k_slots(self):
        e = formula_engine()
        r = resume(work_experience=[{"responsibility": "STRONG_AGENT_EXPERIENCE\nMODERATE_AGENT_EXPERIENCE"}])
        details = e._run_tier2_semantic(r)[3]
        self.assertEqual(sum("agent" in d.selected_routes for d in details), 1)

    def test_description_and_achievement_share_one_experience_slot(self):
        e = formula_engine()
        r = resume(project_experience=[{"description": "STRONG_AGENT_EXPERIENCE", "achievement": "MODERATE_AGENT_EXPERIENCE"}])
        score, _, _, details = e._run_tier2_semantic(r)
        self.assertEqual(score, 55)
        self.assertEqual(sum("agent" in d.selected_routes for d in details), 1)

    def test_supporting_labels_do_not_dilute_practice(self):
        e = formula_engine()
        base = resume(project_experience=[{"description": "STRONG_AGENT_EXPERIENCE"}])
        auxiliary = resume(project_experience=[{"description": "STRONG_AGENT_EXPERIENCE"}], skills="MODERATE_AGENT_EXPERIENCE")
        self.assertEqual(e._run_tier2_semantic(base)[:2], e._run_tier2_semantic(auxiliary)[:2])

    def test_auxiliary_semantics_has_limited_weight_and_trace(self):
        e = formula_engine()
        score, _, _, details = e._run_tier2_semantic(resume(skills="STRONG_AGENT_EXPERIENCE"))
        self.assertEqual(score, 11)
        self.assertEqual(details[0].source, "skills")
        self.assertEqual(details[0].text, "STRONG_AGENT_EXPERIENCE")
        self.assertEqual(set(details[0].route_positive_similarities), {"agent", "rpa"})

    def test_semantic_config_changes_are_honored(self):
        e = formula_engine()
        e.config["scoring_config"]["semantic_scoring"]["similarity_max"] = 1
        result = e._run_tier2_semantic(resume(work_experience=[{"responsibility": "STRONG_AGENT_EXPERIENCE"}]))
        self.assertLess(result[0], 55)
        self.assertGreater(result[0], 40)


if __name__ == "__main__":
    unittest.main()
