from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
from datetime import datetime, timezone
import threading
import math

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

_state_lock = threading.Lock()

NO_SIGNAL_TIMEOUT_S = 12

victim_registry: Dict[str, Dict[str, Any]] = {}
# assignments: rescuer_id -> {"victim_id": str, "confirmed": bool}
assignments: Dict[str, Dict[str, Any]] = {}

latest_state: Dict[str, Any] = {"victims": [], "rescuers": [], "beacons": []}


# ---------------------------------------------------------------------------
# TERRAIN: a static "drone surface scan" of the rubble, generated once at
# startup. A grid of elevations (meters of debris piled above ground) over the
# 100m x 100m site. Stands in for the drone's processed heightmap.
# ---------------------------------------------------------------------------
TERRAIN_GRID = 20          # 20 x 20 cells over the site
TERRAIN_SITE = 100.0       # meters

def _build_terrain():
    cells = []
    for j in range(TERRAIN_GRID):
        row = []
        for i in range(TERRAIN_GRID):
            x = i / (TERRAIN_GRID - 1) * TERRAIN_SITE
            y = j / (TERRAIN_GRID - 1) * TERRAIN_SITE
            # Smooth pseudo-rubble: a couple of mounds via sums of sines.
            h = (2.4 * math.sin(x / 18.0) * math.cos(y / 22.0)
                 + 1.6 * math.sin(x / 9.0 + 1.3) * math.sin(y / 12.0)
                 + 2.0)
            row.append(round(max(0.0, h), 2))
        cells.append(row)
    return {"grid": TERRAIN_GRID, "site": TERRAIN_SITE, "cells": cells}

TERRAIN = _build_terrain()


# ---------------------------------------------------------------------------
# Input models
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
    x: float
    y: float
    heading: float = 0.0          # degrees, which way they're facing (simulated)


class BeaconInput(BaseModel):
    beacon_id: str
    x: float
    y: float
    is_master: bool = False


class FullMapState(BaseModel):
    victims: List[VictimInput]
    rescuers: List[RescuerInput]
    beacons: List[BeaconInput] = []


class AssignRequest(BaseModel):
    rescuer_id: str
    victim_id: str


class ConfirmRequest(BaseModel):
    rescuer_id: str


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
            record = {
                "victim_id": item.victim_id,
                "triage_status": status,
                "metrics": {
                    "current_bpm": item.bpm,
                    "current_spo2": item.spo2,
                    "calculated_depth_meters": depth,
                    "hr_baseline_ratio": round(item.bpm / 80.0, 2),
                    "access_difficulty": round(min(depth / 5.0, 1.0), 2),
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
            # attach assignment info (who is coming for this victim)
            assigned_to = None
            for rid, a in assignments.items():
                if a["victim_id"] == vid:
                    assigned_to = {"rescuer_id": rid, "confirmed": a["confirmed"]}
                    break
            rec["assigned_to"] = assigned_to
            output_victims.append(rec)

        output_victims.sort(key=lambda v: (v["_internal_weight"], -v["_spo2"]), reverse=True)
        for v in output_victims:
            v.pop("_internal_weight", None)
            v.pop("_spo2", None)

        latest_state = {
            "victims": output_victims,
            "rescuers": [r.model_dump() for r in payload.rescuers],
            "beacons": [b.model_dump() for b in payload.beacons],
            "assignments": assignments,
            "terrain": TERRAIN
        }
    return latest_state


@app.post("/assign")
def assign(req: AssignRequest) -> Dict[str, Any]:
    with _state_lock:
        assignments[req.rescuer_id] = {"victim_id": req.victim_id, "confirmed": False}
    return {"ok": True, "rescuer_id": req.rescuer_id, "victim_id": req.victim_id}


@app.post("/confirm")
def confirm(req: ConfirmRequest) -> Dict[str, Any]:
    with _state_lock:
        if req.rescuer_id in assignments:
            assignments[req.rescuer_id]["confirmed"] = True
            return {"ok": True}
    return {"ok": False, "error": "no assignment for that rescuer"}


@app.get("/state")
def get_current_state() -> Dict[str, Any]:
    with _state_lock:
        return latest_state
