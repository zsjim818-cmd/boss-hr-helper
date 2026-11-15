-- Stage 5 derived semantic assets.  Policy and resume source snapshots remain
-- authoritative; vectors are immutable, reproducible projections of them.

CREATE UNIQUE INDEX uq_resume_snapshot_content_binding
ON resume_snapshots(org_id,candidate_id,resume_id,content_hash);

CREATE TABLE embedding_profiles (
 org_id TEXT NOT NULL,
 embedding_profile_id TEXT NOT NULL,
 profile_json TEXT NOT NULL CHECK(json_valid(profile_json) AND json_type(profile_json)='object'),
 profile_hash TEXT NOT NULL CHECK(length(profile_hash)=64),
 model_name TEXT NOT NULL CHECK(length(trim(model_name))>0),
 model_revision TEXT,
 revision_state TEXT NOT NULL CHECK(revision_state IN ('PINNED','UNPINNED')),
 tokenizer_name TEXT NOT NULL CHECK(length(trim(tokenizer_name))>0),
 tokenizer_revision TEXT,
 pooling TEXT NOT NULL CHECK(length(trim(pooling))>0),
 normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version))>0),
 input_prefix TEXT NOT NULL DEFAULT '',
 max_length INTEGER NOT NULL CHECK(typeof(max_length)='integer' AND max_length>0),
 truncation TEXT NOT NULL CHECK(truncation IN ('HEAD','TAIL','LONGEST_FIRST')),
 l2_normalize INTEGER NOT NULL CHECK(l2_normalize IN (0,1)),
 dimension INTEGER NOT NULL CHECK(typeof(dimension)='integer' AND dimension>0),
 dtype TEXT NOT NULL DEFAULT 'float32' CHECK(dtype='float32'),
 byte_order TEXT NOT NULL DEFAULT 'little' CHECK(byte_order='little'),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,embedding_profile_id),
 UNIQUE(org_id,profile_hash),
 UNIQUE(org_id,embedding_profile_id,dimension,dtype,byte_order),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
 ,CHECK((revision_state='PINNED' AND model_revision IS NOT NULL AND length(trim(model_revision))>0
         AND tokenizer_revision IS NOT NULL AND length(trim(tokenizer_revision))>0)
        OR revision_state='UNPINNED')
);

