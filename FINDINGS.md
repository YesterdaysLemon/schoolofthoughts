# Findings — bench, 2026-10-09

Models: `claude-fable-5-1` (effort `xhigh`, up to 64k–100k output tokens) and
`claude-haiku-5-5` (effort `high`). Haiku 5.5 was two days old.

## Problem sets

- **Warm-up (15 problems, `problems.json`)**: competition-style counting, number
  theory, probability and game problems with novel parameters. Haiku 5.5 answered
  every one of 100 attempts correctly, so this set was retired.
- **Hard (12, `problems_hard.json`)**: long exact computations (domino tilings of a
  holed 8×8 board, 3-colourings of a 5×5 grid, a 10-vertex hafnian, lattice points in
  a ball, an octal game's P-positions…) and insight problems built on IMO 2026
  mechanics. Eight with the most room to separate arms were run.
- **Frontier (4, `problems_frontier.json`)**: chosen so that a single Haiku fails.

Every answer is computed by exhaustive enumeration or exact arithmetic in
`verify_*.py`, several by two independent methods. Models get no tools.

## Arms

- **Fable** — one call.
- **Vote** — 50 independent Haiku calls (64k output each), majority answer.
  Also reports mean single-sample accuracy and pass@50.
- **Swarm** — up to 50 Haiku agents. The founder may `spawn(brief, budget)` children
  (same model, same charter), which may spawn their own; spawns in one turn run in
  parallel; unspent slots return to the parent; a shared ledger and
  `amend_charter` are available. Three charters differ only in THE GAME:
  *procedural* (minimal process rules and named failure modes), *empty*, and
  *orgchart* (CEO, departments, QA). See `bench/charters.py`.

## Observations

1. **Room to think explains a lot.** With a 32k output cap a single Haiku scored 0%
   on the holed-board tiling; with 64k it scored 70%. Many apparent capability limits
   of the small model were output-length limits.
2. **The swarm mostly didn't swarm** — on 5 of 8 hard problems the founder solved
   alone. It delegated when the problem forced it:
   - lattice points in a ball → 46 slices, each computed by two independent agents,
     all 23 pairs agreed, the total re-added in two groupings;
   - banded permutations → the founder designed a 20-state transfer matrix, then two
     children computed it, one by a deliberately different route.
3. **Calibrated but not acting on it.** Founders often wrote "no independent check was
   done; residual risk is arithmetic error" — and answered with 49 slots unspent,
   though the charter said unspent slots are worthless.
4. **The template barely stuck.** The org-chart charter (CEO who does not compute)
   mostly produced a founder who computed everything alone. The empty charter behaved
   like the procedural one.
5. **No amendments, little ledger use.** Trees were shallow stars; reports to the
   parent carried the communication. Shared memory and rule changes probably need
   longer-running work.
6. **Failure mode: the aggregation near-miss.** On the half-plane walk count and the
   totient sum, 50-agent swarms returned answers off by 8–16 out of 10^4–10^6:
   decomposition worked, one slip survived the merge.
7. **Harness physics matter.** Agents must be told their own limits (per-turn output,
   context); otherwise they have no reason to delegate a computation that will not
   fit. And a turn cut off by the output limit must not end the agent's options
   (the v1 → v2 fix).
8. **Cost.** Fable spent 10–17 minutes and up to $5 per hard problem. The swarm's
   typical cost was cents, because it only buys help when it needs it.

## Caveats

One attempt per arm per problem; 12 problems; one task family (exact derivations
with integer answers); the problems were written by the same model family that is
being tested. The harness's v1 cut-off rule cost the swarm one frontier problem.
