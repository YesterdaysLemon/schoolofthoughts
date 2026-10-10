"""Summarize an ecology run into RESULTS.md.

    python3 report.py runs/<tag> --repo /path/to/playground [--out RESULTS.md]

Reads runs/<tag>/{summary.json,events.jsonl,calls.jsonl,agent_*.json} and, from the
repository, SCHOOL/LEDGER.md, SCHOOL/AMENDMENTS.md and SCHOOL/REPORTS/.
"""
import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rd, repo = Path(a.run_dir), Path(a.repo)
    ev = [json.loads(l) for l in open(rd / "events.jsonl")]
    calls = [json.loads(l) for l in open(rd / "calls.jsonl")] if (rd / "calls.jsonl").exists() else []
    summ = json.load(open(rd / "summary.json")) if (rd / "summary.json").exists() else None
    starts = {e["agent"]: e for e in ev if e["ev"] == "start"}
    ends = {e["agent"]: e for e in ev if e["ev"] == "end"}
    kinds = Counter(e["ev"] for e in ev)
    tools = Counter(e["tool"] for e in ev if e["ev"] == "tool")
    labels = Counter(e["label"] for e in ev if e["ev"] == "ledger")
    msgs = [e for e in ev if e["ev"] == "message"]
    founders = {aid for aid, s in starts.items() if s["parent"] is None and s["inc"] == 1}
    role_of = {aid: s["role"] for aid, s in starts.items()}
    founder_roles = {role_of[f] for f in founders}
    f2f = [m for m in msgs if role_of.get(m["frm"]) in founder_roles and role_of.get(m["to"]) in founder_roles]
    spent = sum(c["cost"] for c in calls)
    prompts = [c["prompt"] for c in calls]
    over = sum(1 for p in prompts if p > 100_000)
    depth = max((s["depth"] for s in starts.values()), default=0)
    children = defaultdict(list)
    for aid, s in starts.items():
        if s["parent"] is not None and s["inc"] == 1:
            children[s["parent"]].append(aid)
    successors = {e["agent"]: e["successor"] for e in ev if e["ev"] == "succeed"}
    statuses = Counter(e["status"] for e in ends.values())
    wall = max((e["t"] for e in ev), default=0)

    L = []
    w = L.append
    w(f"# Ecology run report: `{rd.name}`\n")
    w(f"- Agents instantiated: **{len(starts)}** (founders: {sorted(founders)}); max depth {depth}; "
      f"hand-offs {len(successors)} (founder revivals {kinds.get('revive', 0)}); statuses {dict(statuses)}")
    w(f"- Spend: **${spent:.2f}** over {len(calls)} requests; wall time {wall/60:.1f} min; "
      f"requests over the 100k price threshold: {over}")
    w(f"- Ledger entries: {sum(labels.values())} {dict(labels)}; amendments: {kinds.get('amend', 0)}; "
      f"messages: {len(msgs)} (founder-to-founder: {len(f2f)}); slot gifts: {kinds.get('give', 0)}")
    w(f"- Tool use: {dict(tools.most_common())}")
    try:
        log = subprocess.run(["git", "-C", str(repo), "log", "--format=%an", "main..HEAD"], capture_output=True, text=True).stdout.split()
        by_author = Counter(log)
        w(f"- Commits on the run branch: {len(log)} ({len([x for x in by_author if x.startswith('school-agent')])} distinct agents committed files)")
        stat = subprocess.run(["git", "-C", str(repo), "diff", "--shortstat", "main..HEAD"], capture_output=True, text=True).stdout.strip()
        w(f"- Diff vs main: {stat}")
    except Exception:
        pass

    flags = []
    for f in sorted(rd.glob("agent_*.json")):
        d = json.load(open(f))
        rep = (d.get("report") or "")
        claims = [ln for ln in rep.splitlines() if "CANDIDATE" in ln and not re.search(r"\b(no|not|nothing|none|never)\b", ln, re.I)]
        if claims:  # the charter's mandatory label for a claimed global proof or counterexample, not merely mentioned
            flags.append((d["id"], rep[:400].replace("\n", " ")))
    w("\n## Flags for review\n")
    if flags:
        w("Reports using the CANDIDATE label (a claimed global proof or counterexample; verify independently before believing):\n")
        for aid, txt in flags:
            w(f"- agent #{aid}: {txt}")
    else:
        w("No agent report uses the CANDIDATE label.")

    am = repo / "SCHOOL" / "AMENDMENTS.md"
    w("\n## Amendments\n")
    w(am.read_text().strip() if am.exists() else "None.")

    w("\n## Founder-to-founder messages\n")
    for m in f2f[:40]:
        w(f"- t+{m['t']:.0f}s #{m['frm']} → #{m['to']}: {m['text']}")
    if not f2f:
        w("None.")

    w("\n## Agent tree\n")
    w("`#id (role.incarnation) depth status turns $cost — brief`\n")

    def line(aid, indent):
        s, e = starts[aid], ends.get(aid, {})
        brief = (s.get("brief") or "").replace("\n", " ")[:110]
        w(f"{'  ' * indent}- #{aid} ({s['role']}.{s['inc']}) d{s['depth']} {e.get('status', '?')} "
          f"{e.get('turns', '?')}t ${e.get('cost', 0):.3f} — {brief}")
        for c in sorted(children.get(aid, [])):
            line(c, indent + 1)
        if aid in successors:
            w(f"{'  ' * indent}  ↳ handed off to #{successors[aid]}")
            line(successors[aid], indent)

    for f in sorted(founders):
        line(f, 0)

    w("\n## Founders' final reports\n")
    rep_dir = repo / "SCHOOL" / "REPORTS"
    for p in sorted(rep_dir.glob("founder-*.md")) if rep_dir.exists() else []:
        w(p.read_text().strip() + "\n")

    led = repo / "SCHOOL" / "LEDGER.md"
    w("\n## Ledger\n")
    w(led.read_text().strip() if led.exists() else "Empty.")

    out = Path(a.out) if a.out else rd / "RESULTS.md"
    out.write_text("\n".join(L) + "\n")
    print(f"wrote {out} ({len(L)} lines); agents {len(starts)}, spend ${spent:.2f}, flags {len(flags)}")


if __name__ == "__main__":
    main()