CREATE TABLE embedding_assets (
 org_id TEXT NOT NULL,
 asset_id TEXT NOT NULL,
 embedding_profile_id TEXT NOT NULL,
 encoded_text_hash TEXT NOT NULL CHECK(length(encoded_text_hash)=64),
 vector_blob BLOB NOT NULL,
 vector_hash TEXT NOT NULL CHECK(length(vector_hash)=64),
 dimension INTEGER NOT NULL CHECK(typeof(dimension)='integer' AND dimension>0),
 dtype TEXT NOT NULL DEFAULT 'float32' CHECK(dtype='float32'),
 byte_order TEXT NOT NULL DEFAULT 'little' CHECK(byte_order='little'),
 vector_norm REAL NOT NULL CHECK(vector_norm>0.0),
 validator_version TEXT NOT NULL CHECK(length(trim(validator_version))>0),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,asset_id),
 UNIQUE(org_id,embedding_profile_id,encoded_text_hash),
 UNIQUE(org_id,asset_id,embedding_profile_id,encoded_text_hash),
 CHECK(typeof(vector_blob)='blob' AND length(vector_blob)=dimension*4),
 FOREIGN KEY(org_id,embedding_profile_id,dimension,dtype,byte_order)
   REFERENCES embedding_profiles(org_id,embedding_profile_id,dimension,dtype,byte_order),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE TABLE policy_anchors (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 policy_id TEXT NOT NULL,
 policy_version INTEGER NOT NULL CHECK(typeof(policy_version)='integer' AND policy_version>0),
 anchor_id TEXT NOT NULL,
 requirement_ids_json TEXT NOT NULL
   CHECK(json_valid(requirement_ids_json) AND json_type(requirement_ids_json)='array'
         AND json_array_length(requirement_ids_json)>0),
 polarity TEXT NOT NULL CHECK(polarity IN ('POSITIVE','NEGATIVE')),
 anchor_text TEXT NOT NULL CHECK(length(trim(anchor_text))>0),
 anchor_text_hash TEXT NOT NULL CHECK(length(anchor_text_hash)=64),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,policy_id,policy_version,anchor_id),
 UNIQUE(org_id,job_id,policy_id,policy_version,anchor_id,anchor_text_hash),
 FOREIGN KEY(org_id,job_id,policy_id,policy_version)
   REFERENCES job_policy_versions(org_id,job_id,policy_id,policy_version),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE TABLE policy_embeddings (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 policy_id TEXT NOT NULL,
 policy_version INTEGER NOT NULL,
 anchor_id TEXT NOT NULL,
 embedding_profile_id TEXT NOT NULL,
 anchor_text_hash TEXT NOT NULL,
 encoded_text_hash TEXT NOT NULL,
 asset_id TEXT NOT NULL,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,policy_id,policy_version,anchor_id,embedding_profile_id),
 FOREIGN KEY(org_id,job_id,policy_id,policy_version,anchor_id,anchor_text_hash)
   REFERENCES policy_anchors(org_id,job_id,policy_id,policy_version,anchor_id,anchor_text_hash),
 FOREIGN KEY(org_id,asset_id,embedding_profile_id,encoded_text_hash)
   REFERENCES embedding_assets(org_id,asset_id,embedding_profile_id,encoded_text_hash),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE TABLE policy_embedding_manifests (
 org_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 policy_id TEXT NOT NULL,
 policy_version INTEGER NOT NULL,
 embedding_profile_id TEXT NOT NULL,
 anchor_count INTEGER NOT NULL CHECK(typeof(anchor_count)='integer' AND anchor_count>0),
 manifest_json TEXT NOT NULL
   CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object'
         AND json_type(manifest_json,'$.anchors')='array'),
 manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64),
 builder_version TEXT NOT NULL CHECK(length(trim(builder_version))>0),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,job_id,policy_id,policy_version,embedding_profile_id),
 FOREIGN KEY(org_id,job_id,policy_id,policy_version)
   REFERENCES job_policy_versions(org_id,job_id,policy_id,policy_version),
 FOREIGN KEY(org_id,embedding_profile_id) REFERENCES embedding_profiles(org_id,embedding_profile_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE TABLE resume_chunks (
 org_id TEXT NOT NULL,
 candidate_id TEXT NOT NULL,
 resume_id TEXT NOT NULL,
 resume_content_hash TEXT NOT NULL,
 chunker_version TEXT NOT NULL CHECK(length(trim(chunker_version))>0),
 chunk_id TEXT NOT NULL,
 source_path TEXT NOT NULL CHECK(length(trim(source_path))>0),
 source_type TEXT NOT NULL CHECK(length(trim(source_type))>0),
 start_offset INTEGER NOT NULL CHECK(typeof(start_offset)='integer' AND start_offset>=0),
 end_offset INTEGER NOT NULL CHECK(typeof(end_offset)='integer' AND end_offset>start_offset),
 experience_key TEXT NOT NULL CHECK(length(trim(experience_key))>0),
 chunk_text TEXT NOT NULL CHECK(length(chunk_text)>0),
 chunk_text_hash TEXT NOT NULL CHECK(length(chunk_text_hash)=64),
 evidence_quality_type TEXT NOT NULL CHECK(length(trim(evidence_quality_type))>0),
 split_index INTEGER NOT NULL CHECK(typeof(split_index)='integer' AND split_index>=0),
 truncated INTEGER NOT NULL DEFAULT 0 CHECK(truncated IN (0,1)),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,candidate_id,resume_id,chunker_version,chunk_id),
 UNIQUE(org_id,candidate_id,resume_id,resume_content_hash,chunker_version,chunk_id,chunk_text_hash),
 CHECK(end_offset-start_offset=length(chunk_text)),
 FOREIGN KEY(org_id,candidate_id,resume_id,resume_content_hash)
   REFERENCES resume_snapshots(org_id,candidate_id,resume_id,content_hash),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE TABLE resume_chunk_manifests (
 org_id TEXT NOT NULL,
 candidate_id TEXT NOT NULL,
 resume_id TEXT NOT NULL,
 resume_content_hash TEXT NOT NULL,
 chunker_version TEXT NOT NULL,
 chunk_count INTEGER NOT NULL CHECK(typeof(chunk_count)='integer' AND chunk_count>0),
 chunker_config_json TEXT NOT NULL
   CHECK(json_valid(chunker_config_json) AND json_type(chunker_config_json)='object'),
 chunker_config_hash TEXT NOT NULL CHECK(length(chunker_config_hash)=64),
 manifest_json TEXT NOT NULL
   CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object'
         AND json_type(manifest_json,'$.chunks')='array'),
 manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,candidate_id,resume_id,chunker_version),
 FOREIGN KEY(org_id,candidate_id,resume_id,resume_content_hash)
   REFERENCES resume_snapshots(org_id,candidate_id,resume_id,content_hash),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE TABLE candidate_embeddings (
 org_id TEXT NOT NULL,
 candidate_id TEXT NOT NULL,
 resume_id TEXT NOT NULL,
 resume_content_hash TEXT NOT NULL,
 chunker_version TEXT NOT NULL,
 chunk_id TEXT NOT NULL,
 chunk_text_hash TEXT NOT NULL,
 embedding_profile_id TEXT NOT NULL,
 encoded_text_hash TEXT NOT NULL,
 asset_id TEXT NOT NULL,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,candidate_id,resume_id,chunker_version,chunk_id,embedding_profile_id),
 FOREIGN KEY(org_id,candidate_id,resume_id,resume_content_hash,chunker_version,chunk_id,chunk_text_hash)
   REFERENCES resume_chunks(org_id,candidate_id,resume_id,resume_content_hash,chunker_version,chunk_id,chunk_text_hash),
 FOREIGN KEY(org_id,asset_id,embedding_profile_id,encoded_text_hash)
   REFERENCES embedding_assets(org_id,asset_id,embedding_profile_id,encoded_text_hash),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE TABLE resume_embedding_manifests (
 org_id TEXT NOT NULL,
 candidate_id TEXT NOT NULL,
 resume_id TEXT NOT NULL,
 resume_content_hash TEXT NOT NULL,
 chunker_version TEXT NOT NULL,
 embedding_profile_id TEXT NOT NULL,
 chunk_count INTEGER NOT NULL CHECK(typeof(chunk_count)='integer' AND chunk_count>0),
 manifest_json TEXT NOT NULL
   CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object'
         AND json_type(manifest_json,'$.chunks')='array'),
 manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64),
 builder_version TEXT NOT NULL CHECK(length(trim(builder_version))>0),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,candidate_id,resume_id,chunker_version,embedding_profile_id),
 FOREIGN KEY(org_id,candidate_id,resume_id,chunker_version)
   REFERENCES resume_chunk_manifests(org_id,candidate_id,resume_id,chunker_version),
 FOREIGN KEY(org_id,embedding_profile_id) REFERENCES embedding_profiles(org_id,embedding_profile_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE TABLE embedding_build_attempts (
 org_id TEXT NOT NULL,
 attempt_id TEXT NOT NULL,
 job_id TEXT NOT NULL,
 target_kind TEXT NOT NULL CHECK(target_kind IN ('POLICY','RESUME')),
 policy_id TEXT,
 policy_version INTEGER,
 candidate_id TEXT,
 resume_id TEXT,
 chunker_version TEXT,
 embedding_profile_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('SUCCEEDED','FAILED')),
 error_type TEXT,
 error_message TEXT,
 metrics_json TEXT NOT NULL DEFAULT '{}'
   CHECK(json_valid(metrics_json) AND json_type(metrics_json)='object'),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(org_id,attempt_id),
  CHECK((target_kind='POLICY' AND policy_id IS NOT NULL AND policy_version IS NOT NULL
         AND candidate_id IS NULL AND resume_id IS NULL AND chunker_version IS NULL)
    OR (target_kind='RESUME' AND policy_id IS NULL AND policy_version IS NULL
         AND candidate_id IS NOT NULL AND resume_id IS NOT NULL
         AND chunker_version IS NOT NULL AND length(trim(chunker_version))>0)),
 CHECK((status='FAILED' AND error_type IS NOT NULL AND length(trim(error_type))>0)
    OR (status='SUCCEEDED' AND error_type IS NULL AND error_message IS NULL)),
 FOREIGN KEY(org_id,job_id) REFERENCES jobs(org_id,job_id),
 FOREIGN KEY(org_id,job_id,policy_id,policy_version)
   REFERENCES job_policy_versions(org_id,job_id,policy_id,policy_version),
 FOREIGN KEY(org_id,candidate_id,resume_id) REFERENCES resume_snapshots(org_id,candidate_id,resume_id),
 FOREIGN KEY(org_id,embedding_profile_id) REFERENCES embedding_profiles(org_id,embedding_profile_id),
 FOREIGN KEY(org_id,created_by) REFERENCES organization_memberships(org_id,user_id)
);

