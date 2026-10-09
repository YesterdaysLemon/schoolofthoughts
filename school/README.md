# THE SCHOOL — ecology runner

Runs a swarm of Claude Haiku 5.5 agents in a git repository that contains
`SCHOOL/CHARTER.md`. The runner is kept outside the repository so the agents never
see harness code; all they see is the charter, the repository, and their tools.

## What an agent is

Every agent is one Haiku 5.5 conversation (effort `xhigh`, adaptive thinking) with
the charter as its system prompt and these tools:

| Tool | What it does |
|---|---|
| `list_dir`, `read_file`, `grep` | read the repository (line ranges; output capped) |
| `write_file`, `edit_file` | change files (the charter, ledger and amendment files are game-owned) |
| `run_python` | run Python in the repo root with `src/` on the path; inline code is saved under `SCHOOL/scratch/agent-NNN/` so every computation stays reproducible; CPU/memory/time limits |
| `spawn(brief, budget)` | start another instance of itself, scoped by the brief, funded from its wallet; parallel within a turn; returns the child's report |
| `succeed(handoff)` | end itself and hand role + wallet + place in the tree to a fresh copy (costs 1 slot) |
| `message(to, text)` | send to one agent; reaches the current successor of a role |
| `ledger_read`, `ledger_post` | shared memory, mirrored to `SCHOOL/LEDGER.md` |
| `amend_charter` | change THE GAME (not the entrenched rules); mirrored to `SCHOOL/AMENDMENTS.md` |
| `give_slots(to, n)` | transfer budget to another living agent |

Two founders (#1, #2) start simultaneously with 49 slots each (100 agents total).

## Price tier

Haiku 5.5 bills a request at $0.10 / $0.50 per million input/output tokens when its
prompt (input + cache reads + cache writes) is at most 100,000 tokens, and at 5x that
when the prompt is longer — per request, not per account. In a tool loop the model's
earlier reasoning stays in its prompt, so an agent at `xhigh` fills up within a few
turns. Agents are told to wrap up (or `succeed`) once their prompt passes
`--ctx-wrap` (default 60,000), leaving room for one more full 32,000-token turn, so
every request stays in the cheap tier.

## Run

```bash
pip install sympy numpy "python-flint==0.9.0" && pip install --pre "python-sat>=1.8"
export SWARM_API_KEY_FILE=/path/to/key        # or SWARM_API_KEY=...
export SWARM_WORKSPACE_ID=wrkspc_...          # only for keys not scoped to one workspace
python3 recipes/krenn_gu_fork.py /path/to/krenn-gu-research ../playground CHARTER.template.md   # build a playground
python3 ecology.py --repo ../playground --tag run1 --cost-cap 30 --wall-hours 6
python3 ecology.py --repo /tmp/copy --tag dry --mock                         # mechanics test, no API
```

Add `--push-branch run/1` to run on a new branch and push it to `origin` every
`--push-every-min 5` minutes, so the commits can be watched live while `main` stays the
clean starting point for the next run.

Knobs: `--budget 100 --founders 2 --effort xhigh --max-turns 20 --turn-tokens 32000
--ctx-wrap 60000 --max-depth 10 --api-conc 16 --py-conc 4 --py-mem-gb 6`.

## What to look at afterwards

`python3 report.py runs/<tag> --repo <playground>` writes `runs/<tag>/RESULTS.md`: totals,
spend, the agent tree with hand-offs, amendments, founder-to-founder messages, the ledger,
the founders' final reports, and any report that uses the `CANDIDATE` label.

- `git log` in the repository: every agent's file changes are committed under
  `school-agent-<id>`, in order — the history of what emerged.
- `SCHOOL/LEDGER.md`, `SCHOOL/AMENDMENTS.md`, `SCHOOL/REPORTS/founder-*.md`.
- `runs/<tag>/events.jsonl` (spawns, hand-offs, messages, slot gifts, tool calls),
  `calls.jsonl` (per-request tokens and cost), `agent_NNN.json` (full transcripts),
  `summary.json` (the tree).

Python run by agents is resource-limited but not sandboxed beyond that: run it in a
throwaway container or VM, not on a machine with anything you care about.
