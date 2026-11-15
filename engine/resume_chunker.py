"""Stable, traceable resume chunks for Stage 5 semantic matching."""
from __future__ import annotations

import hashlib
import re
from typing import Iterable

from schemas.semantic import ResumeChunk, ResumeChunkerProfile


_SENTENCE = re.compile(r"[^\n。！？;；]+(?:[\n。！？;；]+|$)")
_PRACTICE_ACTION = re.compile(
    r"负责|实现|开发|设计|构建|搭建|优化|交付|完成|主导|参与|处理|维护|测试|部署|"
    r"推进|谈判|签约|回款|访谈|管理|跟进|协调|制定|分析|解决|研究|运营|开拓|复盘|落地",
    re.IGNORECASE,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _segments(text: str, *, sentence_split: bool) -> Iterable[tuple[int, int]]:
    if not sentence_split:
        yield 0, len(text)
        return
    found = False
    for match in _SENTENCE.finditer(text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start < end:
            found = True
            yield start, end
    if not found and text:
        yield 0, len(text)


def _quality(source_type: str, text: str) -> str:
    if source_type in {"WORK_TAGS", "SKILLS", "CERTIFICATION"}:
        return "AUXILIARY"
    if source_type == "ADVANTAGE":
        return "SELF_DESCRIPTION"
    if source_type == "THESIS_DESCRIPTION":
        return "ACADEMIC_PRACTICE" if _PRACTICE_ACTION.search(text) else "AUXILIARY"
    if source_type == "PROJECT_ACHIEVEMENT":
        return "DELIVERABLE"
    return "PRACTICE" if _PRACTICE_ACTION.search(text) else "AUXILIARY"


def chunk_resume(raw_resume: dict, *, org_id: str, candidate_id: str, resume_id: str,
                 resume_content_hash: str, profile: ResumeChunkerProfile) -> list[ResumeChunk]:
    if not isinstance(raw_resume, dict):
        raise TypeError("Resume chunking requires the immutable raw snapshot object")
    fields = []

    def add(path, source_type, experience_key, value, *, label=False, force_quality=None):
        if isinstance(value, str) and value.strip():
            fields.append((path, source_type, experience_key, value, label, force_quality))

    for index, work in enumerate(raw_resume.get("work_experience", [])):
        if not isinstance(work, dict):
            continue
        key = f"work_experience[{index}]"
        add(f"{key}.responsibility", "WORK_RESPONSIBILITY", key, work.get("responsibility"))
        add(f"{key}.performance", "WORK_RESPONSIBILITY", key, work.get("performance"),
            force_quality="DELIVERABLE")
        for label_index, label in enumerate(work.get("keywords", [])):
            add(f"{key}.keywords[{label_index}]", "WORK_TAGS", key, label, label=True)

    for index, project in enumerate(raw_resume.get("project_experience", [])):
        if not isinstance(project, dict):
            continue
        key = f"project_experience[{index}]"
        add(f"{key}.description", "PROJECT_DESCRIPTION", key, project.get("description"))
        add(f"{key}.achievement", "PROJECT_ACHIEVEMENT", key, project.get("achievement"),
            force_quality="DELIVERABLE")

    for index, education in enumerate(raw_resume.get("education", [])):
        if isinstance(education, dict):
            key = f"education[{index}]"
            add(f"{key}.thesis_description", "THESIS_DESCRIPTION", key,
                education.get("thesis_description"))

    skills = raw_resume.get("skills")
    if isinstance(skills, list):
        for index, value in enumerate(skills):
            add(f"skills[{index}]", "SKILLS", "skills", value, label=True)
    else:
        add("skills", "SKILLS", "skills", skills, label=True)
    for index, value in enumerate(raw_resume.get("certifications", [])):
        add(f"certifications[{index}]", "CERTIFICATION", "certifications", value, label=True)
    add("advantage", "ADVANTAGE", "advantage", raw_resume.get("advantage"))

    chunks = []
    step = profile.max_characters - profile.overlap_characters
    for path, source_type, experience_key, field_text, label, forced in fields:
        minimum = profile.min_label_characters if label else profile.min_characters
        sentence_split = profile.split_strategy == "FIELD_THEN_SENTENCE" and not label
        split_index = 0
        for segment_start, segment_end in _segments(field_text, sentence_split=sentence_split):
            cursor = segment_start
            long_segment = segment_end - segment_start > profile.max_characters
            while cursor < segment_end:
                end = min(segment_end, cursor + profile.max_characters)
                quote = field_text[cursor:end]
                if len(quote) >= minimum:
                    text_digest = _hash(quote)
                    identity = "|".join((resume_content_hash, profile.chunker_version, path,
                                         str(cursor), str(end), text_digest))
                    chunks.append(ResumeChunk(
                        chunk_id="chunk_" + _hash(identity)[:32],
                        org_id=org_id,
                        candidate_id=candidate_id,
                        resume_id=resume_id,
                        resume_content_hash=resume_content_hash,
                        source_path=path,
                        source_type=source_type,
                        experience_key=experience_key,
                        start=cursor,
                        end=end,
                        text=quote,
                        text_hash=text_digest,
                        chunker_version=profile.chunker_version,
                        evidence_quality_type=forced or _quality(source_type, quote),
                        split_index=split_index,
                        truncated=long_segment,
                    ))
                    split_index += 1
                if end >= segment_end:
                    break
                cursor += step
    return sorted(chunks, key=lambda item: (item.source_path, item.start, item.end, item.chunk_id))


def chunks_for_storage(chunks: list[ResumeChunk]) -> list[dict]:
    return [{
        "chunk_id": item.chunk_id,
        "source_path": item.source_path,
        "source_type": item.source_type,
        "start_offset": item.start,
        "end_offset": item.end,
        "experience_key": item.experience_key,
        "text": item.text,
        "evidence_quality_type": item.evidence_quality_type,
        "split_index": item.split_index,
        "truncated": item.truncated,
    } for item in chunks]


def resolve_chunk_source(raw_resume: dict, path: str):
    """Resolve the finite identifier/list-index path grammar; never evaluate it."""
    value = raw_resume
    for part in path.split("."):
        match = re.fullmatch(r"([A-Za-z_][A-Za-z_0-9]*)(?:\[(\d+)\])?", part)
        if match is None or not isinstance(value, dict) or match[1] not in value:
            raise ValueError("Chunk source path is absent from the immutable resume snapshot")
        value = value[match[1]]
        if match[2] is not None:
            index = int(match[2])
            if not isinstance(value, list) or index >= len(value):
                raise ValueError("Chunk source list index is outside the immutable resume snapshot")
            value = value[index]
    return value


def validate_chunk_sources(raw_resume: dict, chunks: list[ResumeChunk]):
    for item in chunks:
        source = resolve_chunk_source(raw_resume, item.source_path)
        if not isinstance(source, str) or source[item.start:item.end] != item.text:
            raise ValueError("Chunk text and offsets do not resolve to the immutable resume snapshot")
