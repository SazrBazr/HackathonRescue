from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any
from pydantic import BaseModel
from datetime import datetime, timezone
import threading

app = FastAPI()

# Allow the browser dashboard (served from a file or any local port) to fetch
# this API. Without this, browsers block the cross-origin request and the
# whole frontend goes "Offline". Wide-open is fine for a hackathon/LAN demo.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# A single lock guards the shared mutable state.
_state_lock = threading.Lock()

# How many seconds of silence before a victim is treated as "lost signal".
# Tune this: too low = false alarms from a skipped packet; too high = slow to
# notice a real dropout. ~2-3 missed cycles is a reasonable default.
NO_SIGNAL_TIMEOUT_S = 12

# The REGISTRY: the last known full record of every victim we've ever heard
# from, keyed by victim_id, plus the wall-clock time we last heard from them.
# This is what lets a victim who goes silent stay on the map instead of
# silently disappearing. Shape: { victim_id: {"record": {...}, "last_seen": ts} }
victim_registry: Dict[str, Dict[str, Any]] = {}

# Global state store for the dashboard to poll
latest_state: Dict[str, Any] = {
    "victims": [],
    "rescuers": []
}

# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------
# NOTE: the C++ localization node now sends a COMPUTED position (x, y, z) per
# victim, calculated from beacon/drone distances via multilateration. We no
# longer receive hand-typed coords or a raw signal radius. `solved` is False
# if the anchor geometry was degenerate (e.g. no usable drone data that tick).
#
# (Signal denoising of the raw distances belongs UPSTREAM, on the per-anchor
#  ranges before the solve. That's a deliberate next step, not done here.)
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


class FullMapState(BaseModel):
    victims: List[VictimInput]
    rescuers: List[RescuerInput]


# ---------------------------------------------------------------------------
# Triage scoring  (UNCHANGED -- medical logic is locked)
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

        # --- 1. Score each freshly-heard victim and refresh the registry ---
        for item in payload.victims:
            status, score, is_panic = calculate_triage(
                item.bpm, item.spo2, item.hemorrhage
            )

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
                # internal scoring helpers, stripped before output
                "_internal_weight": score,
                "_spo2": item.spo2
            }
            victim_registry[item.victim_id] = {"record": record, "last_seen": now}

        # --- 2. Build the output from the REGISTRY (everyone ever seen) ---
        # Anyone silent past the timeout is overridden to NO_SIGNAL but keeps
        # their last known vitals and location.
        output_victims = []
        for vid, entry in victim_registry.items():
            rec = dict(entry["record"])              # shallow copy
            rec["metrics"] = dict(rec["metrics"])     # copy nested dicts we edit
            rec["flags"] = dict(rec["flags"])

            silence = (now - entry["last_seen"]).total_seconds()
            rec["metrics"]["seconds_since_seen"] = int(silence)

            if silence > NO_SIGNAL_TIMEOUT_S:
                rec["triage_status"] = "NO_SIGNAL"
                # Lost-signal victims are not medically actionable right now, so
                # they sort below everyone live (weight below any real score).
                rec["_internal_weight"] = -1
            output_victims.append(rec)

        # --- 3. Sort: live victims sickest-first; NO_SIGNAL sinks to the bottom ---
        output_victims.sort(
            key=lambda v: (v["_internal_weight"], -v["_spo2"]),
            reverse=True
        )
        for v in output_victims:
            v.pop("_internal_weight", None)
            v.pop("_spo2", None)

        final_state = {
            "victims": output_victims,
            "rescuers": [r.model_dump() for r in payload.rescuers]
        }
        latest_state = final_state

    return final_state


@app.get("/state")
def get_current_state() -> Dict[str, Any]:
    with _state_lock:
        return latest_state
