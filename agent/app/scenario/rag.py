"""
ScenarioForge — Qdrant RAG & Hybrid Knowledge Retrieval Engine
==============================================================

Implements the 4-collection RAG architecture inspired by Chat2Scenic,
adapted for ScenarioForge's Pydantic ScenarioIR and SOTIF safety assurance:

Collections:
  1. `scenario_components` : Decomposed IR components (`ego`, `actor`, `spatial`,
                             `trigger`, `full_scenario`) from approved scenarios.
  2. `regulations`         : International safety standards (UN R152, UN R157,
                             UN R171, NHTSA Pre-Crash, CARLA Leaderboard, and
                             Vietnamese mixed-traffic SOTIF rules).
  3. `execution_history`   : Telemetry & safety metric summaries from past runs
                             to avoid duplicate generation and learn failure modes.
  4. `project_documents`   : Chunked, source-attributed project Markdown.
  5. `external_scenic_examples`: Prepared, unverified Chat2Scenic examples.

Retrieval Strategy:
  - **Dense Search**: Cosine similarity in Qdrant (`:memory:` or local disk `./qdrant_data`).
  - **Sparse Search**: BM25 lexical scoring over tokenized bilingual (EN + VI) text.
  - **Hybrid Re-ranking**: Reciprocal Rank Fusion (RRF) combining dense and sparse ranks:
        RRF_score(d) = 1 / (k + rank_dense(d)) + 1 / (k + rank_sparse(d))
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from qdrant_client import QdrantClient, models

from app.scenario.schemas import ScenarioIR

# ---------------------------------------------------------------------------
# Constants & Collection Names
# ---------------------------------------------------------------------------
COLLECTION_COMPONENTS = "scenario_components"
COLLECTION_REGULATIONS = "regulations"
COLLECTION_HISTORY = "execution_history"
COLLECTION_PROJECT_DOCS = "project_documents"
COLLECTION_EXTERNAL_SCENIC = "external_scenic_examples"

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
PROJECT_MARKDOWN_FILES = (
    "project_docs/RAV03_RESEARCH_REPORT.md",
    "project_docs/RAV03_SIMPLE_PROPOSAL_VI.md",
    "project_docs/RAV03_TECH_PROPOSAL_SHORT_VI.md",
)

EMBEDDING_DIM = 384

# Bilingual (Vietnamese <-> English) domain concept groups for semantic alignment
DOMAIN_CONCEPT_GROUPS: list[tuple[str, ...]] = [
    ("motorcycle", "motorbike", "scooter", "xe máy", "xe mô tô", "two_wheeler"),
    ("car", "sedan", "vehicle", "ô tô", "xe hơi", "xe con", "auto"),
    ("truck", "lorry", "heavy_vehicle", "xe tải", "xe chở hàng", "container"),
    ("pedestrian", "walker", "person", "vru", "người đi bộ", "khách bộ hành"),
    ("bicycle", "cyclist", "bike", "xe đạp", "người đi xe đạp"),
    ("cut_in", "cut in", "lane_change", "swerve", "tạt đầu", "cắt đầu", "chuyển làn", "lấn làn"),
    ("sudden_brake", "emergency_brake", "hard_brake", "decelerate", "phanh gấp", "thắng gấp", "dừng đột ngột"),
    ("jaywalking", "crossing", "dart_out", "băng qua đường", "sang đường", "cắt ngang"),
    ("red_light_violation", "red_light", "run_red", "vượt đèn đỏ", "không nhường đường"),
    ("lane_departure", "oncoming", "drift", "counter_flow", "đi ngược chiều", "lấn làn đối diện", "đối đầu"),
    ("door_opening", "parked", "static_obstacle", "mở cửa xe", "đỗ ven đường"),
    ("intersection", "junction", "crossroad", "4way", "3way", "ngã tư", "ngã ba", "giao lộ", "nút giao"),
    ("highway", "freeway", "expressway", "merge", "cao tốc", "đường trường", "nhập làn"),
    ("urban", "city", "street", "đô thị", "trong phố", "đường phố"),
    ("curve", "bend", "turn", "roundabout", "corner", "đường cong", "ôm cua", "khúc cua", "vòng xuyến"),
    ("rain", "wet", "heavy_rain", "slippery", "mưa", "trời mưa", "mưa lớn", "trơn trượt"),
    ("fog", "mist", "low_visibility", "haze", "sương mù", "khuất tầm nhìn"),
    ("night", "dark", "dusk", "evening", "twilight", "ban đêm", "tối", "hoàng hôn", "chập choạng"),
    ("clear", "sunny", "daylight", "quang đãng", "ban ngày", "nắng"),
    ("occlusion", "blind_spot", "blindspot", "obstructed", "hidden", "điểm mù", "che khuất"),
    ("collision", "crash", "impact", "accident", "va chạm", "đâm", "tai nạn"),
    ("near_miss", "critical", "avoidance", "ttc", "drac", "suýt va chạm", "nguy hiểm"),
    ("ahead_same_lane", "lead_vehicle", "in_front", "phía trước", "cùng làn"),
    ("ahead_adjacent_left", "left_lane", "bên trái", "làn trái"),
    ("ahead_adjacent_right", "right_lane", "bên phải", "làn phải"),
]


def _normalize_text(text: str) -> str:
    """Lowercase and normalize whitespace/punctuation for bilingual tokenization."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\sàáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokenize_with_concepts(text: str) -> list[str]:
    """Tokenize text into words + bilingual canonical concept tags."""
    norm = _normalize_text(text)
    tokens = norm.split()
    expanded = list(tokens)

    for idx, group in enumerate(DOMAIN_CONCEPT_GROUPS):
        concept_tag = f"__concept_{idx}_{group[0]}__"
        for phrase in group:
            if phrase in norm:
                expanded.append(concept_tag)
                break

    return expanded


