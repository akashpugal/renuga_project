#!/usr/bin/env python3
"""Edge node: runs next to the patient (Raspberry Pi / ESP32 gateway).
Reads sensors, cleans the signal, scores risk ON THE DEVICE, and uplinks only
compact summaries (immediately when risk changes). Works offline (store-and-forward).
Replace Simulator.read() with real sensor drivers (MAX30102, DS18B20, ...)."""
import argparse, json, random, time, urllib.error, urllib.request
from collections import deque
from itertools import count

KEYS = ("hr", "spo2", "temp", "rr", "sbp")
VALID = {"hr": (25, 220), "spo2": (60, 100), "temp": (32, 43), "rr": (3, 50), "sbp": (50, 260)}
FLOOR = {"hr": 3.0, "spo2": 0.7, "temp": 0.15, "rr": 1.2, "sbp": 4.0}   # min std-dev for z-score
WORSE = {"hr": 1, "rr": 1, "temp": 1, "spo2": -1, "sbp": -1}            # direction of deterioration
INF = float("inf")
# NEWS2 (Royal College of Physicians) scoring tables: (upper bound exclusive, points)
NEWS = {"rr": [(8.5, 3), (11.5, 1), (20.5, 0), (24.5, 2), (INF, 3)],
        "spo2": [(91.5, 3), (93.5, 2), (95.5, 1), (INF, 0)],
        "sbp": [(90.5, 3), (100.5, 2), (110.5, 1), (219.5, 0), (INF, 3)],
        "hr": [(40.5, 3), (50.5, 1), (90.5, 0), (110.5, 1), (130.5, 2), (INF, 3)],
        "temp": [(35.05, 3), (36.05, 1), (38.05, 0), (39.05, 1), (INF, 2)]}
NAMES = {"hr": "Heart rate", "spo2": "SpO2", "temp": "Temperature", "rr": "Resp. rate", "sbp": "Systolic BP"}

def points(k, x):
    for ub, pts in NEWS[k]:
        if x < ub: return pts

# ---------------------------------------------------------------- sensor simulator
class Simulator:
    BASE = dict(hr=74, spo2=97.5, temp=36.8, rr=16, sbp=124)
    NOISE = dict(hr=1.5, spo2=0.4, temp=0.05, rr=0.8, sbp=2.0)
    SCEN = {"stable": {},
            "fever": dict(hr=40, rr=10, temp=2.3, spo2=-4, sbp=-30),
            "hypoxia": dict(hr=25, rr=9, spo2=-9, sbp=-5),
            "shock": dict(hr=55, rr=8, spo2=-3, sbp=-55)}
    def __init__(self, scenario, onset, duration, seed=None):
        self.rng, self.n, self.delta = random.Random(seed), 0, self.SCEN[scenario]
        self.onset, self.duration = onset, duration
        self.off = {k: self.rng.uniform(-1, 1) * FLOOR[k] for k in KEYS}
    def read(self):
        self.n += 1
        s = min(max((self.n - self.onset) / self.duration, 0), 1)
        v = {k: self.BASE[k] + self.off[k] + self.rng.gauss(0, self.NOISE[k]) + s * self.delta.get(k, 0) for k in KEYS}
        if self.rng.random() < 0.015:                      # motion artefact / lead-off glitch
            k = self.rng.choice(KEYS); v[k] = self.rng.choice([VALID[k][1] + 40, v[k] * 0.6, v[k] * 1.5])
        return v

