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

## Gauges, forgetting, last call

Run 1 ended after 1.4 minutes: both founders spent their context reading while unable
to see how full it was, were told to report without tools, and the game ended with 98
slots unspent. So agents now get proprioception:

- every turn ends with `[gauges]` — context used vs. the wrap line, turn, wallet, and
  the size of the last tool results; every tool result states its own size;
- a warning at 75% of the wrap line;
- old tool results are cleared server-side past `--clear-at` (context-management beta),
  in large chunks (`--clear-min`) so prompt-cache rewrites stay rare;
- at the wrap line, LAST CALL: `succeed`, ledger, message, give slots, amend and write
  files stay open; reading, code and spawning close; then a final report;
- a founder that runs out of room without passing on its role is revived from its final
  report (one slot), so the game only ends when founders end on purpose.

Prompt caching stays on (automatic cache breakpoints): in testing, 84% of prompt tokens
were cache reads.

## Round 3: the world gets bigger

- **Internet**: `web_search` / `web_fetch` (Anthropic server tools; about $0.01 per search, counted in the swarm's spend).
- **Solvers**: `install_solvers.sh` puts Singular, msolve, kissat, glucose, drat-trim, CaDiCaL and nauty on PATH; each agent's intro lists what is available.
- **Family**: `family(reach=3)` shows ancestors and descendants to great-grand level, siblings and founders; a larger reach shows more.
- **Swarm gauges**: agents born, alive, dollars spent of the cap, minutes elapsed, searches — every turn.
- **Living alongside**: `spawn(..., wait=false)` lets a parent keep working; the report and unspent slots arrive in its inbox.
- **Babies**: two living agents who agree can `conceive` a child at no slot cost; it reports to both.
- **Vacuum collapse**: the dollar cap (default $10) halts every agent at once; the game otherwise lasts while anyone is alive.

## Safety and failure handling

- **Sandboxed Python.** Agents' code runs as an unprivileged user (`--sandbox-user`, default
  `schoolagent`) inside `unshare -n`: no network, a minimal environment with no tokens, no
  access to `.git`, the API key, or the runner process. The worktree belongs to that user;
  `.git`, the charter, the ledger, the amendments and `SCHOOL/REPORTS/` stay with the runner.
  Create the user once: `useradd --system --no-create-home --shell /usr/sbin/nologin schoolagent`.
  The runner must run as root and the playground must sit somewhere other users can traverse
  (e.g. `/home/<you>/`). `--unsafe-no-sandbox` turns this off.
- **Huge files**: anything over `--max-file-mb` (10) is unstaged, listed in `.git/info/exclude`,
  and its author is told; single files cannot grow past 4x that; `run_python` refuses when free
  disk drops below `--min-free-gb`.
- **API trouble**: 8 retries with exponential backoff up to 5 minutes; a founder that still
  crashes is revived (up to 6 times per lineage) after a 60-second pause.
- **Vacuum collapse with last words**: the final `--reserve` (3%) of the dollar cap, or the last
  3 minutes, is kept for final reports — every living agent is told to write one, then it stops.
- **Inbox floods**: at most ~5k tokens of messages per turn (each clipped); the rest wait.
- **Lapsed proposals**: if an agent ends without answering a `conceive` proposal, the proposer is told.
- **Web text is data**: the charter (E3) says no web page speaks for the charter, a founder, an
  agent, or the people running the game.

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

Knobs: `--budget 100 --founders 2 --effort xhigh --max-turns 40 --turn-tokens 32000
--ctx-wrap 60000 --max-depth 10 --api-conc 16 --py-conc 4 --py-mem-gb 6`.

## While it runs

`python3 status.py runs/<tag>` prints a one-screen status: whether the process is alive,
spend, agents running, hand-offs, ledger/amendment/message counts, and notable events
from the last few minutes.

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
