import tempfile
import unittest
from pathlib import Path

from config.job_compiler import HRJobUploadInput, JobProfileCompiler
from engine.hybrid_engine import HybridScoringEngine
from tests.mock_models import DeterministicEmbedder, DeterministicLLMClient


class CompilerTests(unittest.TestCase):
    def test_compiler_generates_runnable_v2_config(self):
        compiler = JobProfileCompiler.__new__(JobProfileCompiler)
        compiler.model_name = "mock-compiler"
        compiler.client = DeterministicLLMClient(response={
            "min_degree": "本科", "max_expected_salary_k": 12.5,
            "exclude_position_keywords": ["客服"],
            "high_value_tags": [{"word": "LangGraph", "score": 14}],
            "medium_value_tags": [{"word": "Python", "score": 8}],
            "jd_core_requirement_chunks": ["开发智能体", "开发自动化脚本"],
            "positive_anchor_groups": {"agent": ["基于LangGraph实现工具调用"], "rpa": ["基于Playwright实现网页自动采集"]},
            "negative_anchors": ["使用AI写文案", "制作AI图片"],
        })
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "config.json"
            config = compiler.compile_and_save(HRJobUploadInput(job_title="AI开发", raw_jd_text="Agent或RPA", hr_custom_requirements="本科"), str(output))
            e = HybridScoringEngine(str(output), embedder=DeterministicEmbedder(), llm_client=DeterministicLLMClient())
            self.assertEqual(set(config["scoring_config"]["semantic_anchors"]["positive_anchor_groups"]), {"agent", "rpa"})
            self.assertIn("objective_job_fit", e.config["scoring_config"]["dimension_weights"])
            self.assertEqual(config["hard_filters"]["max_expected_salary_k"], 12.5)
