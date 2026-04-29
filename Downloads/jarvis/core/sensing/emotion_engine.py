"""
Fuses text + voice + face emotion signals into unified state.
"""
import threading
from dataclasses import dataclass
from collections import deque

@dataclass
class Emotion:
    dominant:   str   = "neutral"
    valence:    float = 0.5    # 0=negative, 1=positive
    arousal:    float = 0.5    # 0=calm, 1=activated
    confidence: float = 0.0
    source:     str   = "text"

class EmotionEngine:
    def __init__(self):
        self.current  = Emotion()
        self._lock    = threading.Lock()
        self._history = deque(maxlen=10)
        self._weights = {"text":.30,"voice":.50,"face":.20}
        self._alpha   = 0.28

    def from_text(self, text: str) -> Emotion:
        t = text.lower()
        if any(w in t for w in ["stuck","not working","frustrated","broken","error","help","why"]):
            ev = Emotion("frustrated",.20,.72,.65,"text")
        elif any(w in t for w in ["amazing","great","awesome","love","perfect","done","happy","excited","joy","glad","good"]):
            ev = Emotion("excited",.88,.80,.70,"text")
        elif any(w in t for w in ["tired","sleepy","exhausted","boring"]):
            ev = Emotion("tired",.40,.22,.60,"text")
        else:
            ev = Emotion("neutral",.50,.50,.40,"text")
        self._fuse(ev); return self.current

    def from_voice(self, audio_file: str) -> Emotion:
        try:
            import librosa, numpy as np
            y,sr = librosa.load(audio_file,sr=None)
            p,m  = librosa.piptrack(y=y,sr=sr)
            pv   = p[m>np.median(m)]
            ap   = float(np.mean(pv)) if len(pv)>0 else 0
            rms  = float(np.mean(librosa.feature.rms(y=y)))
            if ap>200 and rms>.05:   ev = Emotion("excited",.82,.85,.75,"voice")
            elif ap>180 and rms>.03: ev = Emotion("frustrated",.22,.70,.70,"voice")
            elif rms<.01:            ev = Emotion("tired",.40,.20,.60,"voice")
            else:                    ev = Emotion("neutral",.50,.50,.50,"voice")
            self._fuse(ev)
        except Exception as e:
            print(f"Voice emotion: {e}")
        return self.current

    def _fuse(self, sig: Emotion):
        with self._lock:
            if sig.source == "text" and sig.confidence >= 0.65:
                self.current = Emotion(sig.dominant, sig.valence, sig.arousal, sig.confidence, "fused")
                self._history.append(self.current)
                return
            w  = self._weights.get(sig.source,.3)
            a  = self._alpha * w
            fv = self.current.valence*(1-a)+sig.valence*a
            fa = self.current.arousal*(1-a)+sig.arousal*a
            if   fv<.35 and fa>.6: dom="frustrated"
            elif fv>.70 and fa>.7: dom="excited"
            elif fa<.28:           dom="tired"
            elif fv>.62:           dom="focused"
            else:                  dom="neutral"
            self.current = Emotion(dom,fv,fa,sig.confidence*.7+self.current.confidence*.3,"fused")
            self._history.append(self.current)

    def get(self) -> Emotion: return self.current
    def frustration(self) -> float:
        if not self._history: return 0.0
        return sum(1 for e in self._history if e.dominant=="frustrated")/len(self._history)
