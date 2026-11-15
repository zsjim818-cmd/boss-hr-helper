"""Authorized, append-only persistence for Stage 5 derived embeddings.

Model inference intentionally does not live here.  Callers calculate vectors
before opening these short SQLite transactions; this module validates and
seals the resulting float32 payloads and their source bindings.
"""
from __future__ import annotations

import hashlib
import json
import math
import struct

from .base import BaseRepository, check_snapshot, digest, json_dump, uid


VECTOR_VALIDATOR_VERSION = "float32-vector-v1"


def text_hash(text: str) -> str:
    if not isinstance(text, str) or not text:
        raise ValueError("Encoded text must be a non-empty string")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack_float32(vector, dimension: int) -> tuple[bytes, float]:
    """Return canonical little-endian float32 bytes and their L2 norm."""
    if type(dimension) is not int or dimension <= 0:
        raise ValueError("Embedding dimension must be a positive integer")
    if isinstance(vector, (bytes, bytearray, memoryview)):
        payload = bytes(vector)
        if len(payload) != dimension * 4:
            raise ValueError("float32 vector byte length does not match its dimension")
        values = struct.unpack(f"<{dimension}f", payload)
    else:
        try:
            raw_values = list(vector)
        except TypeError as exc:
            raise ValueError("Embedding vector must be an iterable of real numbers") from exc
        if len(raw_values) != dimension:
            raise ValueError("Embedding vector dimension mismatch")
        if any(isinstance(value, bool) for value in raw_values):
            raise ValueError("Boolean values are not valid embedding coordinates")
        try:
            payload = struct.pack(f"<{dimension}f", *(float(value) for value in raw_values))
        except (TypeError, ValueError, OverflowError, struct.error) as exc:
            raise ValueError("Embedding coordinates must be finite float32 values") from exc
        values = struct.unpack(f"<{dimension}f", payload)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Embedding vector contains NaN or infinity")
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or norm <= 0.0:
        raise ValueError("Embedding vector must have a finite non-zero norm")
    return payload, norm


