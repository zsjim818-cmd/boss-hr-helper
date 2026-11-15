"""Project paths and runtime environment, independent of the caller's cwd."""
from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"
REPORTS_DIR = PROJECT_ROOT / "reports"

load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class RuntimeSettings:
    embedding_model_path: str
    llm_base_url: str
    llm_api_key: str = field(repr=False)
    llm_model_name: str


def load_runtime_settings():
    return RuntimeSettings(
        embedding_model_path=os.getenv("EMBEDDING_MODEL_PATH", r"D:\model\bge-large-zh-v1.5"),
        llm_base_url=os.getenv("LLM_BASE_URL", "http://localhost:4000/v1"),
        llm_api_key=os.getenv("LLM_API_KEY", "sk-empty"),
        llm_model_name=os.getenv("LLM_MODEL_NAME", "gpt-4o-mini"),
    )
