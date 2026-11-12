"""Ward server: receives compact summaries from edge nodes, stores them,
raises alerts and streams live updates to the dashboard (Server-Sent Events)."""
import json, os, queue, sqlite3, time
from flask import Flask, Response, g, jsonify, request, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "health.db")
FRONT = os.path.normpath(os.path.join(BASE, "..", "frontend"))
EDGE_KEY = os.environ.get("EDGE_KEY", "edge-demo-key")
LEVELS = ["Normal", "Watch", "Warning", "Critical"]

app = Flask(__name__, static_folder=None)
subscribers = []

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients(id TEXT PRIMARY KEY, name TEXT, age INT, bed TEXT);
CREATE TABLE IF NOT EXISTS vitals(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts REAL,
  hr REAL, spo2 REAL, temp REAL, rr REAL, sbp REAL, news2 INT, level INT, risk REAL, reasons TEXT);
CREATE INDEX IF NOT EXISTS ix_vitals ON vitals(patient_id, id);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT, ts REAL,
  level INT, message TEXT, acked INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS edge_stats(patient_id TEXT PRIMARY KEY, raw_bytes INT, sent_bytes INT,
  buffered INT, updated REAL);
"""
PATIENTS = [("P01", "Meena Krishnan", 54, "Bed 01"), ("P02", "Arjun Pillai", 67, "Bed 02"),
            ("P03", "Lakshmi Narayan", 72, "Bed 03"), ("P04", "Vikram Rao", 61, "Bed 04")]

def init_db():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(SCHEMA)
    c.executemany("INSERT OR IGNORE INTO patients VALUES(?,?,?,?)", PATIENTS)
    c.commit(); c.close()

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def publish(msg):
    data = json.dumps(msg)
    for q in list(subscribers):
        try: q.put_nowait(data)
        except queue.Full: pass

def vrow(r):
    d = dict(r); d["reasons"] = json.loads(d.get("reasons") or "[]"); return d

@app.post("/api/ingest")
def ingest():
    if request.headers.get("X-Edge-Key") != EDGE_KEY:
        return jsonify(error="invalid edge key"), 401
    p = request.get_json(silent=True) or {}
    try:
        pid, v, ts = p["patient_id"], p["vitals"], float(p["ts"])
        level, news2, risk = int(p["level"]), int(p["news2"]), float(p.get("risk", 0))
        vals = [float(v[k]) for k in ("hr", "spo2", "temp", "rr", "sbp")]
    except (KeyError, TypeError, ValueError):
        return jsonify(error="malformed payload"), 400
    d = db()
    if not d.execute("SELECT 1 FROM patients WHERE id=?", (pid,)).fetchone():
        return jsonify(error="unknown patient"), 404
    prev = d.execute("SELECT level FROM vitals WHERE patient_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    reasons = [str(x) for x in p.get("reasons", [])][:6]
    d.execute("INSERT INTO vitals(patient_id,ts,hr,spo2,temp,rr,sbp,news2,level,risk,reasons) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (pid, ts, *vals, news2, level, risk, json.dumps(reasons)))
    e = p.get("edge", {})
    d.execute("INSERT OR REPLACE INTO edge_stats VALUES(?,?,?,?,?)",
              (pid, int(e.get("raw_bytes", 0)), int(e.get("sent_bytes", 0)), int(e.get("buffered", 0)), time.time()))
    alert = None
    recent = d.execute("SELECT 1 FROM alerts WHERE patient_id=? AND level>=? AND ts>?", (pid, level, ts - 300)).fetchone()
    if level >= 1 and level > (prev["level"] if prev else 0) and not recent:   # no repeat alerts for flapping levels
        msg = f"{LEVELS[level]}: " + (", ".join(reasons) or f"NEWS2 {news2}")
        cur = d.execute("INSERT INTO alerts(patient_id,ts,level,message) VALUES(?,?,?,?)", (pid, ts, level, msg))
        alert = dict(id=cur.lastrowid, patient_id=pid, ts=ts, level=level, message=msg, acked=0)
    d.commit()
    publish(dict(type="vitals", patient_id=pid, ts=ts, **dict(zip(("hr", "spo2", "temp", "rr", "sbp"), vals)),
                 news2=news2, level=level, risk=risk, reasons=reasons, edge=e))
    if alert: publish(dict(type="alert", alert=alert))
    return jsonify(ok=True)

@app.get("/api/patients")
def patients():
    d, out = db(), []
    for p in d.execute("SELECT * FROM patients ORDER BY id"):
        last = d.execute("SELECT * FROM vitals WHERE patient_id=? ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone()
        out.append(dict(p, latest=vrow(last) if last else None))
    return jsonify(out)

@app.get("/api/patients/<pid>/vitals")
def vitals(pid):
    n = min(int(request.args.get("limit", 120)), 1000)
    rows = db().execute("SELECT * FROM vitals WHERE patient_id=? ORDER BY id DESC LIMIT ?", (pid, n)).fetchall()
    return jsonify([vrow(r) for r in reversed(rows)])

@app.get("/api/alerts")
def alerts():
    rows = db().execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/alerts/<int:aid>/ack")
def ack(aid):
    d = db(); d.execute("UPDATE alerts SET acked=1 WHERE id=?", (aid,)); d.commit()
    publish(dict(type="ack", id=aid)); return jsonify(ok=True)

@app.get("/api/stats")
def stats():
    rows = db().execute("SELECT * FROM edge_stats").fetchall()
    return jsonify({r["patient_id"]: dict(r) for r in rows})

@app.get("/api/stream")
def stream():
    q = queue.Queue(maxsize=300); subscribers.append(q)
    def gen():
        try:
            while True:
                try: yield f"data: {q.get(timeout=15)}\n\n"
                except queue.Empty: yield ": keep-alive\n\n"
        finally: subscribers.remove(q)
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.get("/")
def index(): return send_from_directory(FRONT, "index.html")

@app.get("/<path:p>")
def assets(p): return send_from_directory(FRONT, p)

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