CREATE INDEX idx_embedding_assets_profile ON embedding_assets(org_id,embedding_profile_id,created_at);
CREATE INDEX idx_policy_embeddings_asset ON policy_embeddings(org_id,asset_id);
CREATE INDEX idx_resume_chunks_content ON resume_chunks(org_id,resume_content_hash,chunker_version,chunk_text_hash);
CREATE INDEX idx_candidate_embeddings_asset ON candidate_embeddings(org_id,asset_id);
CREATE INDEX idx_embedding_attempt_target ON embedding_build_attempts(org_id,job_id,target_kind,created_at);

-- A projected anchor must exactly exist in the confirmed immutable Policy JSON.
CREATE TRIGGER policy_anchor_matches_confirmed_policy BEFORE INSERT ON policy_anchors
WHEN NOT EXISTS(
 SELECT 1 FROM job_policy_versions v
 JOIN policy_version_compilations c
   ON c.org_id=v.org_id AND c.job_id=v.job_id AND c.policy_id=v.policy_id
 JOIN json_each(v.policy_json,'$.semantic_anchors') a
 WHERE v.org_id=NEW.org_id AND v.job_id=NEW.job_id AND v.policy_id=NEW.policy_id
   AND v.policy_version=NEW.policy_version
   AND json_extract(a.value,'$.anchor_id')=NEW.anchor_id
   AND json_extract(a.value,'$.polarity')=NEW.polarity
   AND json_extract(a.value,'$.text')=NEW.anchor_text
   AND json(json_extract(a.value,'$.requirement_ids'))=json(NEW.requirement_ids_json)
)
BEGIN SELECT RAISE(ABORT,'policy anchor must exactly match a confirmed immutable policy'); END;

