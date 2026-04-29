"""
JARVIS physical presence via Home Assistant + Matter protocol.
Controls lights, temperature, plugs, switches by voice.
Requires: Home Assistant instance on local network.
"""

import os
from typing import Optional

import requests as http_req


class HomeAssistantClient:
    def __init__(self):
        self.base_url = os.getenv("HA_BASE_URL", "http://homeassistant.local:8123")
        self.token = os.getenv("HA_TOKEN", "")
        self._available = False
        self._entities = {}  # entity_id -> {name, state, domain}

    def connect(self) -> bool:
        if not self.token:
            print("Home Assistant: HA_TOKEN not set in .env. Skipping.")
            return False
        try:
            resp = http_req.get(
                f"{self.base_url}/api/",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=5,
            )
            if resp.status_code == 200:
                self._available = True
                self._load_entities()
                print(f"Home Assistant: connected. {len(self._entities)} entities.")
                return True
        except Exception as e:
            print(f"Home Assistant: {e}")
        return False

    def _load_entities(self):
        try:
            resp = http_req.get(
                f"{self.base_url}/api/states",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )
            for entity in resp.json():
                entity_id = entity["entity_id"]
                self._entities[entity_id] = {
                    "name": entity["attributes"].get("friendly_name", entity_id),
                    "state": entity["state"],
                    "domain": entity_id.split(".")[0],
                }
        except Exception as e:
            print(f"HA entity load: {e}")

    def _call_service(self, domain: str, service: str, entity_id: str, data: dict = None) -> bool:
        if not self._available:
            return False
        payload = {"entity_id": entity_id}
        if data:
            payload.update(data)
        try:
            resp = http_req.post(
                f"{self.base_url}/api/services/{domain}/{service}",
                headers={"Authorization": f"Bearer {self.token}"},
                json=payload,
                timeout=5,
            )
            return resp.status_code in [200, 201]
        except Exception:
            return False

    def turn_on(self, device_name: str) -> str:
        entity_id = self._find_entity(device_name)
        if not entity_id:
            return f"Device '{device_name}' not found."
        domain = entity_id.split(".")[0]
        ok = self._call_service(domain, "turn_on", entity_id)
        return f"Turned on {device_name}." if ok else f"Failed to turn on {device_name}."

    def turn_off(self, device_name: str) -> str:
        entity_id = self._find_entity(device_name)
        if not entity_id:
            return f"Device '{device_name}' not found."
        domain = entity_id.split(".")[0]
        ok = self._call_service(domain, "turn_off", entity_id)
        return f"Turned off {device_name}." if ok else f"Failed to turn off {device_name}."

    def set_light_brightness(self, device_name: str, brightness_pct: int) -> str:
        entity_id = self._find_entity(device_name)
        if not entity_id:
            return f"Light '{device_name}' not found."
        brightness = int(brightness_pct / 100 * 255)
        ok = self._call_service("light", "turn_on", entity_id, {"brightness": brightness})
        return f"Set {device_name} to {brightness_pct}%." if ok else "Failed."

    def set_temperature(self, device_name: str, temp_c: float) -> str:
        entity_id = self._find_entity(device_name)
        if not entity_id:
            return f"Thermostat '{device_name}' not found."
        ok = self._call_service("climate", "set_temperature", entity_id, {"temperature": temp_c})
        return f"Set {device_name} to {temp_c}C." if ok else "Failed."

    def get_state(self, device_name: str) -> str:
        entity_id = self._find_entity(device_name)
        if not entity_id:
            return f"'{device_name}' not found."
        entity = self._entities.get(entity_id, {})
        return f"{device_name}: {entity.get('state', 'unknown')}"

    def list_devices(self, domain: str = None) -> str:
        if not self._entities:
            return "No devices loaded."
        entities = self._entities.items()
        if domain:
            entities = [(eid, e) for eid, e in entities if e["domain"] == domain]
        names = [e["name"] for _, e in list(entities)[:10]]
        return ", ".join(names) if names else "No devices in that category."

    def _find_entity(self, name: str) -> Optional[str]:
        name_lower = name.lower()
        # Exact match first
        for eid, e in self._entities.items():
            if e["name"].lower() == name_lower:
                return eid
        # Partial match
        for eid, e in self._entities.items():
            if name_lower in e["name"].lower() or name_lower in eid.lower():
                return eid
        return None
