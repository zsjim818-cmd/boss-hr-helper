"""Tier 2: route-OR, block-local penalties and bounded Top-K aggregation."""
import re

import numpy as np

from schemas import ChunkSimDetail
from engine.rule_matcher import evidence_sources


DEFAULT_SEMANTIC_CONFIG = {
    "max_score": 55, "similarity_min": .42, "similarity_max": .80,
    "top_k": 3, "top_k_weights": [.65, .25, .1],
    "chunk_chars": 320, "min_chunk_chars": 8,
    "penalty_threshold": .62, "penalty_margin": .04,
    "penalty_baseline": .55, "penalty_weight": 80, "penalty_cap": 12,
    "source_weights": {"work": 1, "project": 1, "thesis": .9, "work_tags": .15, "skills": .2, "advantage": .3},
}


def semantic_parameters(config):
    given = config["scoring_config"].get("semantic_scoring", {})
    params = {**DEFAULT_SEMANTIC_CONFIG, **given}
    params["source_weights"] = {**DEFAULT_SEMANTIC_CONFIG["source_weights"], **given.get("source_weights", {})}
    if not (0 < float(params["max_score"]) <= 55):
        raise ValueError("Tier 2 maximum must be in (0, 55]")
    if params["similarity_max"] <= params["similarity_min"]:
        raise ValueError("Invalid semantic similarity mapping range")
    k, weights = int(params["top_k"]), params["top_k_weights"]
    if k < 1 or len(weights) < k or any(float(w) <= 0 for w in weights[:k]):
        raise ValueError("Invalid Top-K parameters")
    if int(params["chunk_chars"]) < int(params["min_chunk_chars"]) or int(params["min_chunk_chars"]) < 1:
        raise ValueError("Invalid semantic chunk sizes")
    if any(not 0 <= float(w) <= 1 for w in params["source_weights"].values()):
        raise ValueError("Semantic source weights must be in [0, 1]")
    if any(float(params[p]) < 0 for p in ["penalty_weight", "penalty_cap", "penalty_margin"]):
        raise ValueError("Semantic penalty parameters must be nonnegative")
    return params


