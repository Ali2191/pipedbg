"""
Titan-style surprise-based memory prioritization.
Based on: Behrouz et al., Google DeepMind 2024 (Titans paper)

Core insight from paper:
  Memory priority = surprise score
  Surprise score = how much does this input violate our current world model?
  Measured as: gradient magnitude of memory module loss on new input

Application-layer implementation:
  We approximate the gradient magnitude using semantic distance
  between new input and existing memory centroids.
  High distance = high surprise = high priority = stored prominently.

The Titans paper also introduced:
  - Persistent memory: task-independent parameters (our slow CMS tier)
  - Contextual memory: input-dependent (our fast CMS tier)
  - Long-term memory: surprise-gated (THIS module)
"""

import json
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

import chromadb

TITAN_DIR = Path("./memory_stack/titan")
TITAN_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TitanMemory:
    memory_id: str
    content: str
    surprise_score: float  # 0.0 = expected, 1.0 = completely surprising
    context: str  # What was happening when this was stored
    stored_at: str
    retrieval_count: int = 0
    last_retrieved: str = ""


class TitanMemoryModule:
    """
    Long-term memory with surprise-based prioritization.
    High-surprise memories are stored more prominently and
    retrieved first when context is ambiguous.
    """

    def __init__(self):
        self._db = chromadb.PersistentClient(path=str(TITAN_DIR))
        self._col = self._db.get_or_create_collection(
            name="titan_memories", metadata={"hnsw:space": "cosine"}
        )
        self._lock = threading.Lock()
        # Running statistics for surprise normalization
        self._sim_history = deque(maxlen=100)
        self._avg_sim = 0.5
        self._loaded_count = self._col.count()
        print(f"Titan Memory: {self._loaded_count} long-term memories loaded")

    def compute_surprise(self, new_content: str) -> float:
        """
        Estimate surprise score for new content.

        Method: query existing memories for semantic similarity.
        High similarity = expected (low surprise)
        Low similarity  = unexpected (high surprise)

        We approximate Titans' gradient magnitude using
        (1 - max_cosine_similarity_to_existing_memories).
        """
        count = self._col.count()
        if count == 0:
            return 0.8  # First memory is always somewhat surprising

        try:
            results = self._col.query(
                query_texts=[new_content], n_results=min(3, count), include=["distances"]
            )
            distances = results.get("distances", [[]])[0]
            if not distances:
                return 0.8

            # Cosine distance in ChromaDB: 0 = identical, 2 = opposite
            # Normalize to 0-1 similarity
            min_dist = min(distances)
            similarity = 1.0 - (min_dist / 2.0)
            surprise = 1.0 - similarity

            # Update running average for adaptive thresholding
            self._sim_history.append(similarity)
            self._avg_sim = sum(self._sim_history) / len(self._sim_history)

            # Normalize surprise relative to recent history
            if similarity < self._avg_sim - 0.2:
                surprise = min(1.0, surprise * 1.5)  # Amplify true outliers

            return round(max(0.0, min(1.0, surprise)), 3)
        except Exception as e:
            print(f"Titan surprise error: {e}")
            return 0.5

    def store(
        self, content: str, context: str = "", surprise_threshold: float = 0.3
    ) -> Optional[TitanMemory]:
        """
        Store a memory if its surprise score exceeds threshold.
        Low-surprise = boring/expected = don't store
        High-surprise = novel/important = store
        """
        surprise = self.compute_surprise(content)

        if surprise < surprise_threshold:
            return None  # Expected — not worth storing

        mem = TitanMemory(
            memory_id=str(uuid.uuid4()),
            content=content,
            surprise_score=surprise,
            context=context,
            stored_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )

        with self._lock:
            self._col.add(
                documents=[content],
                metadatas=[
                    {
                        "memory_id": mem.memory_id,
                        "surprise_score": str(surprise),
                        "context": context[:200],
                        "stored_at": mem.stored_at,
                        "retrieval_count": "0",
                    }
                ],
                ids=[mem.memory_id],
            )

        print(f"Titan: stored memory (surprise={surprise:.2f}): " f"{content[:60]}")
        return mem

    def retrieve(
        self, query: str, n: int = 5, surprise_weight: float = 0.4
    ) -> List[str]:
        """
        Retrieve memories weighted by surprise score + semantic relevance.
        surprise_weight: 0.0 = pure semantic, 1.0 = pure surprise-based
        """
        count = self._col.count()
        if count == 0:
            return []

        try:
            results = self._col.query(
                query_texts=[query],
                n_results=min(n * 2, count),  # Get extra, then re-rank
                include=["documents", "metadatas", "distances"],
            )
            docs = results["documents"][0]
            metas = results["metadatas"][0]
            distances = results["distances"][0]

            # Re-rank: combined score = semantic + surprise bonus
            scored = []
            for doc, meta, dist in zip(docs, metas, distances):
                semantic_score = 1.0 - (dist / 2.0)
                surprise_score = float(meta.get("surprise_score", "0.5"))
                combined = (1 - surprise_weight) * semantic_score + surprise_weight * surprise_score
                scored.append((combined, doc, meta))

            scored.sort(key=lambda x: x[0], reverse=True)

            # Update retrieval counts
            top_metas = [s[2] for s in scored[:n]]
            for meta in top_metas:
                mid = meta.get("memory_id", "")
                if mid:
                    try:
                        count_ = int(meta.get("retrieval_count", "0")) + 1
                        self._col.update(
                            ids=[mid],
                            metadatas=[
                                {
                                    **meta,
                                    "retrieval_count": str(count_),
                                    "last_retrieved": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                }
                            ],
                        )
                    except Exception:
                        pass

            return [s[1] for s in scored[:n]]

        except Exception as e:
            print(f"Titan retrieve error: {e}")
            return []

    def get_high_surprise_memories(self, threshold: float = 0.7, n: int = 5) -> List[str]:
        """Get most surprising memories — for morning briefings."""
        count = self._col.count()
        if count == 0:
            return []
        try:
            all_metas = self._col.get(limit=min(50, count))
            pairs = list(zip(all_metas["documents"], all_metas["metadatas"]))
            high = [
                (float(m.get("surprise_score", "0")), d)
                for d, m in pairs
                if float(m.get("surprise_score", "0")) >= threshold
            ]
            high.sort(key=lambda x: x[0], reverse=True)
            return [d for _, d in high[:n]]
        except Exception as e:
            print(f"Titan high-surprise: {e}")
            return []

    def get_stats(self) -> str:
        count = self._col.count()
        return (
            f"Titan Memory: {count} long-term memories | "
            f"avg similarity baseline: {self._avg_sim:.2f} | "
            f"threshold adapts dynamically"
        )
