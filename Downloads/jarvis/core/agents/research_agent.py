"""
Autonomous overnight research. Runs during idle hours.
Saves structured Markdown digests to research_output/
"""
import threading, time, json, re
from pathlib import Path
from datetime import datetime
from queue import PriorityQueue, Empty

OUT = Path("./research_output")
OUT.mkdir(exist_ok=True)
IDLE_HOURS = list(range(0,7))+list(range(23,24))

class ResearchAgent:
    def __init__(self, llm_fn, search_fn):
        self.llm    = llm_fn
        self.search = search_fn
        self.q      = PriorityQueue()
        self.run    = False
        self._brief = []

    def schedule(self, topic: str, priority=5, depth="standard"):
        self.q.put((priority,{"topic":topic,"depth":depth}))
        print(f"Research queued: {topic}")

    def start(self):
        self.run = True
        threading.Thread(target=self._worker,daemon=True).start()

    def _worker(self):
        while self.run:
            if datetime.now().hour not in IDLE_HOURS:
                time.sleep(300); continue
            try:
                p,task = self.q.get(timeout=60)
                result = self._research(task["topic"],task["depth"])
                self._save(task["topic"],result)
                self._brief.append({"topic":task["topic"],"summary":result[:280]})
                self.q.task_done()
            except Empty: time.sleep(60)
            except Exception as e: print(f"Research: {e}"); time.sleep(30)

    def _research(self, topic, depth="standard"):
        n = 5 if depth=="deep" else 3
        initial = self.search(topic, n)
        fq_raw  = self.llm(f"Research topic: '{topic}'.\nFindings: {initial[:800]}\n\nGenerate 3 follow-up questions as JSON list.",max_tokens=200,temperature=.3)
        try:
            fq_raw  = re.sub(r'^```json|^```|```$','',fq_raw,flags=re.MULTILINE).strip()
            followups = json.loads(fq_raw)
        except Exception: followups=[]
        extras = []
        for fq in followups[:2]:
            extras.append(f"**{fq}**\n{self.search(fq,2)}")
        combined = initial+"\n\n"+"\n\n".join(extras)
        return self.llm(
            f"Write a comprehensive Markdown research report on '{topic}'.\n"
            f"Use headers: Overview, Key Findings, Current State, Implications, Open Questions.\n"
            f"Aim for 500-800 words.\n\nSources:\n{combined[:4000]}",
            max_tokens=900,temperature=.3
        )

    def _save(self, topic, content):
        ts   = datetime.now().strftime("%Y-%m-%d_%H-%M")
        slug = topic.replace(" ","_").lower()[:40]
        p    = OUT/f"{ts}_{slug}.md"
        p.write_text(f"# Research: {topic}\n*{datetime.now().strftime('%Y-%m-%d %H:%M')} — JARVIS Research Agent*\n\n{content}")
        print(f"Digest saved: {p}")

    def briefing(self) -> str:
        if not self._brief: return "No overnight research completed."
        out = "**Overnight Research:**\n\n"
        for r in self._brief: out+=f"- **{r['topic']}**: {r['summary']}\n\n"
        self._brief.clear()
        return out