-- READY manifests are inserted last and only for a complete one-to-one mapping.
CREATE TRIGGER policy_embedding_manifest_complete BEFORE INSERT ON policy_embedding_manifests
WHEN NEW.anchor_count<>(SELECT json_array_length(json_extract(v.policy_json,'$.semantic_anchors'))
                        FROM job_policy_versions v
                        WHERE v.org_id=NEW.org_id AND v.job_id=NEW.job_id
                          AND v.policy_id=NEW.policy_id AND v.policy_version=NEW.policy_version)
 OR NEW.anchor_count<>(SELECT count(*) FROM policy_anchors a
                       WHERE a.org_id=NEW.org_id AND a.job_id=NEW.job_id
                         AND a.policy_id=NEW.policy_id AND a.policy_version=NEW.policy_version)
 OR NEW.anchor_count<>json_array_length(json_extract(NEW.manifest_json,'$.anchors'))
 OR EXISTS(
    SELECT 1 FROM policy_anchors a
    WHERE a.org_id=NEW.org_id AND a.job_id=NEW.job_id
      AND a.policy_id=NEW.policy_id AND a.policy_version=NEW.policy_version
      AND NOT EXISTS(
        SELECT 1 FROM policy_embeddings e
        WHERE e.org_id=a.org_id AND e.job_id=a.job_id AND e.policy_id=a.policy_id
          AND e.policy_version=a.policy_version AND e.anchor_id=a.anchor_id
          AND e.embedding_profile_id=NEW.embedding_profile_id))
BEGIN SELECT RAISE(ABORT,'policy embedding manifest requires every policy anchor'); END;

CREATE TRIGGER resume_chunk_manifest_complete BEFORE INSERT ON resume_chunk_manifests
WHEN NEW.chunk_count<>(SELECT count(*) FROM resume_chunks c
                       WHERE c.org_id=NEW.org_id AND c.candidate_id=NEW.candidate_id
                         AND c.resume_id=NEW.resume_id AND c.resume_content_hash=NEW.resume_content_hash
                         AND c.chunker_version=NEW.chunker_version)
 OR NEW.chunk_count<>json_array_length(json_extract(NEW.manifest_json,'$.chunks'))
BEGIN SELECT RAISE(ABORT,'resume chunk manifest requires the complete deterministic chunk set'); END;

