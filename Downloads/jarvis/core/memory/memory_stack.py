"""
Four-layer memory compression stack.
Based on: Second Me (Wei & Shang 2024) + our original architecture

L1 (Session/Daily):  Raw conversations → 3-line nightly digest
L2 (Tactical/Weekly): 30 L1 digests → 5-10 arc summaries
L3 (Core/Lifelong):  Stable facts promoted from L2 after repetition
L4 (Meta/Behavioral): Read-only analysis of how user thinks/communicates
"""

import json
import threading
import time
import math
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Optional
import chromadb
import uuid

DATA_DIR = Path("./memory_stack")
DATA_DIR.mkdir(exist_ok=True)


# ══ DATA STRUCTURES ═════════════════════════════════════════════════════════════════

@dataclass
class L1Digest:
    """Daily 3-line digest. Raw transcript discarded after creation."""
    date:         str
    worked_on:    str   # What I worked on today
    left_off_at:  str   # Where I left off (context for tomorrow)
    committed_to: str   # What I committed to for tomorrow
    raw_count:    int   # Number of interactions compressed
    created_at:   str   = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class L2Arc:
    """Weekly/monthly arc summary from ~30 L1 digests."""
    arc_id:      str
    period:      str   # "week of 2026-04-20" or "month of 2026-04"
    theme:       str   # Project/pattern title
    summary:     str   # 2-3 sentence arc
    l1_count:    int   # How many L1 digests compressed
    stalls:      List[str]  # Recurring sticking points
    momentum:    str   # "building", "stalled", "completed"
    created_at:  str   = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class L3Fact:
    """Stable lifelong fact. Promoted from L2 only after repeated observation."""
    fact_id:         str
    category:        str   # identity / value / preference / skill / relationship
    statement:       str   # Plain English fact
    confidence:      float  # 0.0-1.0
    observation_count: int  # Times observed before promotion
    last_confirmed:  str
    user_verified:   bool  = False  # User explicitly confirmed
    created_at:      str   = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class L4Meta:
    """Behavioral meta-layer. Read-only. Drives proactive behavior."""
    # Communication style
    preferred_response_length:  str   = "concise"  # concise/detailed/varies
    preferred_explanation_style: str  = "technical"  # technical/conceptual/example-first
    # Decision patterns
    decision_style:             str   = "analytical"  # analytical/intuitive/mixed
    risk_tolerance:             str   = "medium"
    # Epistemic habits
    skepticism_level:           str   = "high"
    learning_style:             str   = "building"  # building/exploring/refining
    # Deflection patterns
    topic_avoidance:            List[str] = field(default_factory=list)
    # Work patterns
    peak_focus_time:            str   = "unknown"
    avg_session_length_mins:    float = 0.0
    common_stall_types:         List[str] = field(default_factory=list)
    # Communication calibration
    frustration_triggers:       List[str] = field(default_factory=list)
    motivation_style:           str   = "progress-driven"
    last_updated:               str   = field(default_factory=lambda: datetime.now().isoformat())


# ══ MEMORY STACK ENGINE ═════════════════════════════════════════════════════════════════

