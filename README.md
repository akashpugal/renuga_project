# Edge-Based Intelligent Patient Health Monitoring System

Edge node (on-device AI) + Flask backend + live web dashboard. Runs fully offline on one laptop.

## Run it
```bash
pip install -r requirements.txt
python run_demo.py          # starts server + 4 simulated patients
# open http://localhost:5000
```
Manual: `python backend/app.py` then, in other terminals,
`python edge/edge_node.py --patient P02 --scenario fever` (scenarios: stable, fever, hypoxia, shock).

## Architecture
```
Sensors -> EDGE NODE (filter, baseline, NEWS2, risk, alert) -> compact summary -> Flask API -> SQLite
                                                                                   |-> SSE -> Dashboard
```
| Part | File | Job |
|---|---|---|
| Edge | `edge/edge_node.py` | Sensor read, de-noising, scoring, store-and-forward uplink |
| Backend | `backend/app.py` | Ingest API (key-protected), SQLite, alerts, live stream |
| Frontend | `frontend/` | Patient list, trend charts with normal bands, alerts, edge savings |

## What is new (for your report)
1. **Decision-making at the edge**: NEWS2 score, risk and alert level are computed on the device, so alerts still work if the network is down.
2. **Personal adaptive baseline**: EWMA mean/variance per patient and per vital; it barely learns while readings are abnormal, so a slow deterioration is not "learned as normal". This gives a *Watch* alert before NEWS2 thresholds are crossed.
3. **Signal cleaning**: range check + median-of-3 filter remove motion artefacts (about 1.5% of simulated samples are glitches).
4. **Event-driven uplink**: one summary per window, instant send when the risk level changes. The dashboard shows the bandwidth saved (about 70% in the demo).
5. **Store-and-forward**: if the server is down the edge buffers up to 5000 summaries and flushes them in order. Test: stop the server for 30 s, restart it.
6. **Alert hysteresis + de-duplication**: no flapping between levels, no repeat alerts.

## API
`POST /api/ingest` (header `X-Edge-Key`) · `GET /api/patients` · `GET /api/patients/<id>/vitals?limit=120`
· `GET /api/alerts` · `POST /api/alerts/<id>/ack` · `GET /api/stats` · `GET /api/stream` (SSE)

## Real hardware
Replace `Simulator.read()` in `edge_node.py` with sensor drivers (e.g. MAX30102 for HR/SpO2, DS18B20 or MLX90614 for temperature) on a Raspberry Pi. Set `--server http://<ward-pc-ip>:5000` and a strong `EDGE_KEY` environment variable on the server and `--key` on the edge.

## Limits
Educational prototype, not a certified medical device. Consciousness level (AVPU) is not scored. Alert thresholds follow NEWS2 scale 1.