class SemanticRankerMixin:
    def _refresh_anchor_embeddings(self):
        semantic_parameters(self.config)
        anchors = self.config["scoring_config"]["semantic_anchors"]
        groups = anchors.get("positive_anchor_groups")
        if not groups or not all(groups.get(route) for route in ("agent", "rpa")):
            raise ValueError("V2 requires positive_anchor_groups.agent and .rpa; migrate legacy flat anchors")
        self._positive_groups = {route: list(dict.fromkeys(texts)) for route, texts in groups.items()}
        self._route_anchor_vecs = {
            route: np.asarray(self.embedder.encode(texts, normalize_embeddings=True), dtype=float)
            for route, texts in self._positive_groups.items()
        }
        self._neg_anchors = list(dict.fromkeys(anchors.get("negative_anchors", [])))
        self._neg_anchor_vecs = np.asarray(self.embedder.encode(self._neg_anchors, normalize_embeddings=True), dtype=float) if self._neg_anchors else np.empty(0)
        # Kept for callers that inspect the legacy diagnostic attributes.
        self._pos_anchors = [text for texts in self._positive_groups.values() for text in texts]
        self._pos_anchor_vecs = np.concatenate(list(self._route_anchor_vecs.values()))

    def _semantic_chunks(self, resume, params):
        """Keep strongest source for duplicate snippets, with fixed windows per sentence."""
        chunks = {}
        for source, path, text in evidence_sources(resume, self.config["scoring_config"]):
            for sentence in re.split(r"[\n。！？;；]+", text):
                sentence = sentence.strip()
                for start in range(0, len(sentence), int(params["chunk_chars"])):
                    snippet = sentence[start:start + int(params["chunk_chars"])].strip()
                    if len(snippet) < int(params["min_chunk_chars"]):
                        continue
                    key = re.sub(r"[\W_]+", "", snippet).casefold()
                    item = (source, path, snippet, float(params["source_weights"].get(source, 0)))
                    if key not in chunks or item[3] > chunks[key][3]:
                        chunks[key] = item
        # Stable order makes deterministic reruns independent of source duplicate order.
        return [chunks[key] for key in sorted(chunks)]

    def _run_tier2_semantic(self, resume):
        params = semantic_parameters(self.config)
        chunks = self._semantic_chunks(resume, params)
        if not chunks:
            return 0.0, 0.0, {"pos_max_sim": 0.0, "neg_max_sim": 0.0}, []
        vectors = np.asarray(self.embedder.encode([c[2] for c in chunks], normalize_embeddings=True), dtype=float)
        matrices = {route: vectors @ anchor_vectors.T for route, anchor_vectors in self._route_anchor_vecs.items()}
        neg_matrix = vectors @ self._neg_anchor_vecs.T if len(self._neg_anchor_vecs) else np.zeros((len(chunks), 1))
        if not all(np.isfinite(m).all() for m in list(matrices.values()) + [neg_matrix]):
            raise ValueError("Embedding similarity is non-finite")
        max_score = float(params["max_score"])
        details, route_candidates = [], {route: [] for route in matrices}
        for index, (source, path, text, weight) in enumerate(chunks):
            neg = float(np.max(neg_matrix[index]))
            similarities, scores, penalties = {}, {}, {}
            best_route = max(matrices, key=lambda route: float(np.max(matrices[route][index])))
            best_index = int(np.argmax(matrices[best_route][index]))
            for route, matrix in matrices.items():
                positive = float(np.max(matrix[index]))
                mapped = float(np.clip((positive - params["similarity_min"]) / (params["similarity_max"] - params["similarity_min"]), 0, 1)) * max_score
                penalty = 0.0
                if neg > params["penalty_threshold"] and neg >= positive - params["penalty_margin"]:
                    penalty = min(float(params["penalty_cap"]), max(0.0, (neg - params["penalty_baseline"]) * params["penalty_weight"]))
                penalty = min(mapped, penalty) * weight
                net = max(0.0, mapped * weight - penalty)
                similarities[route] = round(positive, 6)
                scores[route], penalties[route] = round(net, 6), round(penalty, 6)
                if net > 0:
                    # One field may have several sentences/windows; do not reward length
                    # of that field as multiple independent experiences.
                    entity = re.sub(r"\.(?:responsibility|performance|description|achievement|thesis_description)$", "", path)
                    route_candidates[route].append((net, penalty, index, entity, source))
            details.append(ChunkSimDetail(
                chunk_preview=text.replace("\n", " ")[:64], text=text,
                source=source, source_path=path, source_weight=weight,
                best_pos_sim=round(float(np.max(matrices[best_route][index])), 6),
                best_pos_anchor=self._positive_groups[best_route][best_index],
                best_neg_sim=round(neg, 6), route_scores=scores,
                route_positive_similarities=similarities, route_penalties=penalties,
            ))

        route_results, route_penalties = {}, {}
        for route, candidates in route_candidates.items():
            # One experience cannot fill Top-K by splitting descriptions/results.
            by_field = {}
            for row in candidates:
                if row[3] not in by_field or row[0] > by_field[row[3]][0]:
                    by_field[row[3]] = row
            # Supporting labels cannot dilute concrete practice or occupy its slots.
            # They remain an independent weak fallback signal, never additive.
            populations = [
                [row for row in by_field.values() if row[4] in {"work", "project", "thesis"}],
                [row for row in by_field.values() if row[4] not in {"work", "project", "thesis"}],
            ]
            aggregates = []
            for population in populations:
                ranked = sorted(population, key=lambda row: (-row[0], row[2]))[:int(params["top_k"])]
                weights = [float(w) for w in params["top_k_weights"][:len(ranked)]]
                denominator = sum(weights) or 1
                score = sum(row[0] * w for row, w in zip(ranked, weights)) / denominator
                penalty = sum(row[1] * w for row, w in zip(ranked, weights)) / denominator
                aggregates.append((score, penalty, ranked))
            route_results[route], route_penalties[route], ranked = max(aggregates, key=lambda row: row[0])
            for row in ranked:
                details[row[2]].selected_routes.append(route)
        winner = max(route_results, key=route_results.get)
        metrics = {"pos_max_sim": round(max(d.best_pos_sim for d in details), 6), "neg_max_sim": round(max(d.best_neg_sim for d in details), 6)}
        metrics.update({f"route_{route}_score": round(score, 2) for route, score in route_results.items()})
        metrics.update({f"route_{route}_penalty": round(route_penalties[route], 2) for route in route_results})
        return round(min(max_score, route_results[winner]), 2), round(route_penalties[winner], 2), metrics, details
