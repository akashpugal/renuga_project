#!/usr/bin/env python3
"""One command demo: starts the server and four edge nodes (stable / fever / hypoxia / shock)."""
import subprocess, sys, time, os
here = os.path.dirname(os.path.abspath(__file__))
py = sys.executable
procs = [subprocess.Popen([py, os.path.join(here, "backend", "app.py")])]
time.sleep(2)
for pid, sc, onset in [("P01", "stable", 0), ("P02", "fever", 50), ("P03", "hypoxia", 90), ("P04", "shock", 140)]:
    procs.append(subprocess.Popen([py, os.path.join(here, "edge", "edge_node.py"), "--patient", pid,
                                   "--scenario", sc, "--tick", "0.5", "--onset", str(onset), "--duration", "120"]))
print("\nDashboard: http://localhost:5000   (Ctrl+C to stop)\n")
try:
    for p in procs: p.wait()
except KeyboardInterrupt:
    for p in procs: p.terminate()