def unpack_float32(payload, dimension: int) -> tuple[float, ...]:
    blob, _ = pack_float32(payload, dimension)
    return struct.unpack(f"<{dimension}f", blob)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_int(value, label, *, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


class EmbeddingRepository(BaseRepository):
    """Repository mixin for immutable profiles, vectors and source links."""

    _PROFILE_FIELDS = (
        "model_identifier", "model_revision", "revision_status", "tokenizer_revision",
        "tokenizer_identifier", "pooling_strategy", "text_normalization_version",
        "input_prefix", "max_length", "truncation_strategy", "l2_normalize",
        "embedding_dimension", "dtype", "byte_order",
    )

    @classmethod
    def _validate_profile_payload(cls, profile):
        check_snapshot(profile)
        missing = [field for field in cls._PROFILE_FIELDS if field not in profile]
        if missing:
            raise ValueError("Embedding profile is missing: " + ", ".join(missing))
        for field in ("model_identifier", "tokenizer_identifier", "pooling_strategy",
                      "text_normalization_version"):
            _require_text(profile[field], field)
        if profile["revision_status"] not in ("PINNED", "UNPINNED"):
            raise ValueError("revision_status must be PINNED or UNPINNED")
        if profile["revision_status"] == "PINNED":
            _require_text(profile["model_revision"], "model_revision")
            _require_text(profile["tokenizer_revision"], "tokenizer_revision")
        elif profile["model_revision"] is not None and not isinstance(profile["model_revision"], str):
            raise ValueError("model_revision must be a string or null")
        elif profile["tokenizer_revision"] is not None and not isinstance(profile["tokenizer_revision"], str):
            raise ValueError("tokenizer_revision must be a string or null")
        if not isinstance(profile["input_prefix"], str):
            raise ValueError("input_prefix must be a string")
        _require_int(profile["max_length"], "max_length")
        _require_int(profile["embedding_dimension"], "embedding_dimension")
        if profile["truncation_strategy"] not in ("HEAD", "TAIL", "LONGEST_FIRST"):
            raise ValueError("truncation_strategy must be HEAD, TAIL or LONGEST_FIRST")
        if type(profile["l2_normalize"]) is not bool:
            raise ValueError("l2_normalize must be a boolean")
        if profile["dtype"] != "float32" or profile["byte_order"] != "little":
            raise ValueError("Stage 5 storage supports little-endian float32 only")

    def create_embedding_profile(self, context, job_id, profile, *, embedding_profile_id=None):
        """Create or return an identical organization-scoped encoding profile."""
        self._validate_profile_payload(profile)
        fingerprint = digest(profile)
        identifier = embedding_profile_id or uid()
        with self.tx(context, job_id, "evaluate") as connection:
            existing_hash = connection.execute(
                "SELECT * FROM embedding_profiles WHERE org_id=? AND profile_hash=?",
                (context.org_id, fingerprint)).fetchone()
            if existing_hash:
                row = dict(existing_hash)
                if row["profile_json"] != json_dump(profile):
                    raise ValueError("Embedding profile hash collision")
                if embedding_profile_id is not None and row["embedding_profile_id"] != identifier:
                    raise ValueError("The same immutable profile already has another identifier")
                return row
            existing_id = connection.execute(
                "SELECT 1 FROM embedding_profiles WHERE org_id=? AND embedding_profile_id=?",
                (context.org_id, identifier)).fetchone()
            if existing_id:
                raise ValueError("Embedding profile identifier already refers to different content")
            connection.execute("""INSERT INTO embedding_profiles
                (org_id,embedding_profile_id,profile_json,profile_hash,model_name,model_revision,
                 revision_state,tokenizer_name,tokenizer_revision,pooling,normalizer_version,input_prefix,max_length,
                  truncation,l2_normalize,dimension,dtype,byte_order,created_by)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (context.org_id, identifier, json_dump(profile), fingerprint, profile["model_identifier"],
                  profile["model_revision"], profile["revision_status"], profile["tokenizer_identifier"],
                  profile["tokenizer_revision"], profile["pooling_strategy"],
                  profile["text_normalization_version"], profile["input_prefix"],
                  profile["max_length"], profile["truncation_strategy"], int(profile["l2_normalize"]),
                  profile["embedding_dimension"], profile["dtype"], profile["byte_order"], context.user_id))
            self.audit(connection, context, "EMBEDDING_PROFILE_CREATED", identifier,
                       {"profile_hash": fingerprint, "revision_state": profile["revision_status"]})
            return self.one(connection,
                "SELECT * FROM embedding_profiles WHERE org_id=? AND embedding_profile_id=?",
                (context.org_id, identifier))

    def get_embedding_profile(self, context, job_id, embedding_profile_id):
        with self.read(context, job_id, "evaluate") as connection:
            return self.one(connection,
                "SELECT * FROM embedding_profiles WHERE org_id=? AND embedding_profile_id=?",
                (context.org_id, embedding_profile_id))

    @staticmethod
    def _profile(connection, org_id, embedding_profile_id):
        return BaseRepository.one(connection,
            "SELECT * FROM embedding_profiles WHERE org_id=? AND embedding_profile_id=?",
            (org_id, embedding_profile_id))

    @staticmethod
    def _validate_asset_row(row, profile, encoded_hash=None):
        if encoded_hash is not None and row["encoded_text_hash"] != encoded_hash:
            raise ValueError("Embedding asset is linked to another encoded text")
        if (row["embedding_profile_id"] != profile["embedding_profile_id"]
                or row["dimension"] != profile["dimension"]
                or row["dtype"] != profile["dtype"]
                or row["byte_order"] != profile["byte_order"]):
            raise ValueError("Embedding asset metadata conflicts with its profile")
        payload, norm = pack_float32(row["vector_blob"], row["dimension"])
        if _sha256_bytes(payload) != row["vector_hash"]:
            raise ValueError("Embedding asset vector hash mismatch")
        if not math.isclose(norm, row["vector_norm"], rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError("Embedding asset stored norm mismatch")
        if profile["l2_normalize"] and not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
            raise ValueError("Embedding profile requires an L2-normalized vector")
        return row

    def _store_or_reuse_asset(self, connection, context, embedding_profile_id,
                              encoded_text, vector, *, asset_id=None,
                              validator_version=VECTOR_VALIDATOR_VERSION):
        profile = self._profile(connection, context.org_id, embedding_profile_id)
        encoded_hash = text_hash(encoded_text)
        payload, norm = pack_float32(vector, profile["dimension"])
        if profile["l2_normalize"] and not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
            raise ValueError("Embedding profile requires an L2-normalized vector")
        vector_hash = _sha256_bytes(payload)
        existing = connection.execute("""SELECT * FROM embedding_assets
            WHERE org_id=? AND embedding_profile_id=? AND encoded_text_hash=?""",
            (context.org_id, embedding_profile_id, encoded_hash)).fetchone()
        if existing:
            row = self._validate_asset_row(dict(existing), profile, encoded_hash)
            if row["vector_hash"] != vector_hash or row["vector_blob"] != payload:
                raise ValueError("The physical embedding cache key has conflicting vector content")
            if asset_id is not None and row["asset_id"] != asset_id:
                raise ValueError("The physical vector already has another immutable asset identifier")
            return row, True
        identifier = asset_id or uid()
        if connection.execute("SELECT 1 FROM embedding_assets WHERE org_id=? AND asset_id=?",
                              (context.org_id, identifier)).fetchone():
            raise ValueError("Embedding asset identifier already refers to different content")
        _require_text(validator_version, "validator_version")
        connection.execute("""INSERT INTO embedding_assets
            (org_id,asset_id,embedding_profile_id,encoded_text_hash,vector_blob,vector_hash,
             dimension,dtype,byte_order,vector_norm,validator_version,created_by)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (context.org_id, identifier, embedding_profile_id, encoded_hash, payload, vector_hash,
             profile["dimension"], profile["dtype"], profile["byte_order"], norm,
             validator_version, context.user_id))
        return self.one(connection, "SELECT * FROM embedding_assets WHERE org_id=? AND asset_id=?",
                        (context.org_id, identifier)), False

    def store_or_reuse_embedding_asset(self, context, job_id, embedding_profile_id,
                                       encoded_text, vector, *, asset_id=None,
                                       validator_version=VECTOR_VALIDATOR_VERSION):
        with self.tx(context, job_id, "evaluate") as connection:
            row, reused = self._store_or_reuse_asset(
                connection, context, embedding_profile_id, encoded_text, vector,
                asset_id=asset_id, validator_version=validator_version)
            if not reused:
                self.audit(connection, context, "EMBEDDING_ASSET_CREATED", row["asset_id"],
                           {"job_id": job_id, "embedding_profile_id": embedding_profile_id,
                            "encoded_text_hash": row["encoded_text_hash"]})
            return {"asset": row, "reused": reused}

    def get_embedding_asset(self, context, job_id, asset_id):
        with self.read(context, job_id, "evaluate") as connection:
            row = self.one(connection, "SELECT * FROM embedding_assets WHERE org_id=? AND asset_id=?",
                           (context.org_id, asset_id))
            profile = self._profile(connection, context.org_id, row["embedding_profile_id"])
            return self._validate_asset_row(row, profile)

    def find_embedding_assets(self, context, job_id, embedding_profile_id, encoded_texts):
        """Return validated physical assets keyed by exact encoded-text SHA256."""
        texts = list(encoded_texts)
        if any(not isinstance(item, str) or not item for item in texts):
            raise ValueError("Encoded texts must be non-empty strings")
        hashes = {text_hash(item) for item in texts}
        with self.read(context, job_id, "evaluate") as connection:
            profile = self._profile(connection, context.org_id, embedding_profile_id)
            found = {}
            for encoded_hash in sorted(hashes):
                row = connection.execute("""SELECT * FROM embedding_assets
                    WHERE org_id=? AND embedding_profile_id=? AND encoded_text_hash=?""",
                    (context.org_id, embedding_profile_id, encoded_hash)).fetchone()
                if row:
                    found[encoded_hash] = self._validate_asset_row(dict(row), profile, encoded_hash)
            return found

    @staticmethod
    def _confirmed_policy(connection, org_id, job_id, policy_id, policy_version):
        return BaseRepository.one(connection, """SELECT v.* FROM job_policy_versions v
            JOIN policy_version_compilations c
              ON c.org_id=v.org_id AND c.job_id=v.job_id AND c.policy_id=v.policy_id
            WHERE v.org_id=? AND v.job_id=? AND v.policy_id=? AND v.policy_version=?
              AND v.policy_schema_version='job_scoring_policy_v1'""",
            (org_id, job_id, policy_id, policy_version))

    def persist_policy_embeddings(self, context, job_id, policy_id, policy_version,
                                  embedding_profile_id, anchors, *,
                                  builder_version="policy-embedding-builder-v1"):
        """Atomically project all confirmed anchors, links and a READY manifest."""
        _require_text(builder_version, "builder_version")
        anchors = list(anchors)
        with self.tx(context, job_id, "evaluate") as connection:
            policy_row = self._confirmed_policy(connection, context.org_id, job_id,
                                                policy_id, policy_version)
            profile = self._profile(connection, context.org_id, embedding_profile_id)
            policy = json.loads(policy_row["policy_json"])
            expected = {item["anchor_id"]: item for item in policy.get("semantic_anchors", [])}
            supplied = {item.get("anchor_id"): item for item in anchors}
            if None in supplied or len(supplied) != len(anchors) or set(supplied) != set(expected):
                raise ValueError("Policy embedding input must contain every confirmed anchor exactly once")
            manifest_entries = []
            for anchor_id in sorted(expected):
                source = expected[anchor_id]
                item = supplied[anchor_id]
                if any(item.get(key) != source[key]
                       for key in ("requirement_ids", "polarity", "text")):
                    raise ValueError("Policy anchor input does not match the confirmed policy")
                anchor_text_hash = text_hash(source["text"])
                requirement_json = json_dump(source["requirement_ids"])
                existing_anchor = connection.execute("""SELECT * FROM policy_anchors
                    WHERE org_id=? AND job_id=? AND policy_id=? AND policy_version=? AND anchor_id=?""",
                    (context.org_id, job_id, policy_id, policy_version, anchor_id)).fetchone()
                if existing_anchor:
                    row = dict(existing_anchor)
                    if (row["requirement_ids_json"] != requirement_json or row["polarity"] != source["polarity"]
                            or row["anchor_text"] != source["text"] or row["anchor_text_hash"] != anchor_text_hash):
                        raise ValueError("Stored policy anchor conflicts with the immutable policy")
                else:
                    connection.execute("""INSERT INTO policy_anchors
                        (org_id,job_id,policy_id,policy_version,anchor_id,requirement_ids_json,
                         polarity,anchor_text,anchor_text_hash,created_by)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (context.org_id, job_id, policy_id, policy_version, anchor_id, requirement_json,
                         source["polarity"], source["text"], anchor_text_hash, context.user_id))
                encoded_text = _require_text(item.get("encoded_text"), "encoded_text")
                asset, _ = self._store_or_reuse_asset(
                    connection, context, embedding_profile_id, encoded_text, item["vector"])
                existing_link = connection.execute("""SELECT * FROM policy_embeddings
                    WHERE org_id=? AND job_id=? AND policy_id=? AND policy_version=?
                      AND anchor_id=? AND embedding_profile_id=?""",
                    (context.org_id, job_id, policy_id, policy_version, anchor_id,
                     embedding_profile_id)).fetchone()
                if existing_link:
                    link = dict(existing_link)
                    if (link["anchor_text_hash"], link["encoded_text_hash"], link["asset_id"]) != (
                            anchor_text_hash, asset["encoded_text_hash"], asset["asset_id"]):
                        raise ValueError("Stored policy embedding link conflicts with supplied content")
                else:
                    connection.execute("""INSERT INTO policy_embeddings
                        (org_id,job_id,policy_id,policy_version,anchor_id,embedding_profile_id,
                         anchor_text_hash,encoded_text_hash,asset_id,created_by)
                        VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (context.org_id, job_id, policy_id, policy_version, anchor_id,
                         embedding_profile_id, anchor_text_hash, asset["encoded_text_hash"],
                         asset["asset_id"], context.user_id))
                manifest_entries.append({"anchor_id": anchor_id, "anchor_text_hash": anchor_text_hash,
                    "encoded_text_hash": asset["encoded_text_hash"], "asset_id": asset["asset_id"],
                    "vector_hash": asset["vector_hash"]})
            manifest = {"policy_id": policy_id, "policy_version": policy_version,
                        "embedding_profile_id": embedding_profile_id, "anchors": manifest_entries}
            fingerprint = digest(manifest)
            existing_manifest = connection.execute("""SELECT * FROM policy_embedding_manifests
                WHERE org_id=? AND job_id=? AND policy_id=? AND policy_version=?
                  AND embedding_profile_id=?""",
                (context.org_id, job_id, policy_id, policy_version, embedding_profile_id)).fetchone()
            if existing_manifest:
                row = dict(existing_manifest)
                if (row["manifest_json"] != json_dump(manifest) or row["manifest_hash"] != fingerprint
                        or row["anchor_count"] != len(manifest_entries)):
                    raise ValueError("Stored policy embedding manifest conflicts with validated assets")
                return row
            connection.execute("""INSERT INTO policy_embedding_manifests
                (org_id,job_id,policy_id,policy_version,embedding_profile_id,anchor_count,
                 manifest_json,manifest_hash,builder_version,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (context.org_id, job_id, policy_id, policy_version, embedding_profile_id,
                 len(manifest_entries), json_dump(manifest), fingerprint, builder_version,
                 context.user_id))
            self.audit(connection, context, "POLICY_EMBEDDINGS_READY", policy_id,
                       {"job_id": job_id, "policy_version": policy_version,
                        "embedding_profile_id": embedding_profile_id,
                        "anchor_count": len(manifest_entries), "manifest_hash": fingerprint})
            return self.one(connection, """SELECT * FROM policy_embedding_manifests
                WHERE org_id=? AND job_id=? AND policy_id=? AND policy_version=?
                  AND embedding_profile_id=?""",
                (context.org_id, job_id, policy_id, policy_version, embedding_profile_id))

    def get_policy_embedding_manifest(self, context, job_id, policy_id, policy_version,
                                      embedding_profile_id):
        with self.read(context, job_id, "evaluate") as connection:
            row = self.one(connection, """SELECT * FROM policy_embedding_manifests
                WHERE org_id=? AND job_id=? AND policy_id=? AND policy_version=?
                  AND embedding_profile_id=?""",
                (context.org_id, job_id, policy_id, policy_version, embedding_profile_id))
            manifest = json.loads(row["manifest_json"])
            if digest(manifest) != row["manifest_hash"] or len(manifest.get("anchors", [])) != row["anchor_count"]:
                raise ValueError("Policy embedding manifest hash or count is invalid")
            return row

    def get_policy_embeddings(self, context, job_id, policy_id, policy_version,
                              embedding_profile_id):
        with self.read(context, job_id, "evaluate") as connection:
            manifest = self.one(connection, """SELECT * FROM policy_embedding_manifests
                WHERE org_id=? AND job_id=? AND policy_id=? AND policy_version=?
                  AND embedding_profile_id=?""",
                (context.org_id, job_id, policy_id, policy_version, embedding_profile_id))
            manifest_json = json.loads(manifest["manifest_json"])
            if digest(manifest_json) != manifest["manifest_hash"]:
                raise ValueError("Policy embedding manifest hash is invalid")
            rows = connection.execute("""SELECT a.anchor_id,a.requirement_ids_json,a.polarity,
                    a.anchor_text,a.anchor_text_hash,e.embedding_profile_id,e.encoded_text_hash,e.asset_id,
                    x.vector_blob,x.vector_hash,x.dimension,x.dtype,x.byte_order,x.vector_norm
                FROM policy_anchors a JOIN policy_embeddings e
                  ON e.org_id=a.org_id AND e.job_id=a.job_id AND e.policy_id=a.policy_id
                 AND e.policy_version=a.policy_version AND e.anchor_id=a.anchor_id
                JOIN embedding_assets x ON x.org_id=e.org_id AND x.asset_id=e.asset_id
                WHERE a.org_id=? AND a.job_id=? AND a.policy_id=? AND a.policy_version=?
                  AND e.embedding_profile_id=? ORDER BY a.anchor_id""",
                (context.org_id, job_id, policy_id, policy_version, embedding_profile_id)).fetchall()
            profile = self._profile(connection, context.org_id, embedding_profile_id)
            result = [self._validate_asset_row(dict(row), profile) for row in rows]
            if len(result) != manifest["anchor_count"] or {row["anchor_id"] for row in result} != {
                    item["anchor_id"] for item in manifest_json.get("anchors", [])}:
                raise ValueError("Policy embedding manifest does not match its validated assets")
            return result

    @staticmethod
    def _sealed_resume(connection, org_id, job_id, job_candidate_id, resume_id):
        return BaseRepository.one(connection, """SELECT i.candidate_id,i.resume_id,r.content_hash,
                   r.raw_resume_json,r.normalized_resume_json,r.completeness,r.is_scorable
            FROM job_candidate_inputs i JOIN resume_snapshots r
              ON r.org_id=i.org_id AND r.candidate_id=i.candidate_id AND r.resume_id=i.resume_id
            WHERE i.org_id=? AND i.job_id=? AND i.job_candidate_id=? AND i.resume_id=?""",
            (org_id, job_id, job_candidate_id, resume_id))

    def persist_resume_chunks(self, context, job_id, job_candidate_id, resume_id,
                              chunker_version, chunker_config, chunks):
        _require_text(chunker_version, "chunker_version")
        check_snapshot(chunker_config)
        chunks = list(chunks)
        if not chunks:
            raise ValueError("A semantic chunk manifest requires at least one traceable chunk")
        with self.tx(context, job_id, "evaluate") as connection:
            source = self._sealed_resume(connection, context.org_id, job_id, job_candidate_id, resume_id)
            config_hash = digest(chunker_config)
            seen = set()
            entries = []
            prepared = []
            for item in chunks:
                chunk_id = _require_text(item.get("chunk_id"), "chunk_id")
                if chunk_id in seen:
                    raise ValueError("Resume chunks must have unique chunk IDs")
                seen.add(chunk_id)
                chunk_text = item.get("text")
                if not isinstance(chunk_text, str) or not chunk_text:
                    raise ValueError("Resume chunk text must be non-empty")
                start = item.get("start_offset")
                end = item.get("end_offset")
                if type(start) is not int or type(end) is not int or start < 0 or end - start != len(chunk_text):
                    raise ValueError("Resume chunk offsets must locate the exact chunk text")
                chunk_hash = text_hash(chunk_text)
                prepared.append({"chunk_id": chunk_id,
                    "source_path": _require_text(item.get("source_path"), "source_path"),
                    "source_type": _require_text(item.get("source_type"), "source_type"),
                    "start_offset": start, "end_offset": end,
                    "experience_key": _require_text(item.get("experience_key"), "experience_key"),
                    "chunk_text": chunk_text, "chunk_text_hash": chunk_hash,
                    "evidence_quality_type": _require_text(item.get("evidence_quality_type"),
                                                             "evidence_quality_type"),
                    "split_index": _require_int(item.get("split_index", 0), "split_index", minimum=0),
                    "truncated": int(bool(item.get("truncated", False)))})
                entries.append({"chunk_id": chunk_id, "chunk_text_hash": chunk_hash,
                                "source_path": item["source_path"], "start_offset": start,
                                "end_offset": end})
            entries.sort(key=lambda item: item["chunk_id"])
            manifest = {"resume_id": resume_id, "resume_content_hash": source["content_hash"],
                        "chunker_version": chunker_version, "chunks": entries}
            fingerprint = digest(manifest)
            existing = connection.execute("""SELECT * FROM resume_chunk_manifests
                WHERE org_id=? AND candidate_id=? AND resume_id=? AND chunker_version=?""",
                (context.org_id, source["candidate_id"], resume_id, chunker_version)).fetchone()
            if existing:
                row = dict(existing)
                stored = [dict(item) for item in connection.execute("""SELECT * FROM resume_chunks
                    WHERE org_id=? AND candidate_id=? AND resume_id=? AND chunker_version=?
                    ORDER BY chunk_id""",
                    (context.org_id, source["candidate_id"], resume_id, chunker_version))]
                prepared_by_id = {item["chunk_id"]: item for item in prepared}
                comparable = ("source_path", "source_type", "start_offset", "end_offset",
                              "experience_key", "chunk_text", "chunk_text_hash", "evidence_quality_type")
                comparable = comparable + ("split_index", "truncated")
                if (row["chunker_config_hash"] != config_hash
                        or row["chunker_config_json"] != json_dump(chunker_config)
                        or row["manifest_json"] != json_dump(manifest)
                        or row["manifest_hash"] != fingerprint or row["chunk_count"] != len(prepared)
                        or set(prepared_by_id) != {item["chunk_id"] for item in stored}
                        or any(tuple(item[key] for key in comparable)
                               != tuple(prepared_by_id[item["chunk_id"]][key] for key in comparable)
                               for item in stored)):
                    raise ValueError("Stored resume chunk manifest conflicts with supplied deterministic chunks")
                return row
            for item in prepared:
                connection.execute("""INSERT INTO resume_chunks
                    (org_id,candidate_id,resume_id,resume_content_hash,chunker_version,chunk_id,
                     source_path,source_type,start_offset,end_offset,experience_key,chunk_text,
                     chunk_text_hash,evidence_quality_type,split_index,truncated,created_by)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (context.org_id, source["candidate_id"], resume_id, source["content_hash"],
                     chunker_version, item["chunk_id"], item["source_path"], item["source_type"],
                     item["start_offset"], item["end_offset"], item["experience_key"],
                     item["chunk_text"], item["chunk_text_hash"], item["evidence_quality_type"],
                     item["split_index"], item["truncated"], context.user_id))
            connection.execute("""INSERT INTO resume_chunk_manifests
                (org_id,candidate_id,resume_id,resume_content_hash,chunker_version,chunk_count,
                 chunker_config_json,chunker_config_hash,manifest_json,manifest_hash,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (context.org_id, source["candidate_id"], resume_id, source["content_hash"],
                 chunker_version, len(entries), json_dump(chunker_config), config_hash,
                 json_dump(manifest), fingerprint, context.user_id))
            self.audit(connection, context, "RESUME_CHUNKS_READY", resume_id,
                       {"job_id": job_id, "job_candidate_id": job_candidate_id,
                        "chunker_version": chunker_version, "chunk_count": len(entries),
                        "manifest_hash": fingerprint})
            return self.one(connection, """SELECT * FROM resume_chunk_manifests
                WHERE org_id=? AND candidate_id=? AND resume_id=? AND chunker_version=?""",
                (context.org_id, source["candidate_id"], resume_id, chunker_version))

    def get_resume_chunks(self, context, job_id, job_candidate_id, resume_id, chunker_version):
        with self.read(context, job_id, "evaluate") as connection:
            source = self._sealed_resume(connection, context.org_id, job_id, job_candidate_id, resume_id)
            rows = connection.execute("""SELECT * FROM resume_chunks
                WHERE org_id=? AND candidate_id=? AND resume_id=? AND resume_content_hash=?
                  AND chunker_version=? ORDER BY chunk_id""",
                (context.org_id, source["candidate_id"], resume_id, source["content_hash"],
                 chunker_version)).fetchall()
            return [dict(row) for row in rows]

    def get_resume_chunk_manifest(self, context, job_id, job_candidate_id, resume_id,
                                  chunker_version):
        with self.read(context, job_id, "evaluate") as connection:
            source = self._sealed_resume(connection, context.org_id, job_id, job_candidate_id, resume_id)
            row = self.one(connection, """SELECT * FROM resume_chunk_manifests
                WHERE org_id=? AND candidate_id=? AND resume_id=? AND resume_content_hash=?
                  AND chunker_version=?""",
                (context.org_id, source["candidate_id"], resume_id, source["content_hash"],
                 chunker_version))
            manifest = json.loads(row["manifest_json"])
            if (digest(manifest) != row["manifest_hash"]
                    or len(manifest.get("chunks", [])) != row["chunk_count"]):
                raise ValueError("Resume chunk manifest hash or count is invalid")
            return row

    def persist_candidate_embeddings(self, context, job_id, job_candidate_id, resume_id,
                                     chunker_version, embedding_profile_id, embeddings, *,
                                     builder_version="resume-embedding-builder-v1"):
        _require_text(builder_version, "builder_version")
        embeddings = list(embeddings)
        with self.tx(context, job_id, "evaluate") as connection:
            source = self._sealed_resume(connection, context.org_id, job_id, job_candidate_id, resume_id)
            profile = self._profile(connection, context.org_id, embedding_profile_id)
            chunk_manifest = self.one(connection, """SELECT * FROM resume_chunk_manifests
                WHERE org_id=? AND candidate_id=? AND resume_id=? AND chunker_version=?""",
                (context.org_id, source["candidate_id"], resume_id, chunker_version))
            chunks = connection.execute("""SELECT * FROM resume_chunks
                WHERE org_id=? AND candidate_id=? AND resume_id=? AND resume_content_hash=?
                  AND chunker_version=? ORDER BY chunk_id""",
                (context.org_id, source["candidate_id"], resume_id, source["content_hash"],
                 chunker_version)).fetchall()
            chunk_by_id = {row["chunk_id"]: dict(row) for row in chunks}
            supplied = {item.get("chunk_id"): item for item in embeddings}
            if None in supplied or len(supplied) != len(embeddings) or set(supplied) != set(chunk_by_id):
                raise ValueError("Candidate embedding input must contain every deterministic chunk exactly once")
            entries = []
            for chunk_id in sorted(chunk_by_id):
                chunk = chunk_by_id[chunk_id]
                item = supplied[chunk_id]
                encoded_text = _require_text(item.get("encoded_text"), "encoded_text")
                asset, _ = self._store_or_reuse_asset(
                    connection, context, embedding_profile_id, encoded_text, item["vector"])
                existing_link = connection.execute("""SELECT * FROM candidate_embeddings
                    WHERE org_id=? AND candidate_id=? AND resume_id=? AND chunker_version=?
                      AND chunk_id=? AND embedding_profile_id=?""",
                    (context.org_id, source["candidate_id"], resume_id, chunker_version,
                     chunk_id, embedding_profile_id)).fetchone()
                if existing_link:
                    link = dict(existing_link)
                    if (link["chunk_text_hash"], link["encoded_text_hash"], link["asset_id"]) != (
                            chunk["chunk_text_hash"], asset["encoded_text_hash"], asset["asset_id"]):
                        raise ValueError("Stored candidate embedding link conflicts with supplied content")
                else:
                    connection.execute("""INSERT INTO candidate_embeddings
                        (org_id,candidate_id,resume_id,resume_content_hash,chunker_version,chunk_id,
                         chunk_text_hash,embedding_profile_id,encoded_text_hash,asset_id,created_by)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (context.org_id, source["candidate_id"], resume_id, source["content_hash"],
                         chunker_version, chunk_id, chunk["chunk_text_hash"], embedding_profile_id,
                         asset["encoded_text_hash"], asset["asset_id"], context.user_id))
                entries.append({"chunk_id": chunk_id, "chunk_text_hash": chunk["chunk_text_hash"],
                                "encoded_text_hash": asset["encoded_text_hash"],
                                "asset_id": asset["asset_id"], "vector_hash": asset["vector_hash"]})
            manifest = {"resume_id": resume_id, "resume_content_hash": source["content_hash"],
                        "chunker_version": chunker_version,
                        "embedding_profile_id": embedding_profile_id, "chunks": entries}
            fingerprint = digest(manifest)
            existing = connection.execute("""SELECT * FROM resume_embedding_manifests
                WHERE org_id=? AND candidate_id=? AND resume_id=? AND chunker_version=?
                  AND embedding_profile_id=?""",
                (context.org_id, source["candidate_id"], resume_id, chunker_version,
                 embedding_profile_id)).fetchone()
            if existing:
                row = dict(existing)
                if (row["manifest_json"] != json_dump(manifest) or row["manifest_hash"] != fingerprint
                        or row["chunk_count"] != len(entries)):
                    raise ValueError("Stored resume embedding manifest conflicts with validated assets")
                return row
            connection.execute("""INSERT INTO resume_embedding_manifests
                (org_id,candidate_id,resume_id,resume_content_hash,chunker_version,
                 embedding_profile_id,chunk_count,manifest_json,manifest_hash,builder_version,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (context.org_id, source["candidate_id"], resume_id, source["content_hash"],
                 chunker_version, embedding_profile_id, len(entries), json_dump(manifest),
                 fingerprint, builder_version, context.user_id))
            self.audit(connection, context, "RESUME_EMBEDDINGS_READY", resume_id,
                       {"job_id": job_id, "job_candidate_id": job_candidate_id,
                        "embedding_profile_id": embedding_profile_id,
                        "chunk_manifest_hash": chunk_manifest["manifest_hash"],
                        "manifest_hash": fingerprint})
            return self.one(connection, """SELECT * FROM resume_embedding_manifests
                WHERE org_id=? AND candidate_id=? AND resume_id=? AND chunker_version=?
                  AND embedding_profile_id=?""",
                (context.org_id, source["candidate_id"], resume_id, chunker_version,
                 embedding_profile_id))

    def get_candidate_embeddings(self, context, job_id, job_candidate_id, resume_id,
                                 chunker_version, embedding_profile_id):
        with self.read(context, job_id, "evaluate") as connection:
            source = self._sealed_resume(connection, context.org_id, job_id, job_candidate_id, resume_id)
            manifest = self.one(connection, """SELECT * FROM resume_embedding_manifests
                WHERE org_id=? AND candidate_id=? AND resume_id=? AND chunker_version=?
                  AND embedding_profile_id=?""",
                (context.org_id, source["candidate_id"], resume_id, chunker_version,
                 embedding_profile_id))
            manifest_json = json.loads(manifest["manifest_json"])
            if digest(manifest_json) != manifest["manifest_hash"]:
                raise ValueError("Resume embedding manifest hash is invalid")
            rows = connection.execute("""SELECT c.*,e.embedding_profile_id,e.encoded_text_hash,e.asset_id,
                       a.vector_blob,a.vector_hash,a.dimension,a.dtype,a.byte_order,a.vector_norm
                FROM resume_chunks c JOIN candidate_embeddings e
                  ON e.org_id=c.org_id AND e.candidate_id=c.candidate_id AND e.resume_id=c.resume_id
                 AND e.chunker_version=c.chunker_version AND e.chunk_id=c.chunk_id
                JOIN embedding_assets a ON a.org_id=e.org_id AND a.asset_id=e.asset_id
                WHERE c.org_id=? AND c.candidate_id=? AND c.resume_id=?
                  AND c.resume_content_hash=? AND c.chunker_version=?
                  AND e.embedding_profile_id=? ORDER BY c.chunk_id""",
                (context.org_id, source["candidate_id"], resume_id, source["content_hash"],
                 chunker_version, embedding_profile_id)).fetchall()
            profile = self._profile(connection, context.org_id, embedding_profile_id)
            result = [self._validate_asset_row(dict(row), profile) for row in rows]
            if len(result) != manifest["chunk_count"] or {row["chunk_id"] for row in result} != {
                    item["chunk_id"] for item in manifest_json.get("chunks", [])}:
                raise ValueError("Resume embedding manifest does not match its validated assets")
            return result

    def get_resume_embedding_manifest(self, context, job_id, job_candidate_id, resume_id,
                                      chunker_version, embedding_profile_id):
        with self.read(context, job_id, "evaluate") as connection:
            source = self._sealed_resume(connection, context.org_id, job_id, job_candidate_id, resume_id)
            row = self.one(connection, """SELECT * FROM resume_embedding_manifests
                WHERE org_id=? AND candidate_id=? AND resume_id=? AND resume_content_hash=?
                  AND chunker_version=? AND embedding_profile_id=?""",
                (context.org_id, source["candidate_id"], resume_id, source["content_hash"],
                 chunker_version, embedding_profile_id))
            manifest = json.loads(row["manifest_json"])
            if (digest(manifest) != row["manifest_hash"]
                    or len(manifest.get("chunks", [])) != row["chunk_count"]):
                raise ValueError("Resume embedding manifest hash or count is invalid")
            return row

    def append_embedding_build_attempt(self, context, job_id, embedding_profile_id, *,
                                       target_kind, status, policy_id=None, policy_version=None,
                                       candidate_id=None, resume_id=None, chunker_version=None, error_type=None,
                                       error_message=None, metrics=None, attempt_id=None):
        metrics = {} if metrics is None else metrics
        check_snapshot(metrics)
        if target_kind not in ("POLICY", "RESUME") or status not in ("SUCCEEDED", "FAILED"):
            raise ValueError("Unknown embedding build target or status")
        if status == "FAILED":
            _require_text(error_type, "error_type")
        elif error_type is not None or error_message is not None:
            raise ValueError("A successful build cannot contain an error")
        identifier = attempt_id or uid()
        with self.tx(context, job_id, "evaluate") as connection:
            self._profile(connection, context.org_id, embedding_profile_id)
            if target_kind == "POLICY":
                if candidate_id is not None or resume_id is not None or chunker_version is not None:
                    raise ValueError("Policy build attempts cannot reference a resume")
                self._confirmed_policy(connection, context.org_id, job_id, policy_id, policy_version)
                if status == "SUCCEEDED":
                    self.one(connection, """SELECT 1 FROM policy_embedding_manifests
                        WHERE org_id=? AND job_id=? AND policy_id=? AND policy_version=?
                          AND embedding_profile_id=?""",
                        (context.org_id, job_id, policy_id, policy_version, embedding_profile_id))
            else:
                if policy_id is not None or policy_version is not None:
                    raise ValueError("Resume build attempts cannot reference a policy")
                _require_text(chunker_version, "chunker_version")
                self.one(connection, """SELECT 1 FROM job_candidate_inputs i
                    WHERE i.org_id=? AND i.job_id=? AND i.candidate_id=? AND i.resume_id=?""",
                    (context.org_id, job_id, candidate_id, resume_id))
                if status == "SUCCEEDED":
                    self.one(connection, """SELECT 1 FROM resume_embedding_manifests
                        WHERE org_id=? AND candidate_id=? AND resume_id=?
                          AND chunker_version=? AND embedding_profile_id=?""",
                        (context.org_id, candidate_id, resume_id, chunker_version, embedding_profile_id))
            connection.execute("""INSERT INTO embedding_build_attempts
                (org_id,attempt_id,job_id,target_kind,policy_id,policy_version,candidate_id,resume_id,chunker_version,
                 embedding_profile_id,status,error_type,error_message,metrics_json,created_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (context.org_id, identifier, job_id, target_kind, policy_id, policy_version,
                  candidate_id, resume_id, chunker_version, embedding_profile_id, status, error_type,
                 error_message, json_dump(metrics), context.user_id))
            self.audit(connection, context, "EMBEDDING_BUILD_ATTEMPT", identifier,
                       {"job_id": job_id, "target_kind": target_kind, "status": status})
            return self.one(connection,
                "SELECT * FROM embedding_build_attempts WHERE org_id=? AND attempt_id=?",
                (context.org_id, identifier))
