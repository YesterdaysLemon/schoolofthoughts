"""One-screen status of a running (or finished) ecology run.

    python3 status.py runs/<tag> [--since-min 10]
"""
import argparse
import json
import subprocess
import time
from collections import Counter
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("run_dir")
ap.add_argument("--since-min", type=float, default=10.0, help="list notable events from the last N minutes")
a = ap.parse_args()
rd = Path(a.run_dir)
ev = [json.loads(l) for l in open(rd / "events.jsonl")] if (rd / "events.jsonl").exists() else []
calls = [json.loads(l) for l in open(rd / "calls.jsonl")] if (rd / "calls.jsonl").exists() else []
alive = subprocess.run(["pgrep", "-f", f"ecology.py.*--tag {rd.name}( |$)"], capture_output=True).returncode == 0
mtime = max([(rd / f).stat().st_mtime for f in ("events.jsonl", "calls.jsonl") if (rd / f).exists()] or [0])
k = Counter(e["ev"] for e in ev)
starts = [e for e in ev if e["ev"] == "start"]
ends = [e for e in ev if e["ev"] == "end"]
t_now = max([e["t"] for e in ev] or [0])
print(f"run {rd.name}: process {'ALIVE' if alive else 'NOT RUNNING'}; last write {time.time() - mtime:.0f}s ago; "
      f"game clock {t_now/60:.1f} min; {'GAME OVER' if (rd / 'summary.json').exists() else 'in progress'}")
print(f"agents started {len(starts)} / ended {len(ends)} (running {len(starts) - len(ends)}); hand-offs {k.get('succeed', 0)} "
      f"(founder revivals {k.get('revive', 0)}); "
      f"max depth {max([e['depth'] for e in starts] or [0])}")
print(f"spend ${sum(c['cost'] for c in calls):.2f} over {len(calls)} requests; max prompt {max([c['prompt'] for c in calls] or [0]):,} tokens; "
      f"cache reads {100 * sum(c.get('cache_read', 0) for c in calls) / max(1, sum(c['prompt'] for c in calls)):.0f}% of prompt; "
      f"server clears {k.get('cleared', 0)} (~{sum(e.get('tokens', 0) for e in ev if e['ev'] == 'cleared'):,} tokens); errors {k.get('error', 0)}; pushes {sum(1 for e in ev if e['ev']=='push' and e.get('ok'))} ok / "
      f"{sum(1 for e in ev if e['ev']=='push' and not e.get('ok'))} failed")
print(f"ledger {k.get('ledger', 0)} {dict(Counter(e['label'] for e in ev if e['ev']=='ledger'))}; amendments {k.get('amend', 0)}; "
      f"messages {k.get('message', 0)}; slot gifts {k.get('give', 0)}; proposals {k.get('propose', 0)}; births {k.get('birth', 0)}; "
      f"background spawns {sum(1 for e in ev if e['ev'] == 'spawn' and e.get('wait') is False)}")
print(f"tool calls: {dict(Counter(e['tool'] for e in ev if e['ev']=='tool').most_common(8))}")
notable = [e for e in ev if e["t"] >= t_now - a.since_min * 60 and (
    e["ev"] in ("amend", "message", "give", "succeed", "revive", "error", "birth", "propose", "bequest") or (e["ev"] == "spawn")
    or (e["ev"] == "ledger" and e["label"] in ("PROVED", "COMPUTED", "CHECKED", "CANDIDATE")) or (e["ev"] == "push" and not e.get("ok")))]
print(f"\nnotable events in the last {a.since_min:g} min ({len(notable)}):")
for e in notable[-25:]:
    d = {kk: v for kk, v in e.items() if kk not in ("t", "ev")}
    print(f"  t+{e['t']/60:6.1f}m {e['ev']:8s} {json.dumps(d)[:220]}")
if any(e["ev"] == "ledger" and e["label"] == "CANDIDATE" for e in ev):
    print("\n!! a CANDIDATE claim is on the ledger: flag it for independent review; do not announce it as a result")
