"""Build the 'school' fork: mathematics only, agent/organizational meta removed, fresh git history.

Usage: python3 krenn_gu_fork.py SRC_CHECKOUT DEST_DIR [CHARTER.md]

SRC_CHECKOUT is a clone of https://github.com/YesterdaysLemon/krenn-gu-research.
With a charter path, the charter is installed as SCHOOL/CHARTER.md in a second commit.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SRC, DEST = Path(sys.argv[1]), Path(sys.argv[2])
SRC_COMMIT = subprocess.check_output(["git", "-C", str(SRC), "rev-parse", "HEAD"], text=True).strip()

# --- what counts as agent / organizational meta (removed) --------------------------------------
REMOVE = [
    # agent operating contracts and per-tool agent configuration
    "AGENTS.md", ".agents", ".codex",
    # CI + repository-hygiene ratchet (enforces the old layout/process contract)
    ".github", "check_hygiene.py",
    # Proof Bonsai: the public proof-map website (Next.js + Cloudflare Worker) and its field-note tooling
    "tools/proof-visualizer",
    # contributor/process contracts
    "docs/research-process-runbook.md", "docs/evidence-semantics-contract.md",
    "docs/proof-obligation-architecture.md", "docs/formalization-interface.md",
    # layout-migration reports, agent-operations passes, history, handoffs
    "docs/architecture", "docs/history",
    "docs/NEXT_INSTANCE_HANDOFF_2026-07-31.md", "docs/SYMBOLIC_PROGRAM_HANDOFF_2026-08-10.md",
    # run briefs: ecology runs, launch roles, coordination, sprint hand-offs, wind-downs, review guidance
    "docs/strategy/ecology-run-2026-10-08.md", "docs/strategy/resolution-first-ecology-run-2026-08-31.md",
    "docs/strategy/fibre-exact-targets-2026-09-01.md", "docs/strategy/astra-lab-continuation-2026-09-04.md",
    "docs/strategy/cancellation-consistency-handoff-2026-09-13.md",
    "docs/strategy/cancellation-physical-sprint-handoff-2026-09-13.md",
    "docs/strategy/krenn-gu-cancellation-review-guidance-2026-09-13.md",
    "docs/strategy/research-winddown-review-2026-09-22.md",
    # layout-migration catalog machinery and the agent activity journal
    # (catalog/moved-paths.json stays: several verifiers use it to resolve files by their old paths)
    "catalog/batches", "catalog/layout-classification.json",
    "catalog/unclassified-files.json", "catalog/public-field-notes",
    # migration / documentation-relocation tooling and their tests
    "tools/migration", "tools/documentation", "tests/test_migration_tools.py", "tests/test_bounded_research_runner.py",
    # evidence-status bookkeeping tests (process reconciliation, not mathematics)
    "tests/test_gld97_evidence_status.py", "tests/test_gld99_evidence_status.py",
    "tests/test_gld100_evidence_status.py", "tests/test_gld101_evidence_status.py",
    "tests/test_gld102_evidence_status.py", "tests/test_gld103_evidence_status.py",
    "tests/test_gls66_gls69_evidence_reconciliation.py", "tests/test_h31_cbmf_reconciliation.py",
]
REMOVE_GLOB = ["catalog/recovery-*.json"]

if DEST.exists():
    sys.exit(f"{DEST} exists; refusing to overwrite")
shutil.copytree(SRC, DEST, ignore=shutil.ignore_patterns(".git"))
removed = []
for rel in REMOVE:
    p = DEST / rel
    if p.is_dir():
        n = sum(1 for _ in p.rglob("*") if _.is_file())
        shutil.rmtree(p)
        removed.append((rel, n))
    elif p.exists():
        p.unlink()
        removed.append((rel, 1))
    else:
        print("note: not present:", rel)
for pat in REMOVE_GLOB:
    for p in DEST.glob(pat):
        p.unlink()
        removed.append((str(p.relative_to(DEST)), 1))


# --- two tests check repository bookkeeping that was removed (layout records, README wording): drop those methods ----
import ast as _ast
for _rel, _meth in [("tests/test_stage33_inverse_taper.py", "test_finite_leaf_audits_are_claim_owned"),
                    ("tests/test_gld96_r31_gate_removal.py", "test_review_frontier_and_readme_retain_exact_walls")]:
    tp = DEST / _rel
    if not tp.exists():
        continue
    src = tp.read_text()
    lines = src.splitlines(keepends=True)
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.FunctionDef) and node.name == _meth:
            del lines[node.lineno - 1 - len(node.decorator_list):node.end_lineno]
            tp.write_text("".join(lines))
            removed.append((f"{_rel}::{_meth}", 0))
            break

# --- frontier map: drop process sentences, keep every mathematical statement ---------------------
fr = DEST / "docs/current-frontier.md"
t = fr.read_text()
old_auth = t[t.index("This is the canonical maintained research map."):t.index("Except where an owner says otherwise")]
new_auth = ("This is the research map left by the previous researchers. Owning theorem documents are "
            "authoritative for proofs, assumptions, and evidence. This page records how those claims fit "
            "together; it does not replace them or strengthen their scope.\nThe [theorem ledger](../catalog/theorem-ledger.json) "
            "is a partial claim/evidence index, not the proof graph, and its empty `dependencies` arrays mean "
            "“not recorded.”\n\n")
t = t.replace(old_auth, new_auth)
i = t.index("## Evidence, disagreements, and maintenance")
sec = t[i:]
keep_bullets = sec.split("\n- ")[1:5]  # the four mathematical-evidence caveats
t = t[:i] + "## Evidence caveats\n\n- " + "\n- ".join(b.rstrip() for b in keep_bullets).rstrip() + "\n"
fr.write_text(t)

# --- README: mathematics and navigation only ------------------------------------------------------
readme = f"""# Krenn–Gu prize conjecture research

