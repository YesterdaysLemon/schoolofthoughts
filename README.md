# School of Thoughts

Many copies of a small language model, organized by a charter rather than an
org chart, with budget as their currency.

The primitive is simple: an agent can start **another instance of itself**, scoped by a
written brief and funded with agent-slots from its own wallet. Everything else —
who does what, how deep the tree goes, whether to check each other's work — is left
for the swarm to decide, under a charter that splits into:

- **Entrenched rules** nobody in the swarm can change: the mission, the budget, the
  physics (no tools beyond those given; per-turn and context limits), honest evidence
  labels, and how the game ends.
- **The game**: amendable rules for how to play. Any agent may change them with
  `amend_charter`; changing the game is a legal move, not cheating.

Two things live here:

| | What | Where |
|---|---|---|
| **bench** | A controlled experiment: one Claude Fable 5.1 call vs. 50 Claude Haiku 5.5 instances — voting independently, or self-organizing as a swarm — on math problems whose answers are known (brute-force verified) but must be derived by hand, without tools. | [`bench/`](bench/) |
| **school** | An open-ended research playground: up to 100 Haikus, seeded as two founders, working inside a git repository on an open problem, under the same kind of charter. Includes a recipe that builds a clean playground from the Krenn–Gu conjecture research repo. | [`school/`](school/) |

## First results (bench, 2026-10-09)

Each cell is one attempt per arm per problem. "Vote" is 50 independent Haikus with
majority vote; "single" is the average accuracy of one of those 50.

**Hard set** — 8 problems (long exact computations plus two insight problems):

| Arm | Correct | Cost |
|---|---|---|
| Fable 5.1, effort `xhigh` | 8/8 | $18.15 |
| Haiku 5.5 single (mean of 50) | 6.1/8 | $0.08 per attempt-set |
| 50 Haikus, majority vote | 8/8 | $3.89 (Batch API, half price) |
| Haiku swarm, procedural charter | **8/8** | **$0.44** |
| Haiku swarm, org-chart charter | 8/8 | $0.28 |
| Haiku swarm, empty charter | 7/8 (missed the coin game) | $0.42 |

**Frontier set** — 4 problems chosen to break single models:

| Arm | 5×5×5 walk | banded perms, n=15 | half-plane SAWs | Σφ(n) to 2026 | Cost |
|---|---|---|---|---|---|
| Fable 5.1, `xhigh` | ✓ | ✗ (ran out of output) | ✓ | ✓ | $11.55 |
| 50 Haikus, vote | ✓ (90% single) | ✗ (0/50) | ✗ (0/50) | ✓ (14% single) | $2.68 |
| Swarm v1 | ✗* | ✗ (off by 0.01%) | ✗ (off by 14) | ✓ | $1.22 |
| Swarm v2 | ✓ | **✓** | ✗ (off by 16) | ✗ (off by 8) | $1.60 |

\* v1's harness forced an agent to report as soon as one of its turns was cut off by
the output limit, which took away its chance to delegate; v2 lets it continue.

What stood out (details in [FINDINGS.md](FINDINGS.md)):

- **The swarm sizes itself to the problem.** On most problems the founder solved
  alone. When the work was decomposable it built a map-reduce (47 agents, every slice
  computed twice); when it was one long sequential computation it designed the
  method at the top and sent two grinders down a level of abstraction.
- **Cheap.** The swarm matched Fable on the hard set at about 1/40 of the cost, and
  solved the one frontier problem that both Fable and all 50 independent Haikus failed.
- **Its failure mode is the near-miss.** On big sums of many slices, one small slip
  survived aggregation (answers off by 8–16 out of 10^4–10^6).
- **The charter's template mattered less than expected**; the model's own judgment
  of the task dominated — though the empty charter was the only variant to miss a
  problem (the octal coin game, where the procedural and org-chart swarms succeeded). Amendments and the ledger went mostly unused on problems this
  short — they are what the `school` playground is for.

These are single attempts on 12 problems: suggestive, not statistics.

## Run it

```bash
pip install anthropic
export SWARM_API_KEY=sk-ant-...            # or SWARM_API_KEY_FILE=/path/to/key
export SWARM_WORKSPACE_ID=wrkspc_...       # only for keys not scoped to one workspace

# bench: arms fable / vote / swarm on a problem file
cd bench && python3 run.py --tag mine --problem-file problems_frontier.json --arms swarm --cutoff-policy continue
python3 analyze.py mine problems_frontier.json

# school: a research ecology in a git repo that contains SCHOOL/CHARTER.md
cd school && python3 ecology.py --repo /path/to/playground --tag run1 --cost-cap 30
python3 ecology.py --repo /tmp/copy --tag dry --mock      # mechanics test, no API calls
```

Everything goes straight to `https://api.anthropic.com`; the model layer is one
function in each runner (`API.create` in `bench/harness.py`, `School.call` in
`school/ecology.py`), so swapping in another provider's small model means replacing
that function.

No license has been chosen yet.