class DeterministicSemanticEmbedder:
    """
    Fast, deterministic 384-D semantic + n-gram feature hashing embedder.
    Aligns English and Vietnamese ADAS/SOTIF concepts into shared dimensions
    and requires zero external API calls or heavyweight GPU model downloads.
    """

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        norm = _normalize_text(text)
        if not norm:
            vec[0] = 1.0
            return vec

        tokens = _tokenize_with_concepts(text)

        # 1. Dedicated slots (0..63) for canonical domain concepts (high semantic weight)
        for idx, group in enumerate(DOMAIN_CONCEPT_GROUPS):
            slot = idx % 64
            for phrase in group:
                if phrase in norm:
                    vec[slot] += 3.0
                    break

        # 2. Word unigrams & bigrams hashed into slots (64..dim-1)
        hash_space = self.dim - 64
        for i, tok in enumerate(tokens):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            slot = 64 + (h % hash_space)
            sign = 1.0 if (h // hash_space) % 2 == 0 else -1.0
            weight = 2.0 if tok.startswith("__concept_") else 1.0
            vec[slot] += sign * weight

            if i + 1 < len(tokens):
                bigram = f"{tok}_{tokens[i + 1]}"
                hb = int(hashlib.md5(bigram.encode("utf-8")).hexdigest(), 16)
                slot_b = 64 + (hb % hash_space)
                sign_b = 1.0 if (hb // hash_space) % 2 == 0 else -1.0
                vec[slot_b] += sign_b * 0.8

        # 3. Character 3-grams for morphological / typo resilience
        compact = norm.replace(" ", "_")
        for i in range(max(0, len(compact) - 2)):
            trigram = compact[i : i + 3]
            ht = int(hashlib.md5(trigram.encode("utf-8")).hexdigest(), 16)
            slot_t = 64 + (ht % hash_space)
            vec[slot_t] += 0.25

        # L2 normalization for Cosine similarity
        norm_val = math.sqrt(sum(v * v for v in vec))
        if norm_val < 1e-9:
            vec[0] = 1.0
            return vec
        return [round(v / norm_val, 6) for v in vec]


class BM25Index:
    """Lightweight in-memory BM25 sparse lexical search index."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs: dict[str, list[str]] = {}
        self.doc_payloads: dict[str, dict[str, Any]] = {}
        self.doc_freqs: dict[str, int] = {}
        self.avgdl: float = 0.0

    def add_document(self, doc_id: str, text: str, payload: dict[str, Any]) -> None:
        tokens = _tokenize_with_concepts(text)
        if doc_id in self.docs:
            old_unique = set(self.docs[doc_id])
            for t in old_unique:
                self.doc_freqs[t] = max(0, self.doc_freqs.get(t, 1) - 1)

        self.docs[doc_id] = tokens
        self.doc_payloads[doc_id] = payload
        for t in set(tokens):
            self.doc_freqs[t] = self.doc_freqs.get(t, 0) + 1

        total_len = sum(len(toks) for toks in self.docs.values())
        self.avgdl = total_len / max(1, len(self.docs))

    def remove_document(self, doc_id: str) -> None:
        tokens = self.docs.pop(doc_id, None)
        self.doc_payloads.pop(doc_id, None)
        if tokens is None:
            return
        for token in set(tokens):
            remaining = self.doc_freqs.get(token, 0) - 1
            if remaining > 0:
                self.doc_freqs[token] = remaining
            else:
                self.doc_freqs.pop(token, None)
        self.avgdl = sum(len(items) for items in self.docs.values()) / max(1, len(self.docs))


    def search(
        self,
        query: str,
        limit: int = 10,
        filter_fn: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[tuple[str, float]]:
        if not self.docs:
            return []

        q_tokens = _tokenize_with_concepts(query)
        n_docs = len(self.docs)
        scores: list[tuple[str, float]] = []

        for doc_id, doc_tokens in self.docs.items():
            payload = self.doc_payloads[doc_id]
            if filter_fn is not None and not filter_fn(payload):
                continue

            dl = len(doc_tokens)
            score = 0.0
            tf_map: dict[str, int] = {}
            for t in doc_tokens:
                tf_map[t] = tf_map.get(t, 0) + 1

            for qt in q_tokens:
                tf = tf_map.get(qt, 0)
                if tf == 0:
                    continue
                df = self.doc_freqs.get(qt, 0)
                idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
                denom = tf + self.k1 * (1.0 - self.b + self.b * (dl / max(self.avgdl, 1e-6)))
                score += idf * ((tf * (self.k1 + 1.0)) / max(denom, 1e-6))

            if score > 0.0:
                scores.append((doc_id, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:limit]


def chunk_markdown(text: str, *, max_chars: int = 1400) -> list[tuple[str, str]]:
    """Split Markdown on headings and paragraphs while retaining section provenance."""
    chunks: list[tuple[str, str]] = []
    headings: list[str] = []
    section = "Introduction"
    body: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal body, size
        if body:
            chunks.append((section, "\n\n".join(body)))
            body, size = [], 0

    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        block = block.strip()
        if not block:
            continue
        match = re.match(r"^(#{1,6})\s+(.+)", block)
        if match:
            flush()
            level = len(match.group(1))
            headings = headings[:level - 1] + [match.group(2).strip()]
            section = " > ".join(headings)
            block = block[match.end():].strip()
        while block:
            room = max_chars - size - (2 if body else 0)
            if room <= 0:
                flush()
                room = max_chars
            part, block = block[:room], block[room:]
            if part:
                body.append(part)
                size += len(part) + (2 if len(body) > 1 else 0)
            if block:
                flush()
    flush()
    return chunks


@dataclass
class RAGSearchResult:
    """Unified search result returned by Hybrid Search + RRF."""

    doc_id: str
    rrf_score: float
    dense_score: float
    sparse_score: float
    text: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "rrf_score": round(self.rrf_score, 5),
            "dense_score": round(self.dense_score, 4),
            "sparse_score": round(self.sparse_score, 4),
            "text": self.text,
            "payload": self.payload,
        }


class ScenarioRAG:
    """
    Qdrant-backed RAG manager with five collections and hybrid search.
    """

    COLLECTIONS = (
        COLLECTION_COMPONENTS,
        COLLECTION_REGULATIONS,
        COLLECTION_HISTORY,
        COLLECTION_PROJECT_DOCS,
        COLLECTION_EXTERNAL_SCENIC,
    )

    def __init__(
        self,
        location: str | None = ":memory:",
        *,
        path: str | Path | None = None,
        url: str | None = None,
        api_key: str | None = None,
        embed_fn: Callable[[str], list[float]] | None = None,
        embedding_dim: int = EMBEDDING_DIM,
        embedding_model: str | None = None,
        auto_seed: bool = True,
    ) -> None:
        """
        Initialize the Qdrant RAG engine.
        """
        # FastAPI runs sync handlers in a thread pool; serialise access to the in-process Qdrant client.
        self._lock = threading.RLock()
        self.embedding_model = embedding_model or os.getenv("SFORGE_RAG_EMBEDDING_MODEL", "feature_hash_384")
        self.embedding_dim = embedding_dim
        if self.embedding_model == "multilingual_minilm":
            if embedding_dim != 384:
                raise ValueError("multilingual_minilm requires embedding_dim=384")
            from sentence_transformers import SentenceTransformer

            neural_model = SentenceTransformer(
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                local_files_only=True,
                device="cpu",
            )
            self._embedder = neural_model
            self.embed_fn = embed_fn or (lambda value: neural_model.encode(value, normalize_embeddings=True).tolist())
        elif self.embedding_model == "feature_hash_384":
            self._embedder = DeterministicSemanticEmbedder(dim=embedding_dim)
            self.embed_fn = embed_fn or self._embedder.embed
        else:
            raise ValueError(f"Unknown RAG embedding model: {self.embedding_model}")

        if url:
            self.client = QdrantClient(url=url, api_key=api_key or None)
        elif path is not None and str(path).strip():
            index_path = Path(path)
            index_path.mkdir(parents=True, exist_ok=True)
            marker = index_path / ".embedding_model"
            if marker.is_file() and marker.read_text(encoding="utf-8").strip() != self.embedding_model:
                raise ValueError("Qdrant index uses a different embedding model; choose a separate path")
            if not marker.is_file():
                if self.embedding_model != "feature_hash_384" and any(index_path.iterdir()):
                    raise ValueError("Cannot infer embedding model of an existing index; choose an empty path")
                marker.write_text(self.embedding_model, encoding="utf-8")
            self.client = QdrantClient(path=str(path))
        else:
            self.client = QdrantClient(location=location or ":memory:")

        self._bm25_indices: dict[str, BM25Index] = {
            col: BM25Index() for col in self.COLLECTIONS
        }

        self._init_collections()
        self._restore_sparse_indices()
        if auto_seed:
            self.seed_default_knowledge()

    def _init_collections(self) -> None:
        """Ensure all Qdrant collections exist."""
        existing = {c.name for c in self.client.get_collections().collections}
        for col in self.COLLECTIONS:
            if col not in existing:
                self.client.create_collection(
                    collection_name=col,
                    vectors_config=models.VectorParams(
                        size=self.embedding_dim,
                        distance=models.Distance.COSINE,
                    ),
                )

    def _restore_sparse_indices(self) -> None:
        """Rebuild the in-process BM25 side from persisted Qdrant payloads."""
        for collection in self.COLLECTIONS:
            offset = None
            while True:
                points, offset = self.client.scroll(
                    collection_name=collection,
                    offset=offset,
                    limit=256,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in points:
                    payload = point.payload or {}
                    doc_id, body = payload.get("doc_id"), payload.get("text")
                    if isinstance(doc_id, str) and isinstance(body, str):
                        self._bm25_indices[collection].add_document(doc_id, body, payload)
                if offset is None:
                    break

    @staticmethod
    def _string_to_uuid(doc_id: str) -> str:
        """Convert arbitrary string ID into a deterministic UUID string for Qdrant."""
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))

    def upsert_document(self, collection_name: str, doc_id: str, text: str, payload: dict[str, Any]) -> str:
        with self._lock:
            return self._upsert_document(collection_name, doc_id, text, payload)

    def _upsert_document(
        self,
        collection_name: str,
        doc_id: str,
        text: str,
        payload: dict[str, Any],
    ) -> str:
        """Upsert a document into both Qdrant dense index and BM25 sparse index."""
        point_uuid = self._string_to_uuid(doc_id)
        vector = self.embed_fn(text)
        full_payload = dict(payload)
        full_payload["doc_id"] = doc_id
        full_payload["text"] = text

        self.client.upsert(
            collection_name=collection_name,
            points=[
                models.PointStruct(
                    id=point_uuid,
                    vector=vector,
                    payload=full_payload,
                )
            ],
        )
        self._bm25_indices[collection_name].add_document(doc_id, text, full_payload)
        return doc_id

    def hybrid_search(self, collection_name: str, query: str, **kwargs: Any) -> list["RAGSearchResult"]:
        with self._lock:
            return self._hybrid_search(collection_name, query, **kwargs)

    def _hybrid_search(
        self,
        collection_name: str,
        query: str,
        *,
        limit: int = 3,
        filter_field: str | None = None,
        filter_value: str | None = None,
        rrf_k: int = 60,
    ) -> list[RAGSearchResult]:
        """
        Perform Hybrid Search (Dense Qdrant + Sparse BM25) with Reciprocal Rank Fusion (RRF).
        """
        query_vec = self.embed_fn(query)
        qdrant_filter = None
        if filter_field and filter_value:
            qdrant_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key=filter_field,
                        match=models.MatchValue(value=filter_value),
                    )
                ]
            )

        candidate_k = max(limit * 3, 10)

        # 1. Dense search via Qdrant
        if hasattr(self.client, "query_points"):
            resp = self.client.query_points(
                collection_name=collection_name,
                query=query_vec,
                query_filter=qdrant_filter,
                limit=candidate_k,
                with_payload=True,
            )
            dense_hits = resp.points
        else:
            dense_hits = self.client.search(
                collection_name=collection_name,
                query_vector=query_vec,
                query_filter=qdrant_filter,
                limit=candidate_k,
                with_payload=True,
            )

        dense_ranks: dict[str, int] = {}
        dense_scores: dict[str, float] = {}
        payload_store: dict[str, dict[str, Any]] = {}

        for rank_idx, pt in enumerate(dense_hits, start=1):
            pl = pt.payload or {}
            doc_id = str(pl.get("doc_id", pt.id))
            dense_ranks[doc_id] = rank_idx
            dense_scores[doc_id] = float(pt.score or 0.0)
            payload_store[doc_id] = pl

        # 2. Sparse search via BM25
        filter_fn = None
        if filter_field and filter_value:
            filter_fn = lambda pl: pl.get(filter_field) == filter_value

        bm25_hits = self._bm25_indices[collection_name].search(
            query, limit=candidate_k, filter_fn=filter_fn
        )
        sparse_ranks: dict[str, int] = {}
        sparse_scores: dict[str, float] = {}
        for rank_idx, (doc_id, b_score) in enumerate(bm25_hits, start=1):
            sparse_ranks[doc_id] = rank_idx
            sparse_scores[doc_id] = float(b_score)
            if doc_id not in payload_store:
                payload_store[doc_id] = self._bm25_indices[collection_name].doc_payloads.get(doc_id, {})

        # 3. Reciprocal Rank Fusion (RRF)
        all_doc_ids = set(dense_ranks.keys()) | set(sparse_ranks.keys())
        fused_results: list[RAGSearchResult] = []

        for doc_id in all_doc_ids:
            r_d = dense_ranks.get(doc_id)
            r_s = sparse_ranks.get(doc_id)
            rrf_val = 0.0
            if r_d is not None:
                rrf_val += 1.0 / (rrf_k + r_d)
            if r_s is not None:
                rrf_val += 1.0 / (rrf_k + r_s)

            pl = payload_store.get(doc_id, {})
            text = str(pl.get("text", ""))
            clean_pl = {k: v for k, v in pl.items() if k not in ("text",)}
            fused_results.append(
                RAGSearchResult(
                    doc_id=doc_id,
                    rrf_score=rrf_val,
                    dense_score=dense_scores.get(doc_id, 0.0),
                    sparse_score=sparse_scores.get(doc_id, 0.0),
                    text=text,
                    payload=clean_pl,
                )
            )

        fused_results.sort(
            key=lambda r: (r.rrf_score, r.dense_score, r.sparse_score),
            reverse=True,
        )
        return fused_results[:limit]

    # ------------------------------------------------------------------
    # Indexing & Retrieval Methods for the 3 Collections
    # ------------------------------------------------------------------
    def index_scenario_ir(self, ir: ScenarioIR | dict[str, Any], source: str = "suite") -> list[str]:
        """
        Decompose a ScenarioIR into granular components (`spatial`, `ego`, `actor`,
        `trigger`, and `full_scenario`) and index them into `scenario_components`.
        """
        if isinstance(ir, dict):
            ir_obj = ScenarioIR(**ir)
        else:
            ir_obj = ir

        indexed_ids: list[str] = []
        base_desc = f"{ir_obj.name}: {ir_obj.description}"

        # 1. Spatial component
        spatial_id = f"{ir_obj.name}__spatial"
        spatial_data = {
            "map_name": ir_obj.map_name,
            "road_type": ir_obj.ego.road_type,
            "occlusions": ir_obj.occlusions,
        }
        spatial_text = (
            f"Spatial layout for {base_desc} | map={ir_obj.map_name}, "
            f"road_type={ir_obj.ego.road_type}, occlusions={ir_obj.occlusions}"
        )
        self.upsert_document(
            COLLECTION_COMPONENTS,
            spatial_id,
            spatial_text,
            {
                "component_type": "spatial",
                "scenario_name": ir_obj.name,
                "source": source,
                "component_data": spatial_data,
            },
        )
        indexed_ids.append(spatial_id)

        # 2. Ego component
        ego_id = f"{ir_obj.name}__ego"
        ego_data = ir_obj.ego.model_dump()
        ego_text = (
            f"Ego vehicle for {base_desc} | road_type={ir_obj.ego.road_type}, "
            f"speed={ir_obj.ego.initial_speed_kmh} km/h, weather={ir_obj.weather.value}"
        )
        self.upsert_document(
            COLLECTION_COMPONENTS,
            ego_id,
            ego_text,
            {
                "component_type": "ego",
                "scenario_name": ir_obj.name,
                "source": source,
                "component_data": ego_data,
            },
        )
        indexed_ids.append(ego_id)

        # 3. Individual Actor & Trigger components
        for idx, actor in enumerate(ir_obj.actors):
            act_id = f"{ir_obj.name}__actor_{idx}"
            act_data = actor.model_dump(mode="json")
            trig_val = actor.trigger.value if actor.trigger else "none"
            act_text = (
                f"Actor {actor.actor_type.value} in {base_desc} | "
                f"position={actor.relative_position.value}, distance={actor.initial_distance_m}m, "
                f"speed={actor.initial_speed_kmh}km/h, trigger={trig_val}@{actor.trigger_distance_m}m"
            )
            self.upsert_document(
                COLLECTION_COMPONENTS,
                act_id,
                act_text,
                {
                    "component_type": "actor",
                    "scenario_name": ir_obj.name,
                    "actor_type": actor.actor_type.value,
                    "trigger": trig_val,
                    "source": source,
                    "component_data": act_data,
                },
            )
            indexed_ids.append(act_id)

        # 4. Full Scenario component
        full_id = f"{ir_obj.name}__full"
        full_text = (
            f"Full scenario {base_desc} | road={ir_obj.ego.road_type}, "
            f"ego_speed={ir_obj.ego.initial_speed_kmh}km/h, weather={ir_obj.weather.value}, "
            f"actors={[a.actor_type.value for a in ir_obj.actors]}, "
            f"triggers={[a.trigger.value for a in ir_obj.actors if a.trigger]}"
        )
        self.upsert_document(
            COLLECTION_COMPONENTS,
            full_id,
            full_text,
            {
                "component_type": "full_scenario",
                "scenario_name": ir_obj.name,
                "source": source,
                "component_data": ir_obj.model_dump(mode="json"),
            },
        )
        indexed_ids.append(full_id)
        return indexed_ids

    def retrieve_components(
        self,
        query: str,
        *,
        component_type: str | None = None,
        limit: int = 3,
    ) -> list[RAGSearchResult]:
        """Retrieve top-K relevant scenario components (e.g. 'ego', 'actor', 'spatial', 'full_scenario')."""
        return self.hybrid_search(
            COLLECTION_COMPONENTS,
            query,
            limit=limit,
            filter_field="component_type" if component_type else None,
            filter_value=component_type,
        )

    def retrieve_regulations(
        self,
        query: str,
        *,
        regulation_id: str | None = None,
        limit: int = 3,
    ) -> list[RAGSearchResult]:
        """Retrieve top-K safety regulations / benchmarks matching query."""
        return self.hybrid_search(
            COLLECTION_REGULATIONS,
            query,
            limit=limit,
            filter_field="regulation_id" if regulation_id else None,
            filter_value=regulation_id,
        )

    def ingest_markdown(self, file_path: str | Path, *, source: str | None = None) -> int:
        """Chunk, embed and upsert one Markdown file; discard stale chunks on edits."""
        path = Path(file_path)
        content = path.read_text(encoding="utf-8")
        source = source or path.as_posix()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        old_ids = {
            doc_id for doc_id, payload in self._bm25_indices[COLLECTION_PROJECT_DOCS].doc_payloads.items()
            if payload.get("source") == source
        }
        new_ids: set[str] = set()
        for index, (section, body) in enumerate(chunk_markdown(content)):
            doc_id = f"{source}::chunk-{index:04d}"
            new_ids.add(doc_id)
            text = f"Source: {source}\nSection: {section}\n{body}"
            self.upsert_document(
                COLLECTION_PROJECT_DOCS,
                doc_id,
                text,
                {
                    "source": source,
                    "section": section,
                    "chunk_index": index,
                    "content_hash": content_hash,
                    "evidence_type": "project_document",
                },
            )
        stale_ids = old_ids - new_ids
        if stale_ids:
            self.client.delete(
                collection_name=COLLECTION_PROJECT_DOCS,
                points_selector=models.PointIdsList(
                    points=[self._string_to_uuid(doc_id) for doc_id in stale_ids]
                ),
            )
            for doc_id in stale_ids:
                self._bm25_indices[COLLECTION_PROJECT_DOCS].remove_document(doc_id)
        return len(new_ids)

    def retrieve_project_documents(self, query: str, *, limit: int = 3) -> list[RAGSearchResult]:
        """Retrieve source-attributed chunks from the three project Markdown files."""
        return self.hybrid_search(COLLECTION_PROJECT_DOCS, query, limit=limit)

    def ingest_external_scenic_manifest(self, manifest_path: str | Path) -> dict[str, int]:
        """Index prepared training chunks only; keep evaluation cases out of retrieval."""
        path = Path(manifest_path)
        counts = {"examples": 0, "chunks": 0, "holdout": 0}
        if not path.is_file():
            return counts
        old_ids = {
            doc_id for doc_id, payload in self._bm25_indices[COLLECTION_EXTERNAL_SCENIC].doc_payloads.items()
            if payload.get("dataset") == "chat2scenic/Chat2Scenic"
        }
        new_ids: set[str] = set()
        seen_examples: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record["split"] == "holdout":
                counts["holdout"] += 1
                continue
            source_id = record["source_id"]
            seen_examples.add(source_id)
            for chunk in record["chunks"]:
                doc_id = f"chat2scenic::{source_id}::{chunk['kind']}::{chunk['index']:03d}"
                new_ids.add(doc_id)
                self.upsert_document(
                    COLLECTION_EXTERNAL_SCENIC,
                    doc_id,
                    chunk["text"],
                    {
                        "source": record["source_url"],
                        "source_id": source_id,
                        "source_sha256": record["sha256"],
                        "dataset": "chat2scenic/Chat2Scenic",
                        "license": "Apache-2.0 (dataset card; verify upstream provenance)",
                        "split": "rag",
                        "chunk_type": chunk["kind"],
                        "carla_map": record["carla_map"],
                        "compatibility": "unverified_external_scenic",
                    },
                )
                counts["chunks"] += 1
        stale_ids = old_ids - new_ids
        if stale_ids:
            self.client.delete(
                collection_name=COLLECTION_EXTERNAL_SCENIC,
                points_selector=models.PointIdsList(points=[self._string_to_uuid(doc_id) for doc_id in stale_ids]),
            )
            for doc_id in stale_ids:
                self._bm25_indices[COLLECTION_EXTERNAL_SCENIC].remove_document(doc_id)
        counts["examples"] = len(seen_examples)
        return counts

    def retrieve_external_scenic_examples(self, query: str, *, limit: int = 2) -> list[RAGSearchResult]:
        """Retrieve descriptions only; Scenic code cannot be executed as ScenarioIR."""
        return self.hybrid_search(
            COLLECTION_EXTERNAL_SCENIC, query, limit=limit,
            filter_field="chunk_type", filter_value="semantic",
        )

    def record_execution_result(
        self,
        scenario: ScenarioIR | dict[str, Any],
        telemetry_summary: dict[str, Any],
        run_id: str | None = None,
        *,
        evidence_type: str = "observed",
    ) -> str:
        """Index a completed simulation run's telemetry summary into `execution_history`."""
        if isinstance(scenario, dict):
            ir_obj = ScenarioIR(**scenario)
        else:
            ir_obj = scenario

        doc_id = run_id or f"run__{ir_obj.name}"
        collision = bool(telemetry_summary.get("collision", False))
        min_ttc = telemetry_summary.get("min_ttc_s", telemetry_summary.get("min_ttc"))
        max_drac = telemetry_summary.get("max_drac_m_s2", telemetry_summary.get("max_drac", 0.0))
        is_critical = bool(telemetry_summary.get("is_critical", False))

        verdict = telemetry_summary.get('evaluation_status') or ("COLLISION" if collision else ("NEAR_MISS" if is_critical else "PASS"))
        text = (
            f"Execution history for {ir_obj.name}: {ir_obj.description} | "
            f"road={ir_obj.ego.road_type}, weather={ir_obj.weather.value}, "
            f"verdict={verdict}, collision={collision}, min_ttc={min_ttc}, max_drac={max_drac}"
        )
        payload = {
            "evidence_type": evidence_type,
            "scenario_name": ir_obj.name,
            "description": ir_obj.description,
            "road_type": ir_obj.ego.road_type,
            "weather": ir_obj.weather.value,
            "verdict": verdict,
            "collision": collision,
            "min_ttc_s": min_ttc,
            "max_drac_m_s2": max_drac,
            "telemetry_summary": telemetry_summary,
            "metric_contract_version": telemetry_summary.get('metric_contract_version','legacy_unknown'),
            "adaptive_evidence_eligible": telemetry_summary.get('adaptive_evidence_eligible',False),
            "adaptive_evidence_reasons": telemetry_summary.get('adaptive_evidence_reasons',['Legacy/unverified evidence']),
            "near_miss": telemetry_summary.get('near_miss'),
        }
        return self.upsert_document(COLLECTION_HISTORY, doc_id, text, payload)

    def ingest_run_directory(self, runs_root: str | Path) -> dict[str, int]:
        """Index completed CARLA run artifacts; skip incomplete or invalid runs."""
        root = Path(runs_root)
        counts = {"indexed": 0, "skipped": 0}
        for summary_path in sorted(root.rglob("metrics_summary.json")):
            ir_path = summary_path.parent / "ir_snapshot.json"
            if not ir_path.is_file():
                counts["skipped"] += 1
                continue
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                ir = ScenarioIR(**json.loads(ir_path.read_text(encoding="utf-8")))
                if not isinstance(summary, dict) or summary.get("status", "COMPLETED") != "COMPLETED":
                    counts["skipped"] += 1
                    continue
                evidence_type = summary.get("evidence_type", "observed")
                if evidence_type not in {"observed", "synthetic_test"}:
                    counts["skipped"] += 1
                    continue
                self.record_execution_result(
                    ir, summary, run_id=f"{evidence_type}__{summary_path.parent.name}",
                    evidence_type=evidence_type,
                )
                counts["indexed"] += 1
            except (OSError, ValueError, TypeError, KeyError):
                counts["skipped"] += 1
        return counts

    def retrieve_execution_history(
        self,
        query: str,
        *,
        limit: int = 3,
    ) -> list[RAGSearchResult]:
        """Retrieve similar historical simulation runs from `execution_history`."""
        return self.hybrid_search(COLLECTION_HISTORY, query, limit=limit)

    def seed_default_knowledge(self) -> dict[str, int]:
        """
        Seed `regulations` from `knowledge/regulations.json` (and reference benchmarks if present)
        and `scenario_components` from `knowledge/seed_scenarios.json`.
        """
        counts = {"regulations": 0, "components": 0, "history": 0, "project_documents": 0, "external_scenic_examples": 0}

        for relative_path in PROJECT_MARKDOWN_FILES:
            document = KNOWLEDGE_DIR / relative_path
            if document.is_file():
                counts["project_documents"] += self.ingest_markdown(document, source=f"knowledge/{relative_path}")

        external_counts = self.ingest_external_scenic_manifest(KNOWLEDGE_DIR / "chat2scenic" / "manifest.jsonl")
        counts["external_scenic_examples"] = external_counts["chunks"]

        # 1. Seed Regulations
        reg_file = KNOWLEDGE_DIR / "regulations.json"

        if reg_file and reg_file.is_file():
            try:
                regs = json.loads(reg_file.read_text(encoding="utf-8"))
                for reg in regs:
                    reg_id = reg["regulation_id"]
                    text = (
                        f"{reg_id} ({reg.get('standard', '')} - {reg.get('category', '')}): "
                        f"{reg.get('title', '')}. {reg.get('description', '')} "
                        f"Triggers: {reg.get('applicable_triggers', [])}. "
                        f"Positions: {reg.get('applicable_positions', [])}. "
                        f"SOTIF: {reg.get('sotif_notes', '')}"
                    )
                    self.upsert_document(
                        COLLECTION_REGULATIONS, reg_id, text,
                        {**reg, "source": "knowledge/regulations.json", "evidence_type": "curated_summary"},
                    )
                    counts["regulations"] += 1
            except Exception:
                pass

        # 2. Seed Approved Scenario Components from SCENARIOS_DEF
        try:
            # Reference suite of 20 expert-written scenarios (exported from P-065 batch_generator).
            SCENARIOS_DEF = json.loads((KNOWLEDGE_DIR / "seed_scenarios.json").read_text(encoding="utf-8"))
            for spec in SCENARIOS_DEF:
                ids = self.index_scenario_ir(spec, source="batch_suite_20")
                counts["components"] += len(ids)

                # Also seed expected telemetry outcome into execution_history baseline
                exp = spec.get("expected_outcome") or {}
                exp_v = exp.get("expected_verdict")
                v_str = getattr(exp_v, "value", str(exp_v or "NEAR_MISS"))
                ttc_range = exp.get("expected_min_ttc_range") or (1.0, 2.0)
                synth_summary = {
                    "collision": v_str == "COLLISION_EXPECTED",
                    "is_critical": v_str in ("COLLISION_EXPECTED", "NEAR_MISS"),
                    "min_ttc_s": round((ttc_range[0] + ttc_range[1]) / 2.0, 2),
                    "max_drac_m_s2": 6.5 if v_str != "PASS" else 2.5,
                    "is_fair": True,
                }
                self.record_execution_result(
                    spec, synth_summary, run_id=f"baseline__{spec['name']}", evidence_type="expected"
                )
                counts["history"] += 1
        except Exception:
            pass

        return counts
