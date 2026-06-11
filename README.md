# RescueGrid — Search & Rescue Command System

A command system for locating and triaging people trapped under collapsed
buildings. Victims wear a bracelet reading vital signs; a network of ground
beacons and aerial drones measures their position through the rubble; a command
dashboard shows the rescue captain **who is where, how deep, and how critical** —
in priority order, updated live.

> **What is real and what is simulated:** This project is the complete
> *software* of such a system — the localization math, the medical triage, the
> live dashboards — and all of it runs and is tested. The *hardware* (bracelets,
> beacons, drone radios) is **simulated**: a generator produces the exact data
> real sensors would, so the rest of the pipeline can be proven end-to-end.
> Real hardware would attach at one defined point (UDP port 5005) with no other
> changes.

---

## What it does

- **Locates buried victims in 3D.** Each beacon/drone reports its distance to a
  victim's bracelet. From those distances the system computes the victim's
  `(x, y, z)` position by multilateration — accurate to roughly a meter in tests,
  even with measurement noise. Ground beacons alone can't find depth (they're on
  one flat plane); the two drones provide the vertical reference that makes depth
  solvable.
- **Triages by medical urgency.** Each victim is scored from heart rate, blood
  oxygen, and a bleeding flag, then classified CRITICAL / URGENT / STABLE and
  ranked sickest-first. It distinguishes likely panic (high heart rate + good
  oxygen) from genuine deterioration, and flags low heart rate as its own danger.
- **Never forgets a silent victim.** If a bracelet stops transmitting, the victim
  is not dropped — they stay on the map at their last known position, marked
  `NO_SIGNAL`, and sink to the bottom of the priority list until signal returns.
- **Tracks rescue teams live** on the same map as the victims.

---

## Architecture

```
[ Simulated hardware ]        [ C++ localization core ]        [ Python triage API ]        [ Browser dashboards ]
 mock_generator.py    --UDP-->  ingestion_core.cpp     --HTTP-->  triage_api.py     <--poll--  Commander.html
 (vitals + per-anchor           (multilateration:                 (medical scoring,            team.html
  distances over UDP:5005)       distances -> x,y,z)               NO_SIGNAL, ranking,
                                                                   serves GET /state)
```

The seam between simulation and reality is the UDP packet on port 5005. Swap the
mock for a real radio receiver and nothing downstream changes.

---

## Files

| File | Role |
|------|------|
| `mock_generator.py` | Stands in for the hardware. Emits victim vitals plus the distances each beacon/drone measured, over UDP. |
| `ingestion_core.cpp` | The C++ core. Catches packets, runs multilateration to compute each victim's position, forwards results to the triage API. |
| `lib/json.hpp` | nlohmann/json single-header library used by the C++ core. |
| `localization.h` | The multilateration solver (dependency-free 3D position math). |
| `triage_api.py` | FastAPI service. Scores triage, handles lost-signal victims, ranks them, serves `GET /state`. |
| `Commander.html` | Captain's dashboard: 3D map of victims (by depth) and rescue teams, priority list, live. |
| `team.html` | Field-team view: navigate to a selected victim, confirm outcome. |
| `requirements.txt` | Python dependencies. |

*Note: `signal_filter.py`, `data_contracts.h`, and `mock_backend_response.json`
are earlier scaffolding kept for reference; they are not part of the live pipeline.*

---

## How to run

You need 3 terminals plus a browser. Run them in this order.

**1. Install dependencies (once):**
```bash
pip install -r requirements.txt
sudo apt install libcurl4-openssl-dev    # Linux; for the C++ HTTP bridge
```

**2. Start the triage API (terminal 1):**
```bash
uvicorn triage_api:app --host 127.0.0.1 --port 8000
```

**3. Build and run the C++ core (terminal 2):**
```bash
g++ ingestion_core.cpp -o ingestion_core -Ilib -lcurl -pthread
./ingestion_core
```

**4. Start the hardware simulator (terminal 3):**
```bash
python3 mock_generator.py
```

**5. Open the dashboard:** serve the folder over HTTP (don't open the file
directly — browsers block network requests from `file://`):
```bash
python3 -m http.server 8080
```
Then open `http://localhost:8080/Commander.html` in a browser.

You should see victims appear as colored spheres at their computed depth, ranked
by urgency, with the C++ terminal printing `[LOCATED]` lines as it solves each
position.

---

## Honest limitations

- **Hardware is simulated.** Vitals and beacon/drone distances come from the
  mock, not real radios. Everything downstream is real.
- **Two vitals can't always tell panic from shock.** A bleeding victim with
  compensating heart rate and still-good oxygen reads like a frightened healthy
  one. The system flags this rather than guessing — the honest call with the
  sensors available.
- **Signal denoising** of the raw distances is a planned next step, not yet built.
- **Terrain/heightmap rendering** is future work.
