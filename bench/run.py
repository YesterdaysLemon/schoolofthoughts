"""Run arms x problems. Resumable: finished (arm, problem) results are skipped.

python3 run.py --tag full --arms fable,vote,swarm --problems all --cap 60
python3 run.py --tag dry --mock ...
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

from harness import API, FABLE, HAIKU, Swarm, run_solo, run_vote, run_vote_batch


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--arms", default="fable,vote,swarm")
    ap.add_argument("--problems", default="all")
    ap.add_argument("--problem-file", default="problems.json")
    ap.add_argument("--vote-n", type=int, default=50)
    ap.add_argument("--swarm-budget", type=int, default=50)
    ap.add_argument("--game", default="procedural")
    ap.add_argument("--haiku-effort", default="high")
    ap.add_argument("--fable-effort", default="xhigh")
    ap.add_argument("--fable-max-tokens", type=int, default=100000)
    ap.add_argument("--haiku-max-tokens", type=int, default=64000)
    ap.add_argument("--cap", type=float, default=60.0)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--vote-batch", action="store_true")
    ap.add_argument("--cutoff-policy", default="force", choices=["force", "continue"])
    ap.add_argument("--arm-suffix", default="")
    ap.add_argument("--swarm-max-tokens", type=int, default=48000)
    ap.add_argument("--haiku-conc", type=int, default=24)
    a = ap.parse_args()

    out = Path("results") / a.tag
    out.mkdir(parents=True, exist_ok=True)
    problems = json.load(open(a.problem_file))
    if a.problems != "all":
        want = set(a.problems.split(","))
        problems = [p for p in problems if p["id"] in want]
    arms = a.arms.split(",")

    # dollars already spent under this tag count toward the cap (resume-safe)
    spent_before = 0.0
    if (out / "calls.jsonl").exists():
        spent_before = sum(json.loads(l)["cost"] for l in open(out / "calls.jsonl"))
    api = API(out, cap_usd=a.cap, mock=a.mock, haiku_conc=a.haiku_conc)
    api.spent = spent_before
    json.dump(vars(a), open(out / "config.json", "w"), indent=1)

    t0 = time.time()

    async def job(arm, p):
        name = f"{arm}{'_' + a.game + a.arm_suffix if arm == 'swarm' else ''}__{p['id']}.json"
        path = out / name
        if path.exists():
            return
        if arm == "fable":
            r = await run_solo(api, FABLE, a.fable_effort, p, "fable", 0, a.fable_max_tokens)
        elif arm == "vote":
            r = await run_vote(api, p, a.vote_n, a.haiku_effort, a.haiku_max_tokens)
        elif arm == "swarm":
            r = await Swarm(api, p, game=a.game, budget=a.swarm_budget, effort=a.haiku_effort,
                            max_tokens=a.swarm_max_tokens, arm=f"haiku_swarm_{a.game}{a.arm_suffix}",
                            cutoff_policy=a.cutoff_policy).run()
        else:
            raise ValueError(arm)
        r["expected"] = p["answer"]
        json.dump(r, open(path, "w"), indent=1, default=str)
        print(f"[{time.time() - t0:7.1f}s] {arm:6s} {p['id']}  answer={r.get('answer')}  "
              f"correct={r.get('correct')}  cost=${r.get('cost', 0):.3f}  total=${api.spent:.2f}"
              + (f"  agents={r.get('n_agents')} ledger={r.get('n_ledger')} amend={len(r.get('amendments', []))}" if arm == "swarm" else "")
              + (f"  single_acc={r.get('single_acc'):.2f} pass@n={r.get('pass_at_n')}" if arm == "vote" else "")
              + (f"  ERROR {r['error']}" if r.get("error") else ""), flush=True)

    async def vote_batch_job():
        todo = [p for p in problems if not (out / f"vote__{p['id']}.json").exists()]
        if not todo:
            return
        rs = await run_vote_batch(api, todo, a.vote_n, a.haiku_effort, a.haiku_max_tokens, out)
        for r in rs:
            r["expected"] = next(p["answer"] for p in problems if p["id"] == r["pid"])
            json.dump(r, open(out / f"vote__{r['pid']}.json", "w"), indent=1, default=str)
            print(f"[{time.time() - t0:7.1f}s] vote   {r['pid']}  answer={r['answer']}  correct={r['correct']}  "
                  f"cost=${r['cost']:.3f}  single_acc={r['single_acc']:.2f} pass@n={r['pass_at_n']}  total=${api.spent:.2f}", flush=True)

    jobs = [job(arm, p) for p in problems for arm in arms if not (arm == "vote" and a.vote_batch)]
    if "vote" in arms and a.vote_batch:
        jobs.append(vote_batch_job())
    await asyncio.gather(*jobs)
    print(f"done in {time.time() - t0:.0f}s, {api.ncalls} calls, spent ${api.spent:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
