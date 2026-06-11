import socket
import json
import time
import random
import math

UDP_IP = "127.0.0.1"
UDP_PORT = 5005

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
print(f"📡 Mock Hardware Active. Blasting telemetry to UDP port {UDP_PORT}...")

# ---------------------------------------------------------------------------
# THE ANCHOR LAYOUT (things with KNOWN positions that measure distance to a
# bracelet). This is the geometry the rescue team would set up in the field.
#
#   - The MASTER beacon is the origin (0,0,0). Everything is relative to it.
#   - 3 more ground beacons sit at the corners of a ~100m x 100m site. All four
#     are on the ground (z=0) -> they are COPLANAR -> they alone cannot find depth.
#   - 2 drones hover above the site. Because they are up high (different z), they
#     are what makes the vertical (Z / depth) solvable. They WANDER each tick.
# ---------------------------------------------------------------------------
GROUND_BEACONS = [
    {"x": 0.0,   "y": 0.0,   "z": 0.0, "is_master": True},   # MASTER = origin
    {"x": 100.0, "y": 0.0,   "z": 0.0, "is_master": False},
    {"x": 0.0,   "y": 100.0, "z": 0.0, "is_master": False},
    {"x": 100.0, "y": 100.0, "z": 0.0, "is_master": False},
]

# Drone starting positions (they drift around these each cycle)
drones = [
    {"home": (30.0, 30.0, 55.0)},
    {"home": (70.0, 60.0, 50.0)},
]

# ---------------------------------------------------------------------------
# GROUND TRUTH: where the victims actually are. The system does NOT receive
# these. It only receives distances, and must REDISCOVER these positions.
# z is negative because they are buried. (depth = -z)
# ---------------------------------------------------------------------------
victims = [
    {"victim_id": "7a8b9c2d-4e5f", "base_bpm": 85,  "base_spo2": 97, "true": (42.0, 18.0, -4.2)},
    {"victim_id": "9f8e7d6c-5b4a", "base_bpm": 135, "base_spo2": 98, "true": (60.0, 75.0, -1.5)},
    {"victim_id": "1a2b3c4d-5e6f", "base_bpm": 58,  "base_spo2": 91, "true": (85.0, 30.0, -0.5)},
]

rescuers = [
    {"rescuer_id": "Cmdr_Basel",  "team": "Alpha", "status": "MOVING",  "x": 10, "y": 10},
    {"rescuer_id": "Tech_Nicole", "team": "Alpha", "status": "MOVING",  "x": 12, "y": 10},
    {"rescuer_id": "Medic_Shokha","team": "Beta",  "status": "STANDBY", "x": 0,  "y": 0},
]

RANGE_NOISE_M = 0.8   # how noisy each distance measurement is, in meters


def distance(ax, ay, az, bx, by, bz):
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)


def current_drone_positions():
    """Drones drift slightly around their home position each cycle."""
    out = []
    for d in drones:
        hx, hy, hz = d["home"]
        out.append({
            "x": hx + random.uniform(-5, 5),
            "y": hy + random.uniform(-5, 5),
            "z": hz + random.uniform(-3, 3),
            "is_master": False,
        })
    return out


try:
    while True:
        payload_victims = []
        payload_rescuers = []

        # Drones move once per cycle; all victims are measured against the same
        # drone positions this tick (that's how it would work in reality).
        drone_anchors = current_drone_positions()
        all_anchor_positions = GROUND_BEACONS + drone_anchors

        # --- VICTIMS: build vitals + a measured distance from every anchor ---
        for v in victims:
            tx, ty, tz = v["true"]

            bpm = max(40, min(200, v["base_bpm"] + random.randint(-4, 4)))
            spo2 = max(50, min(100, v["base_spo2"] + random.randint(-2, 1)))

            crisis = random.random() < 0.05
            if crisis:
                bpm += 25
                spo2 -= 8

            # Each anchor reports how far it *thinks* the victim is (true range
            # + measurement noise). This is the only spatial info that leaves
            # the field. The backend has to turn these distances into (x,y,z).
            anchors = []
            for a in all_anchor_positions:
                true_r = distance(a["x"], a["y"], a["z"], tx, ty, tz)
                noisy_r = true_r + random.gauss(0, RANGE_NOISE_M)
                anchors.append({
                    "x": round(a["x"], 2),
                    "y": round(a["y"], 2),
                    "z": round(a["z"], 2),
                    "range": round(max(0.0, noisy_r), 2),
                    "is_master": a["is_master"],
                })

            payload_victims.append({
                "victim_id": v["victim_id"],
                "bpm": bpm,
                "spo2": spo2,
                "hemorrhage": crisis,
                "anchors": anchors,   # <-- distances, NOT a position
            })

        # --- RESCUERS: their own GPS/tag gives x,y directly (unchanged) ---
        for r in rescuers:
            if r["status"] == "MOVING":
                r["x"] = max(0, min(500, r["x"] + random.choice([-1, 0, 1])))
                r["y"] = max(0, min(500, r["y"] + random.choice([-1, 0, 1])))
            payload_rescuers.append({
                "rescuer_id": r["rescuer_id"],
                "team": r["team"],
                "status": r["status"],
                "x": r["x"],
                "y": r["y"],
            })

        final_payload = {"victims": payload_victims, "rescuers": payload_rescuers}
        message = json.dumps(final_payload).encode("utf-8")
        sock.sendto(message, (UDP_IP, UDP_PORT))
        print(f"📦 Dispatched map state ({len(message)} bytes)")
        time.sleep(2.0)

except KeyboardInterrupt:
    print("\n🛑 Mock engine gracefully terminated.")
    sock.close()