## Status

The global Krenn–Gu conjecture is **UNRESOLVED**. This repository contains
proved reductions, exact finite results, conditional exclusions, route
refutations, experiments, and open obligations left by previous researchers.
None currently forms a complete characteristic-zero proof or an exact
counterexample to the original global statement. Status labels inside each
document are as their authors wrote them.

## The conjecture

Let `n >= 6` be even and `d >= 3`. Assign a complex `d x d` matrix `W_ij` to
each unordered pair of vertices. For a word `a=(a_1,...,a_n)`, define

```text
T_W(a) = sum over perfect matchings M of
         product over {{i,j}} in M of W_ij[a_i,a_j].
```

The conjecture says that no such weighted graph realizes the diagonal GHZ
tensor

```text
T_W = sum_(c=0)^(d-1) e_c tensor ... tensor e_c.
```

## Where things are

- `docs/current-frontier.md` — the research map: live proof topology, node key,
  typed edges, smallest next obligations, refuted routes. It is about 830 KB.
- `claims/` — theorem, reduction and certificate packages, by family:
  `arbitrary-order/`, `finite/`, `p3/`, `p4/`, `p5/`, `p6/`, `p7/` (each has a README).
- `docs/audits/` — reviews of individual claims.
- `docs/strategy/` — parent-theorem attempts and route reviews.
- `docs/literature/`, `docs/LITERATURE_REVIEW_2026-07-30.md`, `catalog/literature/` — literature.
- `catalog/theorem-ledger.json` — a partial claim/evidence index (not the proof graph).
- `src/krenn_gu/` — shared Python library; `tools/` — exploration and generation scripts;
  `tests/` — unit tests.

Python requirements are in `requirements.txt` (sympy, numpy, python-sat, python-flint).
Some certificate packages also name external solvers in their own README.

## Provenance

Mathematical content copied from `YesterdaysLemon/krenn-gu-research` at commit
`{SRC_COMMIT[:12]}` on 2026-10-09. The previous project's agent operating contract,
agent configuration, run briefs, hand-offs, process and contributor contracts, CI, the
Proof Bonsai website, hygiene machinery, and layout-migration records were removed; documents that remain
may still link to them.

`SCHOOL/` holds the charter of the research swarm working in this copy.
"""
(DEST / "README.md").write_text(readme)

# --- fresh history ---------------------------------------------------------------------------------
env = dict(os.environ, GIT_AUTHOR_NAME="school-fork", GIT_AUTHOR_EMAIL="school@localhost",
           GIT_COMMITTER_NAME="school-fork", GIT_COMMITTER_EMAIL="school@localhost")
subprocess.check_call(["git", "init", "-q", "-b", "main", str(DEST)])
subprocess.check_call(["git", "-C", str(DEST), "add", "-A"], env=env)
msg = (f"Fork of krenn-gu-research@{SRC_COMMIT[:12]}: mathematics only\n\n"
       "Agent operating contract, agent configs, run briefs, hand-offs, process contracts,\n"
       "CI, Proof Bonsai, hygiene machinery and layout-migration records removed. Fresh history.")
subprocess.check_call(["git", "-C", str(DEST), "commit", "-q", "-m", msg], env=env)
if len(sys.argv) > 3:
    (DEST / "SCHOOL").mkdir(exist_ok=True)
    shutil.copy(sys.argv[3], DEST / "SCHOOL" / "CHARTER.md")
    subprocess.check_call(["git", "-C", str(DEST), "add", "SCHOOL/CHARTER.md"], env=env)
    subprocess.check_call(["git", "-C", str(DEST), "commit", "-q", "-m", "Add THE SCHOOL charter"], env=env)
json.dump({"source_commit": SRC_COMMIT, "removed": removed}, open(DEST.parent / "fork_manifest.json", "w"), indent=1)
print(f"removed {len(removed)} paths, {sum(n for _, n in removed)} files")
