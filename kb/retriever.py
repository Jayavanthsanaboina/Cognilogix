"""
Knowledge base: multilingual embeddings + ChromaDB.

Used by the Retrieval agent (search) and the feedback loop (add_cases).
Always run scripts from the project root with `python -m ...` so the
`kb` package is importable.
"""
import json

import chromadb

from .config import (CHROMA_DIR, COLLECTION_NAME, DEFAULT_TOP_K, EMBED_MODEL,
                     PASSAGE_PREFIX, QUERY_PREFIX)

_model = None


def get_model():
    """Load the embedding model once (first run downloads it)."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBED_MODEL)
    return _model


def embed(texts, prefix):
    vectors = get_model().encode([prefix + t for t in texts],
                                 normalize_embeddings=True,
                                 show_progress_bar=False)
    return vectors.tolist()


def case_document(record):
    """The text that gets embedded: equipment + the way the problem was described."""
    return f"{record['equipment_type']}. {record['complaint']}"


def record_to_metadata(record):
    """ChromaDB metadata must be str/int/float/bool, so lists are stored as JSON."""
    return {
        "record_id": record["record_id"],
        "date": record["date"],
        "asset_id": record["asset_id"],
        "equipment_type": record["equipment_type"],
        "location": record["location"],
        "complaint": record["complaint"],
        "root_cause": record["root_cause"],
        "fix_steps": json.dumps(record["fix_steps"], ensure_ascii=False),
        "parts_used": json.dumps(record["parts_used"], ensure_ascii=False),
        "cost_inr": int(record["cost_inr"]),
        "downtime_hours": float(record["downtime_hours"]),
        "urgency": record["urgency"],
        "safety_critical": bool(record["safety_critical"]),
        "prior_failures_on_asset": int(record.get("prior_failures_on_asset", 0)),
        "technician_notes": record.get("technician_notes", ""),
        "source": record.get("source", "seed"),
        "language": record.get("language", "en"),
    }


class KnowledgeBase:
    def __init__(self, reset=False):
        self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        if reset:
            try:
                self.client.delete_collection(COLLECTION_NAME)
            except Exception:
                pass  # collection did not exist yet
        self.collection = self.client.get_or_create_collection(
            COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    def count(self):
        return self.collection.count()

    def add_cases(self, records, batch_size=64):
        """Add (or update) cases. Also used by the technician feedback loop."""
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            docs = [case_document(r) for r in batch]
            self.collection.upsert(
                ids=[r["record_id"] for r in batch],
                documents=docs,
                embeddings=embed(docs, PASSAGE_PREFIX),
                metadatas=[record_to_metadata(r) for r in batch],
            )

    def search(self, query, k=DEFAULT_TOP_K, equipment_type=None):
        """Return the top-k most similar past cases with a similarity score (0-1)."""
        total = self.count()
        if total == 0:
            return []
        where = {"equipment_type": equipment_type} if equipment_type else None
        result = self.collection.query(
            query_embeddings=embed([query], QUERY_PREFIX),
            n_results=min(k, total),
            where=where,
        )
        cases = []
        for meta, dist in zip(result["metadatas"][0], result["distances"][0]):
            case = dict(meta)
            case["fix_steps"] = json.loads(case["fix_steps"])
            case["parts_used"] = json.loads(case["parts_used"])
            case["similarity"] = round(1 - dist, 3)
            cases.append(case)
        return cases