CREATE TRIGGER resume_embedding_manifest_complete BEFORE INSERT ON resume_embedding_manifests
WHEN NEW.chunk_count<>(SELECT count(*) FROM resume_chunks c
                       WHERE c.org_id=NEW.org_id AND c.candidate_id=NEW.candidate_id
                         AND c.resume_id=NEW.resume_id AND c.resume_content_hash=NEW.resume_content_hash
                         AND c.chunker_version=NEW.chunker_version)
 OR NEW.chunk_count<>(SELECT count(*) FROM candidate_embeddings e
                      WHERE e.org_id=NEW.org_id AND e.candidate_id=NEW.candidate_id
                        AND e.resume_id=NEW.resume_id AND e.resume_content_hash=NEW.resume_content_hash
                        AND e.chunker_version=NEW.chunker_version
                        AND e.embedding_profile_id=NEW.embedding_profile_id)
 OR NEW.chunk_count<>json_array_length(json_extract(NEW.manifest_json,'$.chunks'))
BEGIN SELECT RAISE(ABORT,'resume embedding manifest requires every deterministic chunk'); END;

-- Every derived fact is append-only and rejects REPLACE even if recursive
-- triggers are accidentally disabled on a non-application connection.
CREATE TRIGGER no_replace_embedding_profiles BEFORE INSERT ON embedding_profiles
WHEN EXISTS(SELECT 1 FROM embedding_profiles WHERE org_id=NEW.org_id
            AND (embedding_profile_id=NEW.embedding_profile_id OR profile_hash=NEW.profile_hash))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing embedding_profiles'); END;
CREATE TRIGGER no_update_embedding_profiles BEFORE UPDATE ON embedding_profiles
BEGIN SELECT RAISE(ABORT,'immutable embedding_profiles'); END;
CREATE TRIGGER no_delete_embedding_profiles BEFORE DELETE ON embedding_profiles
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for embedding_profiles'); END;

CREATE TRIGGER no_replace_embedding_assets BEFORE INSERT ON embedding_assets
WHEN EXISTS(SELECT 1 FROM embedding_assets WHERE org_id=NEW.org_id
            AND (asset_id=NEW.asset_id OR (embedding_profile_id=NEW.embedding_profile_id
                 AND encoded_text_hash=NEW.encoded_text_hash)))
BEGIN SELECT RAISE(ABORT,'conflict would replace existing embedding_assets'); END;
CREATE TRIGGER no_update_embedding_assets BEFORE UPDATE ON embedding_assets
BEGIN SELECT RAISE(ABORT,'immutable embedding_assets'); END;
CREATE TRIGGER no_delete_embedding_assets BEFORE DELETE ON embedding_assets
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for embedding_assets'); END;

