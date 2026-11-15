"""Shared data contracts and collector-input validation helpers."""
from typing import Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class DataIssue(BaseModel):
    path: str
    kind: str
    message: str


class Evidence(BaseModel):
    stage: str
    source: str
    source_path: str
    snippet: str
    evaluation: str
    confidence: Optional[float] = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    metrics: Dict[str, float] = Field(default_factory=dict)


class InputDataModel(BaseModel):
    """Collector input preserves unexpected values; known fields stay typed."""
    model_config = ConfigDict(populate_by_name=True, extra="allow", strict=True)


def collect_unknown_fields(model, prefix=""):
    """Record unexpected fields in known nested models without losing raw values."""
    issues = []
    if isinstance(model, BaseModel):
        for name in (model.model_extra or {}):
            path = f"{prefix}.{name}" if prefix else name
            issues.append(DataIssue(path=path, kind="UNKNOWN_FIELD", message="未映射字段已原样保留"))
        for name, field in type(model).model_fields.items():
            if name == "data_issues":
                continue
            path = f"{prefix}.{field.alias or name}" if prefix else field.alias or name
            issues.extend(collect_unknown_fields(getattr(model, name), path))
    elif isinstance(model, list):
        for index, value in enumerate(model):
            issues.extend(collect_unknown_fields(value, f"{prefix}[{index}]"))
    return issues


def deduplicate_issues(issues):
    unique = {}
    for issue in issues:
        unique.setdefault((issue.path, issue.kind), issue)
    return list(unique.values())
