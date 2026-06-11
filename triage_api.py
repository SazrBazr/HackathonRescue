from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any
from pydantic import BaseModel
from datetime import datetime, timezone
import threading

app = FastAPI()

# Let the browser dashboard fetch this API cross-origin (else it shows Offline).
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_state_lock = threading.Lock()

# Seconds of silence before a victim is treated as "lost signal".
NO_SIGNAL_TIMEOUT_S = 12

# Registry: last known record of every victim ever seen + when last heard from.
victim_registry: Dict[str, Dict[str, Any]] = {}

latest_state: Dict[str, Any] = {"victims": [], "rescuers": [], "beacons": []}


# ---------------------------------------------------------------------------
# Input models. The C++ node sends a COMPUTED (x,y,z) per victim.
# ---------------------------------------------------------------------------
class VictimInput(BaseModel):
    victim_id: str
    bpm: int
    spo2: int
    hemorrhage: bool
    x: float
    y: float
    z: float
    solved: bool = True


class RescuerInput(BaseModel):
    rescuer_id: str
    team: str
    status: str
    x: int
    y: int


class BeaconInput(BaseModel):
    beacon_id: str
    x: float
    y: float
    is_master: bool = False


class FullMapState(BaseModel):
    victims: List[VictimInput]
    rescuers: List[RescuerInput]
    beacons: List[BeaconInput] = []


# ---------------------------------------------------------------------------
# Triage scoring (medical logic locked).
# ---------------------------------------------------------------------------
def calculate_triage(bpm: int, spo2: int, hemorrhage: bool):
    score = 0
    possible_panic = False

    if spo2 < 85:
        score += 6
    elif spo2 < 90:
        score += 4
    elif spo2 < 94:
        score += 2

    if bpm >= 150:
        score += 4
    elif bpm >= 130:
        if spo2 >= 95 and not hemorrhage:
            possible_panic = True
            score += 1
        else:
            score += 3
    elif bpm < 50:
        score += 3

    if hemorrhage:
        score += 5

    if hemorrhage or spo2 < 85 or score >= 7:
        status = "CRITICAL"
    elif score >= 4:
        status = "URGENT"
    else:
        status = "STABLE"

    return status, score, possible_panic


@app.post("/telemetry/sync")
def process_telemetry(payload: FullMapState) -> Dict[str, Any]:
    global latest_state

    with _state_lock:
        now = datetime.now(timezone.utc)

        for item in payload.victims:
            status, score, is_panic = calculate_triage(item.bpm, item.spo2, item.hemorrhage)
            depth = round(max(0.0, -item.z), 2)
            access_difficulty = round(min(depth / 5.0, 1.0), 2)

            record = {
                "victim_id": item.victim_id,
                "triage_status": status,
                "metrics": {
                    "current_bpm": item.bpm,
                    "current_spo2": item.spo2,
                    "calculated_depth_meters": depth,
                    "hr_baseline_ratio": round(item.bpm / 80.0, 2),
                    "access_difficulty": access_difficulty,
                    "seconds_since_seen": 0,
                    "last_vitals_update": now.isoformat()
                },
                "flags": {
                    "active_hemorrhage": item.hemorrhage,
                    "possible_panic": is_panic,
                    "position_solved": item.solved,
                    "manual_override": False
                },
                "location": {
                    "x_coord": round(item.x, 2),
                    "y_coord": round(item.y, 2),
                    "z_coord": round(item.z, 2),
                    "confidence_radius_cells": 1.0
                },
                "_internal_weight": score,
                "_spo2": item.spo2
            }
            victim_registry[item.victim_id] = {"record": record, "last_seen": now}

        output_victims = []
        for vid, entry in victim_registry.items():
            rec = dict(entry["record"])
            rec["metrics"] = dict(rec["metrics"])
            rec["flags"] = dict(rec["flags"])
            silence = (now - entry["last_seen"]).total_seconds()
            rec["metrics"]["seconds_since_seen"] = int(silence)
            if silence > NO_SIGNAL_TIMEOUT_S:
                rec["triage_status"] = "NO_SIGNAL"
                rec["_internal_weight"] = -1
            output_victims.append(rec)

        output_victims.sort(key=lambda v: (v["_internal_weight"], -v["_spo2"]), reverse=True)
        for v in output_victims:
            v.pop("_internal_weight", None)
            v.pop("_spo2", None)

        final_state = {
            "victims": output_victims,
            "rescuers": [r.model_dump() for r in payload.rescuers],
            "beacons": [b.model_dump() for b in payload.beacons]
        }
        latest_state = final_state

    return final_state


@app.get("/state")
def get_current_state() -> Dict[str, Any]:
    with _state_lock:
        return latest_state
