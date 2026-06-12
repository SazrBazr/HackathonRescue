import socket
import json
import time
import random
import math

UDP_IP = "127.0.0.1"
UDP_PORT = 5005

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
print(f"Mock Hardware Active. Blasting telemetry to UDP port {UDP_PORT}...")

# ---------------------------------------------------------------------------
# ANCHOR LAYOUT (known positions that measure distance to each bracelet).
#  - MASTER beacon = origin (0,0,0). Everything is relative to it.
#  - 3 more ground beacons at the corners of a 100m x 100m site (all z=0, so
#    coplanar -> they alone can't find depth).
#  - 2 drones hover above and WANDER -> they make depth (Z) solvable.
# ---------------------------------------------------------------------------
GROUND_BEACONS = [
    {"beacon_id": "BCN-MASTER", "x": 0.0,   "y": 0.0,   "z": 0.0, "is_master": True},
    {"beacon_id": "BCN-NE",     "x": 100.0, "y": 0.0,   "z": 0.0, "is_master": False},
    {"beacon_id": "BCN-NW",     "x": 0.0,   "y": 100.0, "z": 0.0, "is_master": False},
    {"beacon_id": "BCN-SE",     "x": 100.0, "y": 100.0, "z": 0.0, "is_master": False},
]

drones = [{"home": (30.0, 30.0, 55.0)}, {"home": (70.0, 60.0, 50.0)}]

# ---------------------------------------------------------------------------
# GROUND TRUTH: where victims actually are. The system never sees this -- it
# only sees distances and must rediscover the positions. z<0 = buried.
# Spread across the whole 100x100 site so the map isn't clustered.
# ---------------------------------------------------------------------------
victims = [
    {"victim_id": "7a8b9c2d-4e5f", "base_bpm": 120, "base_spo2": 86, "true": (12.0, 85.0, -5.5)},
    {"victim_id": "9f8e7d6c-5b4a", "base_bpm": 70,  "base_spo2": 96, "true": (78.0, 20.0, -0.5)},
    {"victim_id": "1a2b3c4d-5e6f", "base_bpm": 135, "base_spo2": 97, "true": (45.0, 50.0, -2.5)},
    {"victim_id": "5c6d7e8f-9a0b", "base_bpm": 55,  "base_spo2": 92, "true": (90.0, 90.0, -1.0)},
    {"victim_id": "2e3f4a5b-6c7d", "base_bpm": 110, "base_spo2": 88, "true": (20.0, 30.0, -4.0)},
    {"victim_id": "8d9e0f1a-2b3c", "base_bpm": 145, "base_spo2": 90, "true": (62.0, 78.0, -3.0)},
    {"victim_id": "3a4b5c6d-7e8f", "base_bpm": 80,  "base_spo2": 99, "true": (35.0, 12.0, -0.8)},
    {"victim_id": "00a1b2c3-dead", "base_bpm": 0,   "base_spo2": 0,  "true": (80.0, 80.0, -3.5)}
]

# Rescue teams. Each carries a small "behaviour state" so they move REALISTICALLY:
# they pick a state (idle or walking) + a facing heading, hold it for ~4 seconds,
# then re-randomize. No more frame-by-frame jitter.
rescuers = [
    {"rescuer_id": "Rescuer-1", "team": "North", "x": 18.0, "y": 22.0},
    {"rescuer_id": "Rescuer-2", "team": "North", "x": 55.0, "y": 60.0},
    {"rescuer_id": "Rescuer-3", "team": "South", "x": 80.0, "y": 35.0},
]
for r in rescuers:
    r["state"] = "idle"
    r["heading"] = random.uniform(0, 360)
    r["hold"] = 0

HOLD_CYCLES = 2          # 2 cycles * 2s = ~4 seconds per stable state
WALK_STEP_M = 1.2        # slow, human-paced movement per cycle

RANGE_NOISE_M = 0.5


def distance(ax, ay, az, bx, by, bz):
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)


def current_drone_positions():
    out = []
    for d in drones:
        hx, hy, hz = d["home"]
        out.append({"x": hx + random.uniform(-5, 5),
                    "y": hy + random.uniform(-5, 5),
                    "z": hz + random.uniform(-3, 3),
                    "is_master": False})
    return out


try:
    while True:
        payload_victims = []
        payload_rescuers = []

        drone_anchors = current_drone_positions()
        all_anchor_positions = GROUND_BEACONS + drone_anchors

        for v in victims:
            tx, ty, tz = v["true"]
            if v["base_bpm"] == 0:
                bpm = 0
                spo2 = 0
                crisis = False
            else:

                bpm = max(40, min(200, v["base_bpm"] + random.randint(-4, 4)))
                spo2 = max(50, min(100, v["base_spo2"] + random.randint(-2, 1)))

                crisis = random.random() < 0.05
                if crisis:
                    bpm += 25
                    spo2 -= 8

            anchors = []
            for a in all_anchor_positions:
                true_r = distance(a["x"], a["y"], a["z"], tx, ty, tz)
                noisy_r = true_r + random.gauss(0, RANGE_NOISE_M)
                anchors.append({"x": round(a["x"], 2), "y": round(a["y"], 2),
                                "z": round(a["z"], 2), "range": round(max(0.0, noisy_r), 2),
                                "is_master": a["is_master"]})

            payload_victims.append({"victim_id": v["victim_id"], "bpm": bpm, "spo2": spo2,
                                    "hemorrhage": crisis, "anchors": anchors})

        for r in rescuers:
            # Hold the current state for ~4s, then re-randomize (stable, logical).
            if r["hold"] <= 0:
                r["state"] = random.choice(["idle", "walking", "walking"])  # bias to moving
                r["heading"] = random.uniform(0, 360)
                r["hold"] = HOLD_CYCLES
            r["hold"] -= 1

            if r["state"] == "walking":
                rad = math.radians(r["heading"])
                r["x"] = max(0.0, min(100.0, r["x"] + math.cos(rad) * WALK_STEP_M))
                r["y"] = max(0.0, min(100.0, r["y"] + math.sin(rad) * WALK_STEP_M))

            payload_rescuers.append({
                "rescuer_id": r["rescuer_id"], "team": r["team"],
                "status": "MOVING" if r["state"] == "walking" else "STANDBY",
                "x": round(r["x"], 2), "y": round(r["y"], 2),
                "heading": round(r["heading"], 1)
            })

        # Beacons are static & known; ship their positions so the map can draw them.
        payload_beacons = [{"beacon_id": b["beacon_id"], "x": b["x"], "y": b["y"],
                            "is_master": b["is_master"]} for b in GROUND_BEACONS]

        final_payload = {"victims": payload_victims, "rescuers": payload_rescuers,
                         "beacons": payload_beacons}
        message = json.dumps(final_payload).encode("utf-8")
        sock.sendto(message, (UDP_IP, UDP_PORT))
        print(f"Dispatched map state ({len(message)} bytes)")
        time.sleep(2.0)

except KeyboardInterrupt:
    print("\nMock engine stopped.")
    sock.close()
