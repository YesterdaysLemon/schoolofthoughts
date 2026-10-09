# bench

| File | Purpose |
|---|---|
| `harness.py` | API layer (cost tracking, hard dollar cap, retries), solo/vote arms, Batch-API vote, and the swarm (`spawn`, ledger, `amend_charter`, wallets with refunds) |
| `charters.py` | Entrenched rules + three versions of THE GAME: `procedural`, `empty`, `orgchart` |
| `run.py` | Run arms × problems; resumable (finished results are skipped) |
| `analyze.py` | Accuracy/cost table for a results directory |
| `problems*.json` | Problem sets with answers |
| `verify_*.py` | Brute-force ground truth for every answer |
| `results/` | All runs from 2026-10-09: per-run JSON (answers, swarm trees, ledgers, transcripts) and `calls.jsonl` (tokens and cost per request) |

```bash
python3 run.py --tag t --problem-file problems_hard.json --problems H05,H08 --arms fable,swarm
python3 run.py --tag t --problem-file problems_hard.json --arms vote --vote-batch --vote-n 50
python3 run.py --tag t --arms swarm --game orgchart --cutoff-policy continue --arm-suffix _v2
python3 run.py --tag dry --mock ...        # no API calls
python3 analyze.py t problems_hard.json
python3 verify_hard.py h05 h08            # recompute ground truth
```

Swarm options: `--swarm-budget 50`, `--game procedural|empty|orgchart`,
`--swarm-max-tokens 48000`, `--cutoff-policy force|continue`.