class MemoryStack:
    """
    Four-layer personal memory container.
    User-owned, encrypted-capable, universal LLM adapter.
    """

    def __init__(self, llm_fn):
        self.llm   = llm_fn
        self._lock = threading.Lock()

        # ChromaDB collections for each layer
        self._db    = chromadb.PersistentClient(path=str(DATA_DIR))
        self._l1    = self._db.get_or_create_collection("l1_digests")
        self._l2    = self._db.get_or_create_collection("l2_arcs")
        self._l3    = self._db.get_or_create_collection("l3_facts")
        self._l4raw = DATA_DIR / "l4_meta.json"

        # L4 in memory
        self.l4: L4Meta = self._load_l4()

        # Today's raw session buffer (discarded after L1 compression)
        self._session_buffer: List[str] = []
        self._session_start = datetime.now()

        # Hypothesis queue for calibration
        self._hypotheses: List[str] = []

        print(f"Memory Stack: L1={self._l1.count()} L2={self._l2.count()} L3={self._l3.count()}")

    def log_interaction(self, user_input: str, jarvis_response: str):
        """Add to today's session buffer (L0 equivalent)."""
        entry = f"[{datetime.now().strftime('%H:%M')}] User: {user_input}\nJARVIS: {jarvis_response}"
        with self._lock:
            self._session_buffer.append(entry)
        # Auto-compress if buffer gets large
        if len(self._session_buffer) > 100:
            self._compress_session()

    def get_context_for_prompt(self) -> str:
        """
        Build context string to inject into LLM system prompt.
        Pulls from all four layers.
        """
        parts = []

        # L3 facts (most stable, highest priority)
        l3_facts = self._get_recent_l3(5)
        if l3_facts:
            parts.append("KNOWN FACTS ABOUT USER:\n" +
                         "\n".join(f"- {f}" for f in l3_facts))

        # L2 arcs (current context)
        l2_arc = self._get_latest_l2_arc()
        if l2_arc:
            parts.append(f"CURRENT PROJECT ARC: {l2_arc}")

        # L1 yesterday
        yesterday = self._get_yesterday_l1()
        if yesterday:
            parts.append(
                f"YESTERDAY:\n"
                f"  Worked on: {yesterday.worked_on}\n"
                f"  Left off at: {yesterday.left_off_at}\n"
                f"  Committed to: {yesterday.committed_to}"
            )

        # L4 behavioral calibration
        l4 = self.l4
        parts.append(
            f"BEHAVIORAL CALIBRATION:\n"
            f"  Response style: {l4.preferred_response_length} / {l4.preferred_explanation_style}\n"
            f"  Decision style: {l4.decision_style}\n"
            f"  Motivation: {l4.motivation_style}"
        )

        return "\n\n".join(parts) if parts else ""

    def compress_daily(self) -> Optional[L1Digest]:
        """
        Nightly compression: session buffer → 3-line L1 digest.
        Raw transcript discarded after compression.
        """
        with self._lock:
            buffer_copy = list(self._session_buffer)
            self._session_buffer.clear()

        if not buffer_copy:
            return None

        context = "\n".join(buffer_copy[-50:])  # Last 50 interactions
        prompt  = (
            f"Based on today's conversations, create a 3-line digest.\n"
            f"Return ONLY valid JSON with these exact keys:\n"
            f'{"worked_on": "one sentence", "left_off_at": "one sentence", "committed_to": "one sentence"}\n\n'
            f"Conversations:\n{context[:3000]}"
        )
        try:
            result = self.llm(prompt, max_tokens=200, temperature=0.2)
            import re
            result = re.sub(r'^```json|^```|```$', '', result, flags=re.MULTILINE).strip()
            data   = json.loads(result)
            digest = L1Digest(
                date         = datetime.now().strftime("%Y-%m-%d"),
                worked_on    = data.get("worked_on", "Unknown"),
                left_off_at  = data.get("left_off_at", "Unknown"),
                committed_to = data.get("committed_to", "Unknown"),
                raw_count    = len(buffer_copy)
            )
            # Save to L1
            self._l1.add(
                documents=[json.dumps(asdict(digest))],
                metadatas=[{"date": digest.date, "ts": digest.created_at}],
                ids=[str(uuid.uuid4())]
            )
            print(f"L1 digest created for {digest.date}: {digest.worked_on[:50]}")

            # Trigger L2 synthesis if we have enough L1 digests
            if self._l1.count() % 7 == 0:  # Every 7 days
                threading.Thread(target=self.synthesize_l2, daemon=True).start()

            return digest
        except Exception as e:
            print(f"L1 compression error: {e}")
            return None

    def synthesize_l2(self):
        """Weekly: compress L1 digests into arc summaries."""
        c = self._l1.count()
        if c < 3:
            return
        recent = self._l1.get(limit=min(30, c))
        docs   = recent["documents"]
        combined = "\n".join(docs)
        prompt = (
            "From these daily digests, identify 3-5 project arcs or behavioral patterns.\n"
            "Return JSON list: [{\"theme\": str, \"summary\": str, \"momentum\": \"building|stalled|completed\", \"stalls\": [str]}]\n\n"
            f"Digests:\n{combined[:3000]}"
        )
        try:
            result  = self.llm(prompt, max_tokens=400, temperature=0.2)
            import re
            result  = re.sub(r'^```json|^```|```$', '', result, flags=re.MULTILINE).strip()
            arcs    = json.loads(result)
            for arc_data in arcs:
                arc = L2Arc(
                    arc_id   = str(uuid.uuid4()),
                    period   = f"week of {datetime.now().strftime('%Y-%m-%d')}",
                    theme    = arc_data.get("theme", "Unknown"),
                    summary  = arc_data.get("summary", ""),
                    l1_count = c,
                    stalls   = arc_data.get("stalls", []),
                    momentum = arc_data.get("momentum", "building")
                )
                self._l2.add(
                    documents=[json.dumps(asdict(arc))],
                    metadatas=[{"theme": arc.theme, "ts": arc.created_at}],
                    ids=[arc.arc_id]
                )
            # Try to promote L3 facts
            threading.Thread(target=self.promote_l3, daemon=True).start()
            print(f"L2 synthesis: {len(arcs)} arcs created")
        except Exception as e:
            print(f"L2 synthesis error: {e}")

    def promote_l3(self):
        """Promote stable patterns from L2 to L3 facts."""
        c = self._l2.count()
        if c < 2:
            return
        recent = self._l2.get(limit=min(20, c))
        docs   = recent["documents"]
        combined = "\n".join(docs)
        prompt = (
            "From these arc summaries, identify 3-5 STABLE FACTS about the user.\n"
            "Only include facts that appear repeatedly. Categories: identity/value/preference/skill/relationship\n"
            "Return JSON list: [{\"category\": str, \"statement\": str, \"confidence\": 0.0-1.0}]\n\n"
            f"Arcs:\n{combined[:2000]}"
        )
        try:
            result = self.llm(prompt, max_tokens=300, temperature=0.2)
            import re
            result = re.sub(r'^```json|^```|```$', '', result, flags=re.MULTILINE).strip()
            facts  = json.loads(result)
            for f_data in facts:
                conf = float(f_data.get("confidence", 0.5))
                if conf >= 0.7:  # Only promote high-confidence facts
                    fact = L3Fact(
                        fact_id           = str(uuid.uuid4()),
                        category          = f_data.get("category", "preference"),
                        statement         = f_data.get("statement", ""),
                        confidence        = conf,
                        observation_count = 2,
                        last_confirmed    = datetime.now().isoformat()
                    )
                    self._l3.add(
                        documents=[json.dumps(asdict(fact))],
                        metadatas=[{"category": fact.category, "ts": fact.created_at}],
                        ids=[fact.fact_id]
                    )
            print(f"L3 promotion: {len([f for f in facts if f.get('confidence',0)>=0.7])} facts promoted")
        except Exception as e:
            print(f"L3 promotion error: {e}")

    def update_l4(self, interaction_data: dict):
        """Update L4 behavioral meta from interaction signals."""
        # Slow EMA update — L4 changes slowly
        alpha = 0.05
        if interaction_data.get("frustration", False):
            trigger = interaction_data.get("topic", "unknown")
            if trigger not in self.l4.frustration_triggers:
                self.l4.frustration_triggers.append(trigger)

        if "session_length" in interaction_data:
            old = self.l4.avg_session_length_mins
            self.l4.avg_session_length_mins = old*(1-alpha) + interaction_data["session_length"]*alpha

        self.l4.last_updated = datetime.now().isoformat()
        self._save_l4()

    def generate_calibration_question(self) -> Optional[str]:
        """
        Generate a targeted question to resolve ambiguity in user model.
        Called occasionally to improve L3 confidence.
        """
        l3_count = self._l3.count()
        if l3_count == 0:
            return "What's your main goal with JARVIS right now?"

        # Find low-confidence facts
        all_facts = self._l3.get()
        for doc in all_facts["documents"]:
            fact_data = json.loads(doc)
            if fact_data.get("confidence", 1.0) < 0.75 and not fact_data.get("user_verified"):
                stmt = fact_data.get("statement", "")
                return f"I think {stmt} — is that right?"
        return None

    def get_behavioral_dashboard(self) -> str:
        """Return L4 behavioral analysis as readable text."""
        l4 = self.l4
        return (
            f"**Behavioral Profile (L4 Meta):**\n"
            f"- Communication: {l4.preferred_response_length}, {l4.preferred_explanation_style}\n"
            f"- Decisions: {l4.decision_style} ({l4.risk_tolerance} risk)\n"
            f"- Learning: {l4.learning_style}\n"
            f"- Motivation: {l4.motivation_style}\n"
            f"- Common stalls: {', '.join(l4.common_stall_types) or 'Not yet observed'}\n"
            f"- Avg session: {l4.avg_session_length_mins:.0f} min\n"
            f"- Last updated: {l4.last_updated[:10]}"
        )

    # ── Private helpers ────────────────────────────────────────────────────────────

    def _get_recent_l3(self, n: int) -> List[str]:
        c = self._l3.count()
        if c == 0:
            return []
        results = self._l3.get(limit=min(n, c))
        facts = []
        for doc in results["documents"]:
            data = json.loads(doc)
            facts.append(data.get("statement", ""))
        return [f for f in facts if f]

    def _get_latest_l2_arc(self) -> str:
        c = self._l2.count()
        if c == 0:
            return ""
        results = self._l2.get(limit=1)
        if results["documents"]:
            data = json.loads(results["documents"][0])
            return f"{data.get('theme','')} ({data.get('momentum','')}) — {data.get('summary','')}"
        return ""

    def _get_yesterday_l1(self) -> Optional[L1Digest]:
        c = self._l1.count()
        if c == 0:
            return None
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        results = self._l1.query(
            query_texts=["yesterday session"],
            n_results=min(3, c)
        )
        if results["documents"][0]:
            for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
                data = json.loads(doc)
                if data.get("date") == yesterday:
                    return L1Digest(**data)
        return None

    def _load_l4(self) -> L4Meta:
        if self._l4raw.exists():
            try:
                return L4Meta(**json.loads(self._l4raw.read_text()))
            except Exception:
                pass
        return L4Meta()

    def _save_l4(self):
        self._l4raw.write_text(json.dumps(asdict(self.l4), indent=2))

    def _compress_session(self):
        """Emergency compression when buffer overflows."""
        with self._lock:
            if len(self._session_buffer) > 80:
                self._session_buffer = self._session_buffer[-50:]


# ── NIGHTLY SCHEDULER ───────────────────────────────────────────────────────────────

def start_nightly_scheduler(stack: MemoryStack):
    """Runs L1 compression at midnight every day."""
    def _run():
        while True:
            now     = datetime.now()
            target  = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            wait    = (target - now).total_seconds()
            time.sleep(wait)
            print("Nightly L1 compression starting...")
            digest = stack.compress_daily()
            if digest:
                print(f"L1 done: {digest.worked_on}")
    threading.Thread(target=_run, daemon=True).start()
    print("Nightly memory scheduler started (compresses at midnight).")