# ---------------------------------------------------------------- on-device intelligence
class EdgePipeline:
    def __init__(self):
        self.hist = {k: deque(maxlen=3) for k in KEYS}     # median filter window
        self.good = {}                                     # last plausible value
        self.mean, self.var, self.n = {}, {}, 0            # adaptive personal baseline (EWMA)
        self.run = {k: 0 for k in KEYS}                    # consecutive |z|>3 counters
        self.trend = {k: deque(maxlen=30) for k in KEYS}
        self.level, self.down = 0, 0
    def clean(self, raw):
        out = {}
        for k in KEYS:
            lo, hi = VALID[k]
            x = raw[k] if lo <= raw[k] <= hi else self.good.get(k, raw[k])   # reject impossible values
            self.hist[k].append(x)
            out[k] = sorted(self.hist[k])[len(self.hist[k]) // 2]               # median-of-3 de-spike
            self.good[k] = out[k]
        return out
    def process(self, raw):
        v, self.n = self.clean(raw), self.n + 1
        z, reasons = {}, []
        for k in KEYS:                                     # personal baseline: learns slowly, barely learns when abnormal
            if k not in self.mean: self.mean[k], self.var[k] = v[k], FLOOR[k] ** 2
            sd = max(self.var[k] ** 0.5, FLOOR[k])
            z[k] = (v[k] - self.mean[k]) / sd if self.n > 20 else 0.0
            a = 0.02 if abs(z[k]) < 2 else 0.002
            self.mean[k] += a * (v[k] - self.mean[k]); self.var[k] += a * ((v[k] - self.mean[k]) ** 2 - self.var[k])
            self.run[k] = self.run[k] + 1 if abs(z[k]) > 3 else 0
            self.trend[k].append(v[k])
        pts = {k: points(k, round(v[k], 1) if k == "temp" else round(v[k])) for k in KEYS}
        total, top = sum(pts.values()), max(pts.values())
        sustained = [k for k in KEYS if self.run[k] >= 5]
        tr = 0.0
        for k in KEYS:                                     # short-term drift in the "worse" direction
            h = list(self.trend[k])
            if len(h) == 30:
                d = (sum(h[-10:]) - sum(h[:10])) / 10 * WORSE[k] / (FLOOR[k] * 4)
                if d > 0.8: reasons.append(f"{NAMES[k]} trending {'up' if WORSE[k] > 0 else 'down'}")
                tr = max(tr, min(max(d, 0), 1))
        risk = min(1.0, 0.6 * min(total / 9, 1) + 0.25 * min(len(sustained) / 2, 1) + 0.15 * tr)
        target = 3 if total >= 7 else 2 if (total >= 5 or top == 3) else 1 if (total >= 3 or sustained or risk >= 0.4) else 0
        if target >= self.level: self.level, self.down = target, 0
        else:                                              # hysteresis: step down only after 15 calm samples
            self.down += 1
            if self.down >= 15: self.level, self.down = self.level - 1, 0
        for k in KEYS:
            if pts[k] >= 2: reasons.insert(0, f"{NAMES[k]} {v[k]:.1f}" if k == "temp" else f"{NAMES[k]} {v[k]:.0f}")
        for k in sustained:
            if pts[k] < 2: reasons.append(f"{NAMES[k]} off personal baseline")
        return dict(vitals=v, news2=total, level=self.level, risk=round(risk, 2), reasons=reasons[:5])

# ---------------------------------------------------------------- uplink with store-and-forward
class Uplink:
    def __init__(self, server, key):
        self.url, self.key, self.q = server.rstrip("/") + "/api/ingest", key, deque(maxlen=5000)
        self.raw_bytes = self.sent_bytes = 0
        self.retry_at = 0.0
    def send(self, payload):
        self.q.append(payload)
        if time.time() < self.retry_at: return
        while self.q:
            body = json.dumps(self.q[0], separators=(",", ":")).encode()
            req = urllib.request.Request(self.url, body, {"Content-Type": "application/json", "X-Edge-Key": self.key})
            try:
                urllib.request.urlopen(req, timeout=3).read()
            except urllib.error.HTTPError as e:
                if e.code in (400, 404): self.q.popleft(); continue     # bad payload: drop, don't block the queue
                self.retry_at = time.time() + 5; return
            except (urllib.error.URLError, OSError):
                self.retry_at = time.time() + 5; return                 # offline: keep buffering
            self.sent_bytes += len(body); self.q.popleft()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient", default="P01")
    ap.add_argument("--scenario", choices=Simulator.SCEN, default="stable")
    ap.add_argument("--server", default="http://localhost:5000")
    ap.add_argument("--key", default="edge-demo-key")
    ap.add_argument("--tick", type=float, default=1.0, help="seconds between sensor samples")
    ap.add_argument("--window", type=int, default=10, help="samples per routine uplink")
    ap.add_argument("--onset", type=int, default=60, help="sample number when deterioration starts")
    ap.add_argument("--duration", type=int, default=120, help="samples for deterioration to fully develop")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    sim, pipe, up, win, last_sent = Simulator(a.scenario, a.onset, a.duration, a.seed), EdgePipeline(), None, [], 0
    up = Uplink(a.server, a.key)
    print(f"[edge {a.patient}] scenario={a.scenario} -> {a.server}")
    for _ in count():
        raw = sim.read()
        up.raw_bytes += len(json.dumps({"patient_id": a.patient, "ts": 0, "vitals": raw}, separators=(",", ":")))
        win.append(pipe.process(raw))
        cur = win[-1]
        if cur["level"] != last_sent or len(win) >= a.window:        # routine summary OR instant on risk change
            avg = {k: round(sum(w["vitals"][k] for w in win) / len(win), 1) for k in KEYS}
            up.send(dict(patient_id=a.patient, ts=time.time(), vitals=avg, news2=max(w["news2"] for w in win),
                         level=cur["level"], risk=cur["risk"], reasons=cur["reasons"],
                         edge=dict(raw_bytes=up.raw_bytes, sent_bytes=up.sent_bytes, buffered=len(up.q))))
            last_sent, win = cur["level"], []
        time.sleep(a.tick)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: pass
