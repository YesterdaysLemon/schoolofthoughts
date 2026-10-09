"""Summarize a results/<tag> directory into summary.json and a printed table."""
import json
import sys
from collections import defaultdict
from pathlib import Path

tag = sys.argv[1]
d = Path("results") / tag
problems = json.load(open(sys.argv[2] if len(sys.argv) > 2 else "problems.json"))
rows = []
arm_cost = defaultdict(float)
for p in problems:
    pid = p["id"]
    row = dict(pid=pid, topic=p["topic"], kind=p["kind"], expected=p["answer"])
    for f in d.glob(f"*__{pid}.json"):
        r = json.load(open(f))
        arm = f.name.split("__")[0]
        arm_cost[arm] += r.get("cost", 0) or 0
        if arm == "fable":
            row["fable"] = dict(answer=r.get("answer"), correct=r.get("correct"), cost=r.get("cost"), wall=r.get("wall"),
                                out_tokens=(r.get("usage") or {}).get("output_tokens"), stop=r.get("stop"), error=r.get("error"))
        elif arm == "vote":
            row["vote"] = dict(answer=r.get("answer"), correct=r.get("correct"), tie=r.get("tie"), single_acc=r.get("single_acc"),
                               pass_at_n=r.get("pass_at_n"), n=r.get("n"), dist=r.get("distribution", [])[:5], cost=r.get("cost"),
                               wall=r.get("wall"), errors=len(r.get("errors", [])))
        elif arm.startswith("swarm"):
            row[arm] = dict(answer=r.get("answer"), correct=r.get("correct"), cost=r.get("cost"), wall=r.get("wall"),
                            n_agents=r.get("n_agents"), max_depth=r.get("max_depth"), n_ledger=r.get("n_ledger"),
                            n_amend=len(r.get("amendments", [])))
    rows.append(row)

arms = sorted({k for r in rows for k in r if k in ("fable", "vote") or k.startswith("swarm")})
tot = {}
for arm in arms:
    done = [r[arm] for r in rows if arm in r]
    tot[arm] = dict(n=len(done), correct=sum(1 for x in done if x.get("correct")), cost=arm_cost[arm])
if "vote" in arms:
    done = [r["vote"] for r in rows if "vote" in r]
    tot["haiku_single(mean of 50)"] = dict(n=len(done), correct=round(sum(x["single_acc"] or 0 for x in done), 2),
                                           cost=arm_cost["vote"] / max(1, done[0].get("n") or 50) if done else 0)
    tot["haiku_pass@50"] = dict(n=len(done), correct=sum(1 for x in done if x["pass_at_n"]), cost=arm_cost["vote"])
json.dump(dict(rows=rows, totals=tot), open(d / "summary.json", "w"), indent=1, default=str)

hdr = f"{'pid':4} {'expected':>9} | {'fable':>9} | {'H-single':>8} {'vote':>9} {'p@50':>4} | " + " | ".join(f"{a:>12}" for a in arms if a.startswith("swarm"))
print(hdr)
for r in rows:
    f = r.get("fable", {})
    v = r.get("vote", {})
    mark = lambda x: ("" if not x else ("✓" if x.get("correct") else "✗"))
    line = (f"{r['pid']:4} {r['expected']:>9} | {str(f.get('answer')):>8}{mark(f)} | "
            f"{(v.get('single_acc') if v else float('nan')):>8.2f} {str(v.get('answer')):>8}{mark(v)} {('Y' if v.get('pass_at_n') else 'N') if v else '':>4} | "
            + " | ".join(f"{str(r.get(a, {}).get('answer')):>11}{mark(r.get(a))}" for a in arms if a.startswith("swarm")))
    print(line)
print()
for k, v in tot.items():
    print(f"{k:28s} {v['correct']}/{v['n']}  cost ${v['cost']:.2f}")