CREATE TRIGGER no_replace_policy_anchors BEFORE INSERT ON policy_anchors
WHEN EXISTS(SELECT 1 FROM policy_anchors WHERE org_id=NEW.org_id AND job_id=NEW.job_id
            AND policy_id=NEW.policy_id AND policy_version=NEW.policy_version AND anchor_id=NEW.anchor_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing policy_anchors'); END;
CREATE TRIGGER no_update_policy_anchors BEFORE UPDATE ON policy_anchors
BEGIN SELECT RAISE(ABORT,'immutable policy_anchors'); END;
CREATE TRIGGER no_delete_policy_anchors BEFORE DELETE ON policy_anchors
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for policy_anchors'); END;

CREATE TRIGGER no_replace_policy_embeddings BEFORE INSERT ON policy_embeddings
WHEN EXISTS(SELECT 1 FROM policy_embeddings WHERE org_id=NEW.org_id AND job_id=NEW.job_id
            AND policy_id=NEW.policy_id AND policy_version=NEW.policy_version
            AND anchor_id=NEW.anchor_id AND embedding_profile_id=NEW.embedding_profile_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing policy_embeddings'); END;
CREATE TRIGGER no_update_policy_embeddings BEFORE UPDATE ON policy_embeddings
BEGIN SELECT RAISE(ABORT,'immutable policy_embeddings'); END;
CREATE TRIGGER no_delete_policy_embeddings BEFORE DELETE ON policy_embeddings
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for policy_embeddings'); END;

CREATE TRIGGER no_replace_policy_embedding_manifests BEFORE INSERT ON policy_embedding_manifests
WHEN EXISTS(SELECT 1 FROM policy_embedding_manifests WHERE org_id=NEW.org_id AND job_id=NEW.job_id
            AND policy_id=NEW.policy_id AND policy_version=NEW.policy_version
            AND embedding_profile_id=NEW.embedding_profile_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing policy_embedding_manifests'); END;
CREATE TRIGGER no_update_policy_embedding_manifests BEFORE UPDATE ON policy_embedding_manifests
BEGIN SELECT RAISE(ABORT,'immutable policy_embedding_manifests'); END;
CREATE TRIGGER no_delete_policy_embedding_manifests BEFORE DELETE ON policy_embedding_manifests
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for policy_embedding_manifests'); END;

CREATE TRIGGER no_replace_resume_chunks BEFORE INSERT ON resume_chunks
WHEN EXISTS(SELECT 1 FROM resume_chunks WHERE org_id=NEW.org_id AND candidate_id=NEW.candidate_id
            AND resume_id=NEW.resume_id AND chunker_version=NEW.chunker_version AND chunk_id=NEW.chunk_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing resume_chunks'); END;
CREATE TRIGGER no_update_resume_chunks BEFORE UPDATE ON resume_chunks
BEGIN SELECT RAISE(ABORT,'immutable resume_chunks'); END;
CREATE TRIGGER no_delete_resume_chunks BEFORE DELETE ON resume_chunks
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for resume_chunks'); END;

CREATE TRIGGER no_replace_resume_chunk_manifests BEFORE INSERT ON resume_chunk_manifests
WHEN EXISTS(SELECT 1 FROM resume_chunk_manifests WHERE org_id=NEW.org_id AND candidate_id=NEW.candidate_id
            AND resume_id=NEW.resume_id AND chunker_version=NEW.chunker_version)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing resume_chunk_manifests'); END;
CREATE TRIGGER no_update_resume_chunk_manifests BEFORE UPDATE ON resume_chunk_manifests
BEGIN SELECT RAISE(ABORT,'immutable resume_chunk_manifests'); END;
CREATE TRIGGER no_delete_resume_chunk_manifests BEFORE DELETE ON resume_chunk_manifests
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for resume_chunk_manifests'); END;

CREATE TRIGGER no_replace_candidate_embeddings BEFORE INSERT ON candidate_embeddings
WHEN EXISTS(SELECT 1 FROM candidate_embeddings WHERE org_id=NEW.org_id AND candidate_id=NEW.candidate_id
            AND resume_id=NEW.resume_id AND chunker_version=NEW.chunker_version AND chunk_id=NEW.chunk_id
            AND embedding_profile_id=NEW.embedding_profile_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing candidate_embeddings'); END;
CREATE TRIGGER no_update_candidate_embeddings BEFORE UPDATE ON candidate_embeddings
BEGIN SELECT RAISE(ABORT,'immutable candidate_embeddings'); END;
CREATE TRIGGER no_delete_candidate_embeddings BEFORE DELETE ON candidate_embeddings
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for candidate_embeddings'); END;

CREATE TRIGGER no_replace_resume_embedding_manifests BEFORE INSERT ON resume_embedding_manifests
WHEN EXISTS(SELECT 1 FROM resume_embedding_manifests WHERE org_id=NEW.org_id AND candidate_id=NEW.candidate_id
            AND resume_id=NEW.resume_id AND chunker_version=NEW.chunker_version
            AND embedding_profile_id=NEW.embedding_profile_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing resume_embedding_manifests'); END;
CREATE TRIGGER no_update_resume_embedding_manifests BEFORE UPDATE ON resume_embedding_manifests
BEGIN SELECT RAISE(ABORT,'immutable resume_embedding_manifests'); END;
CREATE TRIGGER no_delete_resume_embedding_manifests BEFORE DELETE ON resume_embedding_manifests
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for resume_embedding_manifests'); END;

CREATE TRIGGER no_replace_embedding_build_attempts BEFORE INSERT ON embedding_build_attempts
WHEN EXISTS(SELECT 1 FROM embedding_build_attempts WHERE org_id=NEW.org_id AND attempt_id=NEW.attempt_id)
BEGIN SELECT RAISE(ABORT,'conflict would replace existing embedding_build_attempts'); END;
CREATE TRIGGER no_update_embedding_build_attempts BEFORE UPDATE ON embedding_build_attempts
BEGIN SELECT RAISE(ABORT,'immutable embedding_build_attempts'); END;
CREATE TRIGGER no_delete_embedding_build_attempts BEFORE DELETE ON embedding_build_attempts
BEGIN SELECT RAISE(ABORT,'ordinary deletion forbidden for embedding_build_attempts'); END;
