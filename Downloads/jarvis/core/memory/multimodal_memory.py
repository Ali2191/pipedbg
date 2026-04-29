"""
Multimodal memory: screenshots, audio, documents + text.
All stored with semantic linkage. Retrieved together.
Based on 2026 unified embedding work.
"""

import time
import threading
import hashlib
import base64
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime
import chromadb

MM_DIR  = Path("./multimodal_memory")
MM_IMGS = MM_DIR / "screenshots"
MM_DOCS = MM_DIR / "documents"
MM_DIR.mkdir(exist_ok=True)
MM_IMGS.mkdir(exist_ok=True)
MM_DOCS.mkdir(exist_ok=True)


@dataclass
class MultimodalEntry:
    entry_id:    str
    modality:    str          # text / screenshot / audio / document
    text_content: str         # Text representation (always present)
    file_path:   str          # Path to original file (if applicable)
    context:     str          # What was happening when stored
    surprise:    float        # Surprise score from Titan
    stored_at:   str          = field(default_factory=lambda: datetime.now().isoformat())


class MultimodalMemory:
    """
    Unified memory store for all modalities.
    Text is always the semantic anchor (for embedding).
    Raw files stored alongside text for rich retrieval.
    """

    def __init__(self):
        try:
            self._db  = chromadb.PersistentClient(path=str(MM_DIR / "chroma"))
            self._col = self._db.get_or_create_collection("multimodal")
            print(f"Multimodal memory: {self._col.count()} entries")
        except Exception as e:
            print(f"Multimodal memory init: {e}")
            self._db = None
            self._col = None
        self._lock = threading.Lock()

    def store_screenshot(self, image_data, description: str,
                          context: str = "") -> str:
        """
        Store a screenshot with its semantic description.
        image_data: PIL.Image or bytes
        """
        if not self._col:
            return ""
        
        entry_id = f"img_{int(time.time() * 1000)}"
        # Save image file
        img_path = MM_IMGS / f"{entry_id}.png"
        try:
            if hasattr(image_data, 'save'):
                image_data.save(str(img_path))
            else:
                img_path.write_bytes(image_data)
        except Exception as e:
            print(f"Screenshot save: {e}")
            img_path = Path("")

        # Store text description in ChromaDB (semantic anchor)
        text = f"[SCREENSHOT] {description} | Context: {context}"
        try:
            self._col.add(
                documents=[text],
                metadatas=[{
                    "entry_id": entry_id,
                    "modality": "screenshot",
                    "file_path": str(img_path),
                    "context":   context[:200],
                    "stored_at": datetime.now().isoformat()
                }],
                ids=[entry_id]
            )
        except Exception as e:
            print(f"Screenshot store: {e}")
        return entry_id

    def store_document(self, file_path: str, text_content: str,
                        context: str = "") -> str:
        """Store a document with its extracted text."""
        if not self._col:
            return ""
        
        entry_id = f"doc_{hashlib.md5(file_path.encode()).hexdigest()[:10]}"
        text     = f"[DOCUMENT: {Path(file_path).name}] {text_content[:500]}"
        try:
            self._col.add(
                documents=[text],
                metadatas=[{
                    "entry_id": entry_id,
                    "modality": "document",
                    "file_path": file_path,
                    "context":   context[:200],
                    "stored_at": datetime.now().isoformat()
                }],
                ids=[entry_id]
            )
        except Exception as e:
            print(f"Document store: {e}")
        return entry_id

    def store_interaction(self, user_input: str, reply: str,
                           context: str = "", surprise: float = 0.0) -> str:
        """Store a text interaction (most common case)."""
        if not self._col:
            return ""
        
        entry_id = f"txt_{int(time.time() * 1000)}"
        text     = f"User: {user_input} | JARVIS: {reply[:300]}"
        try:
            self._col.add(
                documents=[text],
                metadatas=[{
                    "entry_id": entry_id,
                    "modality": "text",
                    "file_path": "",
                    "context":   context[:200],
                    "surprise":  str(surprise),
                    "stored_at": datetime.now().isoformat()
                }],
                ids=[entry_id]
            )
        except Exception as e:
            print(f"Interaction store: {e}")
        return entry_id

    def retrieve(self, query: str, n: int = 5,
                  modality_filter: Optional[str] = None) -> List[dict]:
        """
        Retrieve semantically similar entries across all modalities.
        Optionally filter by modality.
        """
        if not self._col:
            return []
        
        count = self._col.count()
        if count == 0:
            return []
        try:
            where = {"modality": modality_filter} if modality_filter else None
            kwargs = {
                "query_texts": [query],
                "n_results":   min(n, count),
                "include":     ["documents", "metadatas"]
            }
            if where:
                kwargs["where"] = where
            results  = self._col.query(**kwargs)
            entries  = []
            for doc, meta in zip(results["documents"][0],
                                  results["metadatas"][0]):
                entries.append({
                    "text":     doc,
                    "modality": meta.get("modality", "text"),
                    "file":     meta.get("file_path", ""),
                    "context":  meta.get("context", ""),
                    "stored":   meta.get("stored_at", "")[:10]
                })
            return entries
        except Exception as e:
            print(f"Multimodal retrieve: {e}")
            return []

    def retrieve_with_screenshots(self, query: str, n: int = 3) -> str:
        """Retrieve context including any related screenshots."""
        entries = self.retrieve(query, n * 2)
        parts   = []
        for e in entries[:n]:
            if e["modality"] == "screenshot":
                parts.append(f"[Screenshot from {e['stored']}]: {e['text'][:150]}")
            elif e["modality"] == "document":
                parts.append(f"[Document]: {e['text'][:150]}")
            else:
                parts.append(e["text"][:150])
        return "\n".join(parts) if parts else "No related memories found."

    def stats(self) -> str:
        if not self._col:
            return "Multimodal memory: unavailable"
        count = self._col.count()
        return f"Multimodal memory: {count} entries (text + screenshots + documents)"
