"""
Tracks relationships mentioned by the user.
Builds a social context graph: who, how often, sentiment.
"""

import json
import threading
from pathlib import Path
from datetime import datetime
from collections import defaultdict


SOCIAL_FILE = Path("./memory_stack/social_graph.json")


class SocialMemory:
    def __init__(self, llm_fn):
        self.llm = llm_fn
        self._lock = threading.Lock()
        self.graph = self._load()

    def _load(self) -> dict:
        if SOCIAL_FILE.exists():
            try:
                return json.loads(SOCIAL_FILE.read_text())
            except Exception:
                pass
        return {}  # {name: {mentions: int, sentiment: str, context: [str], last_mentioned: str}}

    def _save(self):
        SOCIAL_FILE.parent.mkdir(exist_ok=True)
        SOCIAL_FILE.write_text(json.dumps(self.graph, indent=2))

    def process_interaction(self, user_input: str):
        """Extract and store social context from user input."""
        # Quick name detection
        import re
        # Look for common social patterns
        patterns = [
            r'(?:told|asked|said|texted|called|messaged|from|with|to) ([A-Z][a-z]+)',
            r'([A-Z][a-z]+) (?:said|told|asked|replied|sent)',
            r'my (?:friend|colleague|brother|sister|mom|dad|teacher|boss) ([A-Z][a-z]+)',
        ]
        mentioned = set()
        for pattern in patterns:
            matches = re.findall(pattern, user_input)
            mentioned.update(matches)

        if not mentioned:
            return

        # Analyze sentiment toward each person
        for name in mentioned:
            self._update_person(name, user_input)

    def _update_person(self, name: str, context: str):
        # Quick sentiment from context words
        pos_words = ["thanks", "great", "amazing", "helped", "appreciate", "love", "good"]
        neg_words = ["annoyed", "frustrated", "angry", "upset", "problem", "ignored"]
        t = context.lower()
        sentiment = "positive" if any(w in t for w in pos_words) else \
                    "negative" if any(w in t for w in neg_words) else "neutral"

        with self._lock:
            if name not in self.graph:
                self.graph[name] = {
                    "mentions": 0,
                    "sentiment": sentiment,
                    "context": [],
                    "last_mentioned": "",
                }
            entry = self.graph[name]
            entry["mentions"] += 1
            entry["last_mentioned"] = datetime.now().strftime("%Y-%m-%d")
            # Running average sentiment
            sentiments = entry.get("sentiment_history", []) + [sentiment]
            sentiments = sentiments[-10:]  # Keep last 10
            entry["sentiment_history"] = sentiments
            pos = sentiments.count("positive")
            neg = sentiments.count("negative")
            entry["sentiment"] = "positive" if pos > neg else \
                                 "negative" if neg > pos else "neutral"
            # Store context snippet
            entry["context"] = (entry["context"] + [context[:100]])[-5:]
            self._save()

    def get_person(self, name: str) -> str:
        with self._lock:
            p = self.graph.get(name)
        if not p:
            return f"No memory of {name}."
        return (
            f"{name}: mentioned {p['mentions']} times, "
            f"sentiment: {p['sentiment']}, "
            f"last mentioned: {p.get('last_mentioned', 'unknown')}"
        )

    def get_social_summary(self, top_n=5) -> str:
        with self._lock:
            people = sorted(
                self.graph.items(), key=lambda x: x[1]["mentions"], reverse=True
            )[:top_n]
        if not people:
            return "No social context built yet."
        lines = []
        for name, data in people:
            lines.append(f"{name} (x{data['mentions']}, {data['sentiment']})")
        return "People in your life: " + ", ".join(lines)

    def inject_into_context(self, user_input: str) -> str:
        """If user mentions a known person, inject their context."""
        import re

        words = re.findall(r"[A-Z][a-z]+", user_input)
        parts = []
        with self._lock:
            for word in words:
                if word in self.graph:
                    p = self.graph[word]
                    parts.append(
                        f"{word}: {p['mentions']} mentions, {p['sentiment']} relationship"
                    )
        return "\n".join(parts) if parts else ""
