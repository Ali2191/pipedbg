"""
core/face_engine.py
Face enrollment and greeting helpers for JARVIS.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import cv2

try:
    import face_recognition
except Exception:
    face_recognition = None


FACE_DIR = Path("./known_faces")
FACE_DIR.mkdir(exist_ok=True)
FACE_DB = FACE_DIR / "faces.json"


@dataclass
class FaceRecord:
    name: str
    encoding: List[float]
    image_path: str


class FaceEngine:
    def __init__(self):
        self.records = self._load_records()

    def _load_records(self) -> List[FaceRecord]:
        if not FACE_DB.exists():
            return []
        try:
            data = json.loads(FACE_DB.read_text())
            return [FaceRecord(**item) for item in data]
        except Exception:
            return []

    def _save_records(self) -> None:
        FACE_DB.write_text(json.dumps([record.__dict__ for record in self.records], indent=2))

    def enroll_from_image(self, image_path: str, name: str) -> str:
        if face_recognition is None:
            return "Face engine unavailable: install face_recognition."
        if not os.path.exists(image_path):
            return f"Face image not found: {image_path}"
        image = face_recognition.load_image_file(image_path)
        encodings = face_recognition.face_encodings(image)
        if not encodings:
            return "No face found in the image."
        record = FaceRecord(name=name, encoding=encodings[0].tolist(), image_path=image_path)
        self.records = [r for r in self.records if r.name.lower() != name.lower()]
        self.records.append(record)
        self._save_records()
        return f"Learned face: {name}."

    def enroll_from_camera(self, name: str, camera_index: int = 0) -> str:
        frame = self._capture_frame(camera_index)
        if frame is None:
            return "Camera unavailable for face enrollment."
        if face_recognition is None:
            return "Face engine unavailable: install face_recognition."
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        encodings = face_recognition.face_encodings(rgb)
        if not encodings:
            return "No face found in the camera frame."
        face_path = FACE_DIR / f"{name.replace(' ', '_').lower()}.jpg"
        cv2.imwrite(str(face_path), frame)
        record = FaceRecord(name=name, encoding=encodings[0].tolist(), image_path=str(face_path))
        self.records = [r for r in self.records if r.name.lower() != name.lower()]
        self.records.append(record)
        self._save_records()
        return f"Learned face from camera: {name}."

    def greet_from_camera(self, camera_index: int = 0) -> str:
        frame = self._capture_frame(camera_index)
        if frame is None:
            return "Camera unavailable."
        if face_recognition is None:
            return "Face engine unavailable: install face_recognition."
        if not self.records:
            return "No faces learned yet."
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        encodings = face_recognition.face_encodings(rgb)
        if not encodings:
            return "No face detected."
        known_encodings = [record.encoding for record in self.records]
        known_names = [record.name for record in self.records]
        matches = face_recognition.compare_faces(known_encodings, encodings[0], tolerance=0.5)
        if True in matches:
            index = matches.index(True)
            return f"Welcome back, {known_names[index]}."
        return "Face detected, but I do not recognize this person yet."

    def list_faces(self) -> List[str]:
        return [record.name for record in self.records]

    def _capture_frame(self, camera_index: int):
        cap = cv2.VideoCapture(camera_index)
        try:
            for _ in range(5):
                cap.read()
            ok, frame = cap.read()
            return frame if ok else None
        finally:
            cap.release()


face_engine = FaceEngine()