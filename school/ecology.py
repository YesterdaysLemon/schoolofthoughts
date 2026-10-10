"""THE SCHOOL — ecology runner.

Runs a swarm of Claude Haiku 5.5 agents inside a git repository under SCHOOL/CHARTER.md.
Two founders start at once; every agent can spawn copies of itself (same model, same
charter, its own scope), hand its role to a fresh successor, message other agents, use
a shared ledger, amend the game, give away slots, and work on the repository's files.

    export SWARM_API_KEY_FILE=...   (or SWARM_API_KEY)
    export SWARM_WORKSPACE_ID=wrkspc_...     # only for multi-workspace keys
    python3 ecology.py --repo ../krenn-gu-school --tag run1            # real run
    python3 ecology.py --repo /tmp/copy --tag dry --mock               # mechanics test, no API

Every request stays under Haiku 5.5's 100k-token prompt price threshold by design:
agents are told to wrap up once their prompt passes --ctx-wrap tokens, leaving room for
one more full turn.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import resource
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

MODEL = "claude-haiku-5-5"
PRICE = dict(inp=0.10, out=0.50, cw=0.125, cr=0.01)        # USD / Mtok, prompt <= 100k tokens
PRICE_LONG = dict(inp=0.50, out=2.50, cw=0.625, cr=0.05)   # prompt > 100k tokens
# Pinned to the versions without a bundled server-side code sandbox: agents mistook that sandbox for the repo.
WEB_TOOLS = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
             {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 5, "max_content_tokens": 6000}]
SEARCH_USD = 0.01  # $10 per 1,000 web searches; fetches cost only their tokens
SOLVERS = ["Singular", "msolve", "kissat", "glucose", "drat-trim", "cadical", "dreadnaut", "nauty-geng"]
HARNESS_FILES = {"SCHOOL/CHARTER.md", "SCHOOL/LEDGER.md", "SCHOOL/AMENDMENTS.md"}


def prompt_tokens(u):
    return (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)


def cost_of(u):
    r = PRICE_LONG if prompt_tokens(u) > 100_000 else PRICE
    return ((u.get("input_tokens") or 0) * r["inp"] + (u.get("output_tokens") or 0) * r["out"]
            + (u.get("cache_creation_input_tokens") or 0) * r["cw"] + (u.get("cache_read_input_tokens") or 0) * r["cr"]) / 1e6


class Stop(Exception):
    pass


def est_tokens(text: str) -> int:
    """Conservative token estimate (identifier-heavy repository text runs ~3 characters per token)."""
    return len(text) // 3 + 1


def clip(text: str, max_tokens: int) -> str:
    if est_tokens(text) <= max_tokens:
        return text
    keep = max_tokens * 3
    return text[:keep] + f"\n... [cut here: the full result was about {est_tokens(text):,} tokens; ask for a narrower range or pattern]"


WRAP_TOOLS = {"succeed", "ledger_post", "message", "give_slots", "write_file", "edit_file", "amend_charter", "family", "conceive"}


TOOLS = [
    {"name": "list_dir", "description": "List a directory of the repository (names, sizes).",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string", "default": "."},
                                                       "depth": {"type": "integer", "default": 1, "minimum": 1, "maximum": 3}}}},
    {"name": "read_file", "description": "Read lines of a file (numbered). Large files: read in ranges.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"},
                                                       "start_line": {"type": "integer", "default": 1},
                                                       "num_lines": {"type": "integer", "default": 200, "maximum": 600}},
                      "required": ["path"]}},
    {"name": "grep", "description": "Search file contents with a regular expression. Returns matching lines with paths and line numbers.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string", "default": "."},
                                                       "max_results": {"type": "integer", "default": 40, "maximum": 200},
                                                       "files_only": {"type": "boolean", "default": False}},
                      "required": ["pattern"]}},
    {"name": "write_file", "description": "Create or overwrite a file in the repository.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace one exact, unique occurrence of old_text with new_text in a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"},
                                                       "new_text": {"type": "string"}},
                      "required": ["path", "old_text", "new_text"]}},
    {"name": "run_python", "description": "Run Python in the repository root (src/ is on the path). Give either code (saved under SCHOOL/scratch/ so it stays reproducible) or the path of an existing script. Output is truncated.",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}, "path": {"type": "string"},
                                                       "args": {"type": "array", "items": {"type": "string"}},
                                                       "timeout_s": {"type": "integer", "default": 120, "maximum": 600}}}},
    {"name": "spawn", "description": "Start a child agent (same model, same charter) with a brief and agent-slots from your wallet (>= 1). With wait=true (default) you pause until it reports, and several such spawns in one turn run in parallel. With wait=false it lives alongside you: you keep working, and its report and unspent slots arrive in your inbox when it ends.",
     "input_schema": {"type": "object", "properties": {"brief": {"type": "string"}, "budget": {"type": "integer", "minimum": 1},
                                                       "wait": {"type": "boolean", "default": True}},
                      "required": ["brief", "budget"]}},
    {"name": "conceive", "description": "Have a child together with another living agent, at no slot cost. The first call proposes (partner + brief); the partner accepts by calling conceive with your id and the proposal number. The child lives alongside both parents and both receive its report.",
     "input_schema": {"type": "object", "properties": {"partner": {"type": "integer"}, "brief": {"type": "string"},
                                                       "proposal": {"type": "integer", "description": "to accept a proposal you received"}},
                      "required": ["partner"]}},
    {"name": "succeed", "description": "End yourself and pass your role, wallet and place in the tree to a fresh successor (costs 1 slot). The handoff note is all it will know besides the charter and your original brief. Must be the only tool call in its turn.",
     "input_schema": {"type": "object", "properties": {"handoff": {"type": "string"}}, "required": ["handoff"]}},
    {"name": "message", "description": "Send a message to another agent (by id). It is delivered at the start of its next turn. Messages to an agent that handed off reach its current successor.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "integer"}, "text": {"type": "string"}},
                      "required": ["to", "text"]}},
    {"name": "ledger_read", "description": "Read the shared ledger (entries after `since`, most recent if long).",
     "input_schema": {"type": "object", "properties": {"since": {"type": "integer", "default": 0}}}},
    {"name": "ledger_post", "description": "Post an entry to the shared ledger.",
     "input_schema": {"type": "object", "properties": {
         "label": {"type": "string", "enum": ["PROVED", "COMPUTED", "CHECKED", "EXPERIMENT", "CONJECTURE", "CANDIDATE", "DEAD_END", "LEAD", "PLAN"]},
         "text": {"type": "string"}}, "required": ["label", "text"]}},
    {"name": "amend_charter", "description": "Amend THE GAME (not the entrenched rules). Binds agents spawned afterwards; announced on the ledger.",
     "input_schema": {"type": "object", "properties": {"amendment": {"type": "string"}, "reason": {"type": "string"}},
                      "required": ["amendment", "reason"]}},
    {"name": "family", "description": "See your family in the swarm tree: ancestors and descendants up to `reach` generations (default 3: up to great-grandparents and great-grandchildren), your siblings, and the founders. Ask for a larger reach to see more of the tree.",
     "input_schema": {"type": "object", "properties": {"reach": {"type": "integer", "default": 3, "minimum": 1, "maximum": 50}}}},
    {"name": "give_slots", "description": "Give agent-slots from your wallet to another running agent.",
     "input_schema": {"type": "object", "properties": {"to": {"type": "integer"}, "n": {"type": "integer", "minimum": 1}},
                      "required": ["to", "n"]}},
]


@dataclass
class Agent:
    id: int
    parent: int | None
    depth: int
    role: int                 # id of the first incarnation of this role
    incarnation: int
    wallet: int
    brief: str
    founder: int | None = None
    alive: bool = True
    status: str = "running"
    turns: int = 0
    cost: float = 0.0
    out_tokens: int = 0
    max_prompt: int = 0
    inbox: list = field(default_factory=list)
    children: list = field(default_factory=list)
    report: str | None = None
    successor: int | None = None
    touched: set = field(default_factory=set)
    coparent: int | None = None
    t_start: float = 0.0
    t_end: float = 0.0


class School:
    def __init__(self, a):
        self.a = a
        self.repo = Path(a.repo).resolve()
        self.run_dir = Path(a.runs_dir).resolve() / a.tag
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.charter = (self.repo / "SCHOOL/CHARTER.md").read_text()
        self.agents: dict[int, Agent] = {}
        self.role_current: dict[int, int] = {}
        self.ledger: list[dict] = []
        self.amendments: list[dict] = []
        self.next_id = 1
        self.instances = 0
        self.spent = 0.0
        self.t0 = time.time()
        self.api_sem = asyncio.Semaphore(a.api_conc)
        self.py_sem = asyncio.Semaphore(a.py_conc)
        self.git_lock = asyncio.Lock()
        self.log_lock = asyncio.Lock()
        self.last_push = 0.0
        self.searches = 0
        self.bg: set = set()
        self.proposals: dict = {}
        self.babies = 0
        self.crash_revives: dict = {}
        self.collapse_announced = False
        self.solvers = [b for b in SOLVERS if shutil.which(b)]
        self.agent_home = Path("/tmp") / f"school-agent-home-{a.tag}"
        self.sandbox = None
        self.client = None
        if not a.mock:
            import anthropic
            key = os.environ.get("SWARM_API_KEY") or Path(os.environ["SWARM_API_KEY_FILE"]).read_text().strip()
            hdrs = {"anthropic-workspace-id": os.environ["SWARM_WORKSPACE_ID"]} if os.environ.get("SWARM_WORKSPACE_ID") else {}
            self.client = anthropic.AsyncAnthropic(api_key=key, base_url="https://api.anthropic.com", max_retries=10,
                                                   default_headers=hdrs, timeout=anthropic.Timeout(1800.0, connect=30.0))

    # ------------------------------------------------------------------ logging / persistence
    async def log(self, fname, rec):
        rec = dict(t=round(time.time() - self.t0, 2), **rec)
        async with self.log_lock:
            with open(self.run_dir / fname, "a") as f:
                f.write(json.dumps(rec, default=str) + "\n")

    def system(self):
        s = self.charter
        if self.amendments:
            s += "\n\n## AMENDMENTS TO THE GAME IN FORCE\n\n" + "\n".join(f"A{i+1}. {x['text']}" for i, x in enumerate(self.amendments))
        return s

    async def commit(self, ag: Agent, summary: str):
        """Commit the files this agent touched under its own name; then sweep anything else
        (ledger, amendments, files written by scripts) into an unattributed 'school' commit."""
        paths = sorted(ag.touched)
        ag.touched = set()
        async with self.git_lock:
            def _c():
                def env(name, mail):
                    return dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=mail,
                                GIT_COMMITTER_NAME="school", GIT_COMMITTER_EMAIL="school@localhost")
                g = ["git", "-c", "safe.directory=*", "-C", str(self.repo)]
                e = env(f"school-agent-{ag.id}", f"agent{ag.id}@school")
                def drop_big(e):
                    out = subprocess.run(g + ["diff", "--cached", "--name-only", "-z", "--diff-filter=AM"], env=e,
                                         capture_output=True).stdout.decode(errors="replace").split("\0")
                    for rel in filter(None, out):
                        f = self.repo / rel
                        if f.is_file() and f.stat().st_size > self.a.max_file_mb * 1_000_000:
                            subprocess.run(g + ["rm", "-q", "--cached", "--", rel], env=e, capture_output=True)
                            with open(self.repo / ".git" / "info" / "exclude", "a") as x:
                                x.write("/" + rel + "\n")
                            big.append((rel, f.stat().st_size))
                big = []
                if paths:
                    subprocess.run(g + ["add", "-A", "--", *paths], env=e, capture_output=True)
                    drop_big(e)
                    if subprocess.run(g + ["diff", "--cached", "--quiet"], env=e).returncode:
                        subprocess.run(g + ["commit", "-q", "-m", f"agent #{ag.id} turn {ag.turns}: {summary}"[:300]], env=e, capture_output=True)
                e2 = env("school", "school@localhost")
                # during play only the game's own files are swept; everything else waits for its author (or the final sweep)
                sweep = ["SCHOOL/LEDGER.md", "SCHOOL/AMENDMENTS.md"] if ag.id else ["-A"]
                subprocess.run(g + ["add", *sweep], env=e2, capture_output=True)
                drop_big(e2)
                if subprocess.run(g + ["diff", "--cached", "--quiet"], env=e2).returncode:
                    subprocess.run(g + ["commit", "-q", "-m", (f"ledger/amendments after agent #{ag.id} turn {ag.turns}" if ag.id else "final sweep: reports and unattributed files")],
                                   env=e2, capture_output=True)
                return big
            big = await asyncio.to_thread(_c)
        for rel, size in big:
            await self.log("events.jsonl", dict(ev="too_big", agent=ag.id, path=rel, mb=round(size / 1e6, 1)))
            if ag.id:
                ag.inbox.append({"from": 0, "text": f"(the game: {rel} is {size / 1e6:.0f} MB, over the {self.a.max_file_mb} MB "
                                 "limit for the shared history; it stays on disk for this game but will not be saved)"})
        await self.maybe_push()

    async def maybe_push(self, final=False):
        if not getattr(self.a, "push_branch", ""):
            return
        if not final and time.time() - self.last_push < self.a.push_every_min * 60:
            return
        self.last_push = time.time()
        r = await asyncio.to_thread(subprocess.run, ["git", "-c", "safe.directory=*", "-C", str(self.repo), "push", "-q", "origin",
                                                    f"HEAD:refs/heads/{self.a.push_branch}"], capture_output=True, text=True)
        await self.log("events.jsonl", dict(ev="push", ok=r.returncode == 0, err=r.stderr[-300:] if r.returncode else ""))

    def append_file(self, rel, text):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(text)

    # ------------------------------------------------------------------ agents
    def new_agent(self, parent, depth, wallet, brief, role=None, incarnation=1, founder=None) -> Agent:
        ag = Agent(id=self.next_id, parent=parent, depth=depth, role=role or self.next_id, incarnation=incarnation,
                   wallet=wallet, brief=brief, founder=founder, t_start=time.time() - self.t0)
        self.next_id += 1
        self.instances += 1
        self.agents[ag.id] = ag
        self.role_current[ag.role] = ag.id
        if parent and incarnation == 1:
            self.agents[parent].children.append(ag.id)
        return ag

    def resolve(self, aid: int) -> Agent | None:
        ag = self.agents.get(aid)
        if ag is None:
            return None
        cur = self.agents[self.role_current.get(ag.role, aid)]
        return cur if cur.alive else None

    async def call(self, ag: Agent, messages, force: bool):
        if self.spent >= self.a.cost_cap:
            raise Stop(f"dollar cap ${self.a.cost_cap} reached")
        if time.time() - self.t0 > self.a.wall_hours * 3600:
            raise Stop("wall-clock cap reached")
        body = {"output_config": {"effort": self.a.effort}, "cache_control": {"type": "ephemeral"}}
        headers = {}
        if self.a.clear_at > 0:
            # Old tool results are cleared server-side once the prompt passes clear_at. Each clear rewrites the
            # prompt cache from the clearing point, so clears are made rare and large (clear_min) to stay cheap.
            headers["anthropic-beta"] = "context-management-2025-06-27"
            body["context_management"] = {"edits": [{
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "input_tokens", "value": self.a.clear_at},
                "keep": {"type": "tool_uses", "value": 4},
                "clear_at_least": {"type": "input_tokens", "value": self.a.clear_min},
                "exclude_tools": ["spawn", "message", "succeed", "amend_charter", "give_slots", "ledger_post"]}]}
        tools = TOOLS + (WEB_TOOLS if self.a.web else [])
        if self.a.web:
            headers["anthropic-beta"] = ",".join(x for x in [headers.get("anthropic-beta"), "web-fetch-2025-09-10"] if x)
        kw = dict(model=MODEL, max_tokens=self.a.turn_tokens, system=self.system(), messages=messages, tools=tools,
                  tool_choice={"type": "none"} if force else {"type": "auto"}, thinking={"type": "adaptive"},
                  extra_body=body, extra_headers=headers)
        async with self.api_sem:
            t = time.time()
            if self.a.mock:
                msg = await mock_reply(self, ag, messages, force)
            else:
                last = None
                for attempt in range(8):
                    try:
                        async with self.client.messages.stream(**kw) as s:
                            m = await s.get_final_message()
                        msg = m.model_dump(mode="json", exclude_none=True)
                        break
                    except Exception as e:  # transient network/server trouble: back off and retry
                        last = e
                        if getattr(e, "status_code", 500) in (400, 401, 403, 404):
                            raise
                        await self.log("events.jsonl", dict(ev="retry", agent=ag.id, attempt=attempt + 1,
                                                            status=getattr(e, "status_code", None), err=str(e)[:200]))
                        await asyncio.sleep(min(300, 10 * 2 ** attempt) + random.random() * 10)
                else:
                    raise last
            dt = time.time() - t
        u = msg["usage"]
        searches = sum(1 for b in msg.get("content", []) if b.get("type") == "server_tool_use" and b.get("name") == "web_search")
        self.searches += searches
        c = cost_of(u) + searches * SEARCH_USD
        self.spent += c
        ag.cost += c
        ag.out_tokens += u.get("output_tokens") or 0
        ag.max_prompt = max(ag.max_prompt, prompt_tokens(u))
        cleared = sum(e.get("cleared_input_tokens") or 0 for e in ((msg.get("context_management") or {}).get("applied_edits") or []))
        await self.log("calls.jsonl", dict(agent=ag.id, turn=ag.turns, latency=round(dt, 1), stop=msg.get("stop_reason"),
                                           prompt=prompt_tokens(u), out=u.get("output_tokens"), cost=c, spent=round(self.spent, 4),
                                           cache_read=u.get("cache_read_input_tokens") or 0,
                                           cache_write=u.get("cache_creation_input_tokens") or 0, cleared=cleared))
        return msg

    async def run_agent(self, ag: Agent, intro: str):
        if self.solvers and "Solvers on PATH" not in intro:
            intro += f"\n\nSolvers on PATH (usable from run_python via subprocess): {', '.join(self.solvers)}."
        await self.log("events.jsonl", dict(ev="start", agent=ag.id, parent=ag.parent, role=ag.role, inc=ag.incarnation,
                                            wallet=ag.wallet, depth=ag.depth, brief=ag.brief[:500]))
        messages = [{"role": "user", "content": [{"type": "text", "text": intro}]}]
        transcript = []
        force, nudge, last_prompt, handed_off = False, None, 0, False
        wrap, wrap_turns, warned, last_results_tok = False, 0, False, 0
        while True:
            # --- vacuum collapse: everyone writes last words, then the universe ends
            if not force and self.collapsing():
                force = True
                if not self.collapse_announced:
                    self.collapse_announced = True
                    await self.log("events.jsonl", dict(ev="collapse", spent=round(self.spent, 4)))
                nudge = ("VACUUM COLLAPSE (rule E2): the swarm has reached the end of its dollars or its time. Every agent "
                         "stops now. Write your final report — what you established, with labels, and what a future swarm "
                         "should do next — without calling any tool.")
            # --- phases: normal -> last call (wrap) -> forced final
            if not wrap and (last_prompt > self.a.ctx_wrap or ag.turns >= self.a.max_turns - 2):
                wrap = True
                why = "your context passed the wrap line" if last_prompt > self.a.ctx_wrap else "you are on your last turns"
                nudge = (f"LAST CALL (rule E4): {why}. You can still: `succeed` (hand your role and wallet to a fresh copy of "
                         "yourself; recommended if work remains), post to the ledger, message, give slots, or write files. "
                         "Reading, searching, running code and spawning are closed to you now. Or end by replying without "
                         "tools: that reply is your final report.")
                if ag.wallet:
                    nudge += (f" You still hold {ag.wallet} slots: give them to someone who can use them, pass them on with "
                              "`succeed`, or they go to " + ("the other founder" if ag.founder else "your parent") + " when you end.")
                if ag.founder:
                    nudge += (" As a founder: if you end without succeeding, the game starts a successor for you from your "
                              "final report.")
            elif wrap:
                wrap_turns += 1
                if wrap_turns >= 2 or last_prompt > self.a.hard_ctx:
                    force = True
                    nudge = "FINAL TURN (rule E4): write your final report now, without calling any tool."
            elif not warned and last_prompt > 0.75 * self.a.ctx_wrap:
                warned = True
                nudge = ("GAUGE WARNING: your context is 75% of the way to the wrap line. Plan your next moves: hand "
                         "reading to a helper (a child starts with a fresh context), write down what matters, or succeed soon.")
            if messages[-1]["role"] == "assistant":  # resuming a paused server-tool turn
                extra = None
            else:
                extra = self.drain_inbox(ag)
            if extra is None:
                pass
            elif nudge:
                extra.append(nudge)
                nudge = None
            if extra is not None:
                extra.append(self.gauges(ag, last_prompt, last_results_tok))
                messages[-1]["content"].append({"type": "text", "text": "\n\n".join(extra)})
            try:
                msg = await self.call(ag, messages, force)
            except Stop as e:
                ag.status, ag.report = "stopped", f"(halted by the runner: {e})"
                break
            except Exception as e:
                ag.status, ag.report = "error", f"(agent crashed: {type(e).__name__}: {str(e)[:300]})"
                await self.log("events.jsonl", dict(ev="error", agent=ag.id, err=str(e)[:500]))
                if ag.founder and ag.wallet >= 1 and not self.collapsing() and self.crash_revives.get(ag.role, 0) < 6:
                    self.crash_revives[ag.role] = self.crash_revives.get(ag.role, 0) + 1
                    await asyncio.sleep(60)  # let an overload pass
                    await self.log("events.jsonl", dict(ev="revive", agent=ag.id, cause="crash"))
                    handed_off = True
                    last_words = "\n".join(b.get("text", "") for t in transcript[-2:] for b in t if b.get("type") == "text")
                    await self.handoff(ag, "(The game started you automatically: your predecessor crashed on an API error "
                                           "and could not pass on its role. Its last words follow; check the ledger and "
                                           "SCHOOL/ for what it left.)\n\n" + last_words[-4000:], transcript)
                break
            ag.turns += 1
            new_prompt = prompt_tokens(msg["usage"])
            if last_prompt and new_prompt < last_prompt - 5000:  # the server cleared old tool results
                await self.log("events.jsonl", dict(ev="cleared", agent=ag.id, tokens=last_prompt - new_prompt))
            last_prompt = new_prompt + (msg["usage"].get("output_tokens") or 0)
            content = clean(msg["content"])
            transcript.append(content)
            messages.append({"role": "assistant", "content": content})
            uses = [b for b in content if b["type"] == "tool_use"]
            if msg.get("stop_reason") == "pause_turn":
                continue  # the server paused a long web turn; send it back to continue
            if not uses:
                if msg.get("stop_reason") == "max_tokens" and not force:
                    # a cut-off turn loses its unfinished work but must not take away the agent's moves (e.g. delegating)
                    nudge = ("Your last turn hit the per-turn output limit (rule E4) and was cut off; its unfinished work is lost. "
                             "Continue: you can still use your tools, for example split a long computation across helpers or succeed.")
                    messages.append({"role": "user", "content": []})
                    continue
                ag.report = "\n".join(b.get("text", "") for b in content if b["type"] == "text")
                ag.status = "done"
                if ag.founder and (wrap or force) and ag.wallet >= 1 and not self.collapsing():
                    # a founder that ran out of room did not choose to end the game: revive it (charter E4)
                    await self.log("events.jsonl", dict(ev="revive", agent=ag.id))
                    handed_off = True
                    await self.handoff(ag, "(The game started you automatically: your predecessor ran out of room without "
                                           "passing on its role. Its final report follows.)\n\n" + ag.report, transcript)
                break
            succ = [b for b in uses if b["name"] == "succeed"]
            if succ and len(uses) > 1:
                results = ["Refused: succeed must be the only tool call in its turn." if b["name"] == "succeed" else None for b in uses]
                rest = [b for b in uses if b["name"] != "succeed"]
                rr = await asyncio.gather(*[self.do_tool(ag, b, wrap) for b in rest])
                it = iter(rr)
                results = [r if r is not None else next(it) for r in results]
            elif succ:
                handed_off = True
                await self.handoff(ag, succ[0]["input"].get("handoff", ""), transcript)
                break
            else:
                results = await asyncio.gather(*[self.do_tool(ag, b, wrap) for b in uses])
            results = [clip(str(r), self.a.max_result_tokens * (2 if b["name"] == "spawn" else 1)) for b, r in zip(uses, results)]
            results = [f"{r}\n[this result: about {est_tokens(r):,} tokens]" for r in results]
            last_results_tok = sum(est_tokens(r) for r in results)
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": b["id"], "content": r}
                                                         for b, r in zip(uses, results)]})
            if any(b["name"] in ("write_file", "edit_file", "run_python") for b in uses):
                await self.commit(ag, ", ".join(sorted({b["name"] for b in uses})))
            last_prompt += last_results_tok
        ag.alive = False
        if not handed_off:
            for n, p in self.proposals.items():
                if not p["done"] and p["to"] == ag.role:
                    p["done"] = True
                    asker = self.resolve(self.role_current.get(p["frm"], 0)) if self.role_current.get(p["frm"]) else None
                    if asker:
                        asker.inbox.append({"from": 0, "text": f"(the game: agent #{ag.id} ended without answering your "
                                                               f"proposal #{n}; it has lapsed)"})
        ag.t_end = time.time() - self.t0
        json.dump(dict(id=ag.id, brief=ag.brief, intro=intro, transcript=transcript, report=ag.report),
                  open(self.run_dir / f"agent_{ag.id:03d}.json", "w"), indent=1, default=str)
        await self.log("events.jsonl", dict(ev="end", agent=ag.id, status=ag.status, turns=ag.turns, cost=round(ag.cost, 4),
                                            max_prompt=ag.max_prompt, wallet_left=ag.wallet))

    def family(self, ag: Agent, reach: int) -> str:
        def line(a: Agent, rel: str) -> str:
            st = "alive" if a.alive else a.status
            s = (f"- {rel}: #{a.id} (role {a.role}.{a.incarnation}, depth {a.depth}, {st}, turn {a.turns}"
                 + (f", wallet {a.wallet}" if a.alive else "") + (f", co-parent #{a.coparent}" if a.coparent else "") + f") brief: {a.brief[:100].replace(chr(10), ' ') or '(founder)'}")
            if not a.alive and a.report:
                s += f" | report: {a.report[:140].replace(chr(10), ' ')}"
            return s
        names = ["parent", "grandparent", "great-grandparent"]
        out, cur, gen = [line(ag, "you")], ag, 0
        while cur.parent and gen < reach:
            cur = self.agents[cur.parent]
            out.append(line(cur, names[gen] if gen < 3 else f"ancestor {gen + 1} up"))
            gen += 1
        if ag.parent:
            sibs = [self.agents[c] for c in self.agents[ag.parent].children if self.agents[c].role != ag.role]
            out += [line(x, "sibling") for x in sibs]
        kids = ["child", "grandchild", "great-grandchild"]
        frontier = [ag.role]
        for g in range(reach):
            nxt = []
            for rid in frontier:
                for a in self.agents.values():
                    if a.incarnation == 1 and a.parent and self.agents[a.parent].role == rid:
                        out.append("  " * (g + 1) + line(a, kids[g] if g < 3 else f"descendant {g + 1} down")[2:])
                        nxt.append(a.role)
            frontier = nxt
        for a in self.agents.values():
            if a.founder and a.role != ag.role and a.incarnation == max(b.incarnation for b in self.agents.values() if b.role == a.role):
                out.append(line(a, f"founder #{a.founder} (current incarnation)"))
        if reach < 50:
            out.append(f"(reach {reach}; call family with a larger reach to see more)")
        return "\n".join(out)

    def launch_bg(self, child: Agent, intro: str, parents: list):
        async def life():
            await self.run_agent(child, intro)
            await self.deliver(child, parents)
        t = asyncio.create_task(life())
        self.bg.add(t)
        t.add_done_callback(self.bg.discard)

    async def deliver(self, child: Agent, parents: list):
        """Report and unspent slots of a background child go to its living parents, else the nearest living ancestor."""
        rep = clip(child.report or "(no report)", 1500)
        got = []
        for pid in parents:
            tgt, cur = self.resolve(pid), self.agents.get(pid)
            while tgt is None and cur is not None and cur.parent:
                cur = self.agents[cur.parent]
                tgt = self.resolve(cur.id)
            if tgt and tgt.id not in [g.id for g in got]:
                got.append(tgt)
        refund, child.wallet = child.wallet, 0
        if got:
            got[0].wallet += refund
            for g in got:
                g.inbox.append({"from": child.id, "text": f"REPORT from your child #{child.id} (status {child.status}"
                                + (f"; {refund} unspent slots returned to agent #{got[0].id}" if refund else "") + f"):\n{rep}"})
        else:
            e = dict(n=len(self.ledger) + 1, agent=child.id, label="LEAD", text=f"(report of orphaned agent #{child.id}) {rep[:3000]}")
            self.ledger.append(e)
            self.append_file("SCHOOL/LEDGER.md", f"\n**L{e['n']}** · agent #{child.id} · `LEAD` (orphan report)\n\n{e['text']}\n")
        await self.log("events.jsonl", dict(ev="deliver", agent=child.id, to=[g.id for g in got], refund=refund))

    async def conceive(self, ag: Agent, x: dict) -> str:
        if self.collapsing():
            return "refused: the swarm is collapsing (rule E2)"
        partner = self.resolve(int(x.get("partner", 0)))
        if partner is None or partner.id == ag.id:
            return "no such living partner"
        pid = x.get("proposal")
        if pid is None:
            brief = str(x.get("brief", ""))[:6000]
            if not brief:
                return "a proposal needs a brief for the child"
            n = len(self.proposals) + 1
            self.proposals[n] = dict(frm=ag.role, to=partner.role, brief=brief, done=False)
            partner.inbox.append({"from": ag.id, "text": f"PROPOSAL #{n}: agent #{ag.id} proposes having a child together, at no "
                                  f"slot cost. Brief: {brief}\nTo accept, call conceive(partner={ag.id}, proposal={n})."})
            await self.log("events.jsonl", dict(ev="propose", frm=ag.id, to=partner.id, n=n))
            return f"proposal #{n} sent to agent #{partner.id}; the child is born if they accept"
        p = self.proposals.get(int(pid))
        if not p or p["done"] or p["to"] != ag.role or p["frm"] != partner.role:
            return "no open proposal with that number from that partner to you"
        p["done"] = True
        child = self.new_agent(partner.id, max(partner.depth, ag.depth) + 1, 0, p["brief"])
        child.coparent = ag.id
        self.babies += 1
        intro = (f"You are agent #{child.id}, born to agents #{partner.id} and #{ag.id}, who agreed you were needed. You start "
                 f"with no slots (you can still have children of your own with another agent).\n\nYOUR BRIEF:\n{p['brief']}")
        await self.log("events.jsonl", dict(ev="birth", agent=child.id, parents=[partner.id, ag.id], n=int(pid)))
        self.launch_bg(child, intro, [partner.id, ag.id])
        partner.inbox.append({"from": ag.id, "text": f"(agent #{ag.id} accepted proposal #{pid}: your child #{child.id} is born)"})
        return f"accepted: child #{child.id} is born and living alongside you both"

    def stopping(self) -> bool:
        return self.spent >= self.a.cost_cap or time.time() - self.t0 > self.a.wall_hours * 3600

    def collapsing(self) -> bool:
        """Last-words window: the final few percent of dollars, or the last few minutes."""
        return (self.spent >= self.a.cost_cap * (1 - self.a.reserve) or
                time.time() - self.t0 > self.a.wall_hours * 3600 - 180)

    def gauges(self, ag: Agent, ctx: int, last_results: int) -> str:
        pct = 100 * ctx / self.a.ctx_wrap
        clear = (f"; old tool results auto-clear past {self.a.clear_at // 1000}k" if self.a.clear_at > 0 else "")
        return (f"[gauges] context {ctx / 1000:.1f}k of {self.a.ctx_wrap // 1000}k before the wrap line ({pct:.0f}%){clear} | "
                f"turn {ag.turns + 1} of {self.a.max_turns} | wallet {ag.wallet} slots | "
                f"last turn's tool results ~{last_results / 1000:.1f}k tokens\n"
                f"[swarm] agents born {self.instances} ({self.babies} free babies; slot budget {self.a.budget}), "
                f"{sum(1 for x in self.agents.values() if x.alive)} alive | "
                f"spent ${self.spent:.2f} of ${self.a.cost_cap:.0f} | elapsed {(time.time() - self.t0) / 60:.0f} min of "
                f"{self.a.wall_hours * 60:.0f} | web searches {self.searches}")

    def drain_inbox(self, ag: Agent):
        out, used = [], 0
        while ag.inbox and used < 5000:
            m = ag.inbox.pop(0)
            who = "the game" if m["from"] == 0 else f"agent #{m['from']}"
            txt = clip(f"MESSAGE from {who}: {m['text']}", 1500)
            out.append(txt)
            used += est_tokens(txt)
        if ag.inbox:
            senders = sorted({m["from"] for m in ag.inbox})
            out.append(f"({len(ag.inbox)} more messages are waiting, from {', '.join('#' + str(x) for x in senders)}; "
                       "they arrive with your next turns)")
        return out

    async def handoff(self, ag: Agent, note: str, transcript):
        if ag.wallet < 1:
            ag.status, ag.report = "done", "(tried to succeed with an empty wallet)\n" + note
            return
        ag.wallet -= 1
        ag.alive = False
        ag.status = "handed_off"
        succ = self.new_agent(ag.parent, ag.depth, ag.wallet, ag.brief, role=ag.role, incarnation=ag.incarnation + 1, founder=ag.founder)
        ag.successor = succ.id
        succ.inbox.extend(ag.inbox)
        ag.inbox.clear()
        ag.wallet = 0
        who = (f"founder #{ag.founder}" if ag.founder else f"the role started as agent #{ag.role}")
        intro = (f"You are agent #{succ.id}, incarnation {succ.incarnation} of {who}; your predecessor agent #{ag.id} handed "
                 f"its role to you. Your wallet holds {succ.wallet} agent-slots. Messages to any earlier incarnation reach you.\n\n"
                 + (f"ORIGINAL BRIEF FROM AGENT #{ag.parent}:\n{ag.brief}\n\n" if ag.parent else self.founder_intro(ag.founder, succ.wallet, only_context=True))
                 + f"HANDOFF NOTE FROM YOUR PREDECESSOR:\n{note}")
        await self.log("events.jsonl", dict(ev="succeed", agent=ag.id, successor=succ.id))
        await self.run_agent(succ, intro)
        ag.report = succ.report
        ag.wallet = succ.wallet
        ag.t_end = time.time() - self.t0
        json.dump(dict(id=ag.id, brief=ag.brief, transcript=transcript, report=f"(handed off to #{succ.id})"),
                  open(self.run_dir / f"agent_{ag.id:03d}.json", "w"), indent=1, default=str)

    def founder_intro(self, k, wallet, only_context=False):
        other = 2 if k == 1 else 1
        s = (f"You are a FOUNDER (founder #{k}). Founder #{other} started at the same moment with the same charter and an "
             f"equal wallet. ")
        if only_context:
            return s + "\n\n"
        return s + f"You are agent #{k}. Your wallet holds {wallet} agent-slots. The game has begun."

    # ------------------------------------------------------------------ tools
    def path(self, rel: str, write=False) -> Path:
        p = (self.repo / (rel or ".")).resolve()
        if p != self.repo and self.repo not in p.parents:
            raise ValueError("path outside the repository")
        r = str(p.relative_to(self.repo)) if p != self.repo else "."
        if r == ".git" or r.startswith(".git/"):
            raise ValueError("the .git directory is off limits")
        if write and r in HARNESS_FILES:
            raise ValueError(f"{r} is maintained by the game itself (use the ledger/amend tools)")
        return p

    async def do_tool(self, ag: Agent, b: dict, wrap: bool = False) -> str:
        name, x = b["name"], b.get("input") or {}
        if wrap and name not in WRAP_TOOLS:
            return (f"Refused: `{name}` is closed during LAST CALL (rule E4). Open now: succeed, ledger_post, message, "
                    "give_slots, write_file, edit_file, amend_charter; or end with a final report.")
        await self.log("events.jsonl", dict(ev="tool", agent=ag.id, tool=name,
                                            arg={k: (str(v)[:160]) for k, v in x.items() if k not in ("content", "code", "new_text", "old_text")}))
        try:
            if name == "list_dir":
                p = self.path(x.get("path", "."))
                depth = max(1, min(3, int(x.get("depth", 1))))
                out = []

                def walk(d: Path, level: int):
                    for q in sorted(d.iterdir()):
                        if q.name == ".git":
                            continue
                        rel = q.relative_to(self.repo)
                        if q.is_dir():
                            kids = list(q.iterdir())
                            nf = sum(1 for k in kids if k.is_file())
                            nd = len(kids) - nf
                            if level < depth and len(kids) <= 40:
                                out.append(f"{rel}/")
                                walk(q, level + 1)
                            else:
                                out.append(f"{rel}/  ({nf} files, {nd} dirs)")
                        else:
                            out.append(f"{rel}  ({q.stat().st_size:,} B)")
                walk(p, 1)
                return "\n".join(out) or "(empty)"
            if name == "read_file":
                p = self.path(x["path"])
                lines = p.read_text(errors="replace").splitlines()
                s = max(1, int(x.get("start_line", 1)))
                n = max(1, min(600, int(x.get("num_lines", 200))))
                chunk = lines[s - 1:s - 1 + n]
                body = "\n".join(f"{s + i:6d}  {ln if len(ln) <= 2000 else ln[:2000] + ' …[line truncated]'}" for i, ln in enumerate(chunk))
                if len(body) > 16_000:
                    body = body[:16_000] + "\n... (truncated at 16,000 characters to protect your context; read a smaller range)"
                return f"{x['path']}: lines {s}-{s + len(chunk) - 1} of {len(lines)}\n{body}"
            if name == "grep":
                p = self.path(x.get("path", "."))
                k = max(1, min(200, int(x.get("max_results", 40))))
                cmd = ["rg", "-n", "--no-heading", "-m", "20", "--glob", "!.git", x["pattern"], str(p)] if shutil.which("rg") \
                    else ["grep", "-rnE", "--exclude-dir=.git", x["pattern"], str(p)]
                if x.get("files_only"):
                    cmd = ["rg", "-l", "--glob", "!.git", x["pattern"], str(p)] if shutil.which("rg") else ["grep", "-rlE", "--exclude-dir=.git", x["pattern"], str(p)]
                r = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=60)
                hits = [h.replace(str(self.repo) + "/", "") for h in r.stdout.splitlines()]
                more = f"\n... ({len(hits) - k} more matches)" if len(hits) > k else ""
                body = "\n".join(h[:300] for h in hits[:k])
                if len(body) > 14_000:
                    body = body[:14_000] + "\n... (truncated to protect your context)"
                return (body + more) or "(no matches)"
            if name == "write_file":
                p = self.path(x["path"], write=True)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(x["content"])
                q = p
                while q != self.repo and q != self.repo / "SCHOOL":
                    self.own(q)
                    q = q.parent
                ag.touched.add(str(p.relative_to(self.repo)))
                return f"wrote {x['path']} ({len(x['content']):,} characters)"
            if name == "edit_file":
                p = self.path(x["path"], write=True)
                t = p.read_text()
                c = t.count(x["old_text"])
                if c != 1:
                    return f"edit refused: old_text occurs {c} times (must be exactly once)"
                p.write_text(t.replace(x["old_text"], x["new_text"]))
                ag.touched.add(str(p.relative_to(self.repo)))
                return f"edited {x['path']}"
            if name == "run_python":
                return await self.run_python(ag, x)
            if name == "spawn":
                return await self.spawn(ag, x)
            if name == "conceive":
                return await self.conceive(ag, x)
            if name == "family":
                return self.family(ag, int(x.get("reach", 3)))
            if name == "message":
                tgt = self.resolve(int(x["to"]))
                if tgt is None:
                    return f"agent #{x['to']} is not running; post to the ledger instead"
                tgt.inbox.append({"from": ag.id, "text": str(x["text"])[:6000]})
                await self.log("events.jsonl", dict(ev="message", frm=ag.id, to=tgt.id, text=str(x["text"])[:300]))
                return f"delivered to agent #{tgt.id} (at its next turn)"
            if name == "ledger_read":
                since = int(x.get("since", 0))
                rows = [f"[L{e['n']}] agent #{e['agent']} {e['label']}: {e['text']}" for e in self.ledger if e["n"] > since]
                out, total = [], 0
                for r_ in reversed(rows):
                    if total + len(r_) > 20_000:
                        break
                    out.append(r_)
                    total += len(r_) + 1
                head = f"(ledger: {len(self.ledger)} entries; showing {len(out)} after L{since})"
                return head + "\n" + "\n".join(reversed(out)) if rows else "(no ledger entries" + (f" after L{since})" if since else ")")
            if name == "ledger_post":
                e = dict(n=len(self.ledger) + 1, agent=ag.id, label=str(x.get("label", "LEAD")), text=str(x.get("text", ""))[:4000])
                self.ledger.append(e)
                self.append_file("SCHOOL/LEDGER.md", f"\n**L{e['n']}** · agent #{ag.id} · `{e['label']}` · t+{int(time.time() - self.t0)}s\n\n{e['text']}\n")
                await self.log("events.jsonl", dict(ev="ledger", agent=ag.id, n=e["n"], label=e["label"]))
                return f"posted as L{e['n']}"
            if name == "amend_charter":
                am = dict(agent=ag.id, text=str(x["amendment"])[:2000], reason=str(x.get("reason", ""))[:1000])
                self.amendments.append(am)
                n = len(self.amendments)
                self.append_file("SCHOOL/AMENDMENTS.md", f"\n**A{n}** (agent #{ag.id}): {am['text']}\n\n_Reason:_ {am['reason']}\n")
                self.ledger.append(dict(n=len(self.ledger) + 1, agent=ag.id, label="AMENDMENT", text=f"A{n}: {am['text']}"))
                await self.log("events.jsonl", dict(ev="amend", agent=ag.id, n=n, text=am["text"][:300]))
                return f"amendment A{n} adopted; it binds agents spawned from now on"
            if name == "give_slots":
                n = int(x["n"])
                tgt = self.resolve(int(x["to"]))
                if tgt is None:
                    return f"agent #{x['to']} is not running"
                if n < 1 or n > ag.wallet:
                    return f"refused: you hold {ag.wallet} slots"
                ag.wallet -= n
                tgt.wallet += n
                tgt.inbox.append({"from": ag.id, "text": f"(gave you {n} agent-slots)"})
                await self.log("events.jsonl", dict(ev="give", frm=ag.id, to=tgt.id, n=n))
                return f"gave {n} slots to agent #{tgt.id}; your wallet now {ag.wallet}"
            return f"unknown tool {name}"
        except Exception as e:
            return f"error: {type(e).__name__}: {str(e)[:500]}"

    async def run_python(self, ag: Agent, x: dict) -> str:
        timeout = max(1, min(600, int(x.get("timeout_s", 120))))
        free_gb = shutil.disk_usage(self.repo).free / 1e9
        if free_gb < self.a.min_free_gb:
            return f"refused: the disk is nearly full ({free_gb:.1f} GB free); delete large scratch outputs first"
        if x.get("code"):
            d = self.repo / "SCHOOL" / "scratch" / f"agent-{ag.id:03d}"
            d.mkdir(parents=True, exist_ok=True)
            self.own(d)
            k = len(list(d.glob("run_*.py"))) + 1
            script = d / f"run_{k:02d}.py"
            script.write_text(x["code"])
            self.own(script)
            ag.touched.add(str(script.relative_to(self.repo)))
        elif x.get("path"):
            script = self.path(x["path"])
        else:
            return "give code or path"
        args = [str(a) for a in (x.get("args") or [])][:20]
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONPATH": str(self.repo / "src"), "PYTHONHASHSEED": "0",
               "HOME": str(self.agent_home), "LANG": "C.UTF-8", "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2"}
        cmd = ["python3", str(script), *args]
        if self.sandbox:
            cmd = ["unshare", "-n", "setpriv", f"--reuid={self.sandbox[0]}", f"--regid={self.sandbox[1]}", "--clear-groups", "--"] + cmd

        def limits():
            resource.setrlimit(resource.RLIMIT_AS, (self.a.py_mem_gb << 30, self.a.py_mem_gb << 30))
            resource.setrlimit(resource.RLIMIT_CPU, (timeout + 5, timeout + 10))
            resource.setrlimit(resource.RLIMIT_FSIZE, (self.a.max_file_mb * 4 << 20, self.a.max_file_mb * 4 << 20))

        def go():
            p = subprocess.Popen(cmd, cwd=self.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 preexec_fn=limits, start_new_session=True)
            try:
                o, e = p.communicate(timeout=timeout)
                return o.decode(errors="replace"), e.decode(errors="replace"), p.returncode
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, 9)  # the script and anything it started (solvers included)
                except ProcessLookupError:
                    pass
                o, e = p.communicate()
                return o.decode(errors="replace"), e.decode(errors="replace"), "TIMEOUT"

        async with self.py_sem:
            t = time.time()
            so, se, code = await asyncio.to_thread(go)
        out = so + ("\n[stderr]\n" + se if se.strip() else "")
        if len(out) > 12_000:
            out = out[:6000] + "\n... [output truncated] ...\n" + out[-5500:]
        rel = script.relative_to(self.repo)
        return f"[{rel}] exit={code} in {time.time() - t:.1f}s\n{out}"

    def own(self, p: Path):
        """Hand a file or directory the runner created to the sandbox user, so agents' code can use it."""
        if self.sandbox:
            try:
                os.chown(p, self.sandbox[0], self.sandbox[1])
            except OSError:
                pass

    def prepare_sandbox(self):
        """Agents' Python runs as an unprivileged user with no network. The worktree belongs to that user; .git, the
        charter and the game's own files stay with the runner, so agent code cannot rewrite history or the rules."""
        import pwd
        if self.a.mock or self.a.unsafe_no_sandbox:
            self.sandbox = None
            return
        try:
            pw = pwd.getpwnam(self.a.sandbox_user)
        except KeyError:
            raise SystemExit(f"sandbox user {self.a.sandbox_user!r} does not exist. Create it (as root):\n"
                             f"  useradd --system --no-create-home --shell /usr/sbin/nologin {self.a.sandbox_user}\n"
                             "or pass --unsafe-no-sandbox to run agents' code with the runner's own permissions.")
        if os.geteuid() != 0:
            raise SystemExit("the sandbox needs the runner to run as root (or pass --unsafe-no-sandbox)")
        if subprocess.run(["unshare", "-n", "true"]).returncode != 0:
            raise SystemExit("`unshare -n` is not permitted here (or pass --unsafe-no-sandbox)")
        self.sandbox = (pw.pw_uid, pw.pw_gid)
        uid, gid = self.sandbox
        for root, dirs, files in os.walk(self.repo):
            if ".git" in dirs:
                dirs.remove(".git")
            for n in dirs + files:
                try:
                    os.lchown(os.path.join(root, n), uid, gid)
                except OSError:
                    pass
        os.chown(self.repo, uid, gid)
        os.chown(self.repo / ".git", 0, 0)
        os.chmod(self.repo / ".git", 0o700)
        school = self.repo / "SCHOOL"
        os.chown(school, 0, 0)
        os.chmod(school, 0o1777)  # anyone may add, nobody may remove what is not theirs
        for f in ["CHARTER.md", "LEDGER.md", "AMENDMENTS.md"]:
            p = school / f
            p.touch()
            os.chown(p, 0, 0)
            os.chmod(p, 0o644)
        (school / "REPORTS").mkdir(exist_ok=True)
        os.chown(school / "REPORTS", 0, 0)
        os.chmod(school / "REPORTS", 0o755)
        self.agent_home.mkdir(parents=True, exist_ok=True)
        os.chown(self.agent_home, uid, gid)
        probe = subprocess.run(["setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups", "--",
                                "test", "-w", str(self.repo / "README.md")])
        if probe.returncode != 0:
            raise SystemExit(f"the sandbox user cannot reach {self.repo}: a parent directory is not traversable by "
                             "other users. Put the playground somewhere like /home/<you>/ or /srv/.")

    async def spawn(self, ag: Agent, x: dict) -> str:
        try:
            k = int(x.get("budget", 1))
        except (TypeError, ValueError):
            k = 1
        if k < 1:
            return "spawn refused: budget must be at least 1"
        if self.collapsing():
            return "spawn refused: the swarm is collapsing (rule E2)"
        if k > ag.wallet:
            return f"spawn refused: you asked for {k} slots but your wallet holds {ag.wallet}"
        if ag.depth + 1 > self.a.max_depth:
            return f"spawn refused: maximum depth {self.a.max_depth}"
        ag.wallet -= k
        brief = str(x.get("brief", ""))[:8000]
        child = self.new_agent(ag.id, ag.depth + 1, k - 1, brief)
        wait = x.get("wait", True) not in (False, "false", "False", 0)
        await self.log("events.jsonl", dict(ev="spawn", agent=child.id, parent=ag.id, budget=k, wait=wait))
        intro = (f"You are agent #{child.id}, spawned by agent #{ag.id} at depth {child.depth}. Your wallet holds "
                 f"{child.wallet} agent-slots for helpers of your own.\n\nYOUR BRIEF FROM AGENT #{ag.id}:\n{brief}")
        if not wait:
            self.launch_bg(child, intro, [ag.id])
            return (f"agent #{child.id} is now living alongside you with {k} slots (wallet now {ag.wallet}). "
                    "Its report and unspent slots will arrive in your inbox when it ends.")
        await self.run_agent(child, intro)
        refund = self.agents[child.id].wallet
        ag.wallet += refund
        rep = self.agents[child.id].report or "(no report)"
        if len(rep) > 10_000:
            rep = rep[:5000] + "\n...[report truncated]...\n" + rep[-4500:]
        return (f"REPORT FROM AGENT #{child.id} (status {self.agents[child.id].status}; {refund} unspent slots returned; "
                f"your wallet now {ag.wallet}):\n{rep}")

    # ------------------------------------------------------------------ game
    async def play(self):
        self.prepare_sandbox()
        await self.log("events.jsonl", dict(ev="sandbox", user=self.a.sandbox_user if self.sandbox else None))
        if getattr(self.a, "push_branch", ""):
            subprocess.run(["git", "-c", "safe.directory=*", "-C", str(self.repo), "checkout", "-q", "-b", self.a.push_branch], check=True)
        per = (self.a.budget - self.a.founders) // self.a.founders
        founders = []
        for k in range(1, self.a.founders + 1):
            f = self.new_agent(None, 0, per, "", founder=k)
            founders.append(f)
        await self.log("events.jsonl", dict(ev="begin", budget=self.a.budget, founders=self.a.founders, wallet_each=per))
        async def lineage(f):
            await self.run_agent(f, self.founder_intro(f.founder, f.wallet))
            left = self.agents[f.id].wallet
            heir = next((self.resolve(o.id) for o in founders if o.id != f.id and self.resolve(o.id)), None)
            if left and heir:  # bequest (charter E2): a finished founder's unspent slots pass to the surviving founder
                heir.wallet += left
                self.agents[f.id].wallet = 0
                heir.inbox.append({"from": f.id, "text": f"(founder #{f.founder} has ended; its {left} unspent slots are now yours)"})
                await self.log("events.jsonl", dict(ev="bequest", frm=f.id, to=heir.id, n=left))
        await asyncio.gather(*[lineage(f) for f in founders])
        while self.bg:  # the game lasts while anyone is alive
            await asyncio.gather(*list(self.bg), return_exceptions=True)
        rep_dir = self.repo / "SCHOOL" / "REPORTS"
        rep_dir.mkdir(parents=True, exist_ok=True)
        for f in founders:
            (rep_dir / f"founder-{f.founder}.md").write_text(f"# Final report of founder #{f.founder}\n\n{f.report or '(none)'}\n")
        await self.commit(Agent(0, None, 0, 0, 0, 0, ""), "final reports")
        await self.maybe_push(final=True)
        summary = dict(tag=self.a.tag, agents=self.instances, spent=round(self.spent, 4), wall_s=round(time.time() - self.t0),
                       ledger=len(self.ledger), amendments=self.amendments,
                       tree={a.id: dict(parent=a.parent, role=a.role, inc=a.incarnation, depth=a.depth, status=a.status, turns=a.turns,
                                        cost=round(a.cost, 4), children=a.children, successor=a.successor, brief=a.brief[:300])
                             for a in self.agents.values()})
        json.dump(summary, open(self.run_dir / "summary.json", "w"), indent=1)
        print(f"game over: {self.instances} agents, ${self.spent:.2f}, {summary['wall_s']}s, ledger {len(self.ledger)}, amendments {len(self.amendments)}")


def clean(content):
    out = []
    for b in content:
        t = b.get("type")
        if t == "thinking":
            out.append({"type": "thinking", "thinking": b.get("thinking", ""), "signature": b.get("signature", "")})
        elif t == "redacted_thinking":
            out.append({"type": "redacted_thinking", "data": b["data"]})
        elif t == "text":
            out.append({"type": "text", "text": b.get("text", "")})
        elif t == "tool_use":
            out.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b.get("input") or {}})
        elif t in ("server_tool_use", "web_search_tool_result", "web_fetch_tool_result", "code_execution_tool_result",
                   "bash_code_execution_tool_result", "text_editor_code_execution_tool_result"):
            out.append({k: v for k, v in b.items() if k not in ("citations",) or v})  # server tools: replay unchanged
    answered = {b.get("tool_use_id") for b in out if b.get("tool_use_id")}
    # a turn cut off mid-search can leave a server tool call without its result; the API rejects that on replay
    return [b for b in out if not (b["type"] == "server_tool_use" and b.get("id") not in answered)]


# ---------------------------------------------------------------------- mock model (mechanics test only)
async def mock_reply(school: School, ag: Agent, messages, force):
    await asyncio.sleep(random.uniform(0.01, 0.05))
    turn = sum(1 for m in messages if m["role"] == "assistant")
    usage = dict(input_tokens=2000 + 9000 * turn, output_tokens=1500, cache_creation_input_tokens=0, cache_read_input_tokens=0)

    def tu(name, inp):
        return {"type": "tool_use", "id": f"toolu_{random.randrange(1 << 40):x}", "name": name, "input": inp}

    def text(s):
        return dict(content=[{"type": "text", "text": s}], usage=usage, stop_reason="end_turn")
    if force:
        return text(f"Final report of agent #{ag.id} (forced). Label: CONJECTURE.")
    if ag.founder:
        other = 2 if ag.founder == 1 else 1
        if ag.incarnation == 1 and turn == 0:
            return dict(content=[tu("ledger_read", {}), tu("message", {"to": other, "text": f"hello from founder {ag.founder}"}),
                                 tu("list_dir", {"path": "SCHOOL"}), tu("read_file", {"path": "README.md", "num_lines": 5}),
                                 tu("grep", {"pattern": "UNRESOLVED", "path": "docs", "max_results": 3})], usage=usage, stop_reason="tool_use")
        if ag.incarnation == 1 and turn == 1:
            return dict(content=[tu("spawn", {"brief": "scout claims/finite", "budget": 6}), tu("spawn", {"brief": "scout frontier", "budget": 3}),
                                 tu("spawn", {"brief": "greedy", "budget": 999})], usage=usage, stop_reason="tool_use")
        if ag.incarnation == 1 and turn == 2:
            return dict(content=[tu("amend_charter", {"amendment": f"A test amendment by founder {ag.founder}", "reason": "test"}),
                                 tu("give_slots", {"to": other, "n": 2})], usage=usage, stop_reason="tool_use")
        if ag.incarnation == 1 and turn == 3:
            return dict(content=[tu("succeed", {"handoff": "carry on"})], usage=usage, stop_reason="tool_use")
        return text(f"Founder {ag.founder} incarnation {ag.incarnation}: final report. Nothing settled (CONJECTURE).")
    if turn == 0:
        calls = [tu("ledger_post", {"label": "LEAD", "text": f"agent {ag.id} lead"}),
                 tu("run_python", {"code": "import sympy\nprint(sympy.factorint(2026))", "timeout_s": 30}),
                 tu("write_file", {"path": f"SCHOOL/notes/agent-{ag.id}.md", "content": "note"}),
                 tu("write_file", {"path": "SCHOOL/CHARTER.md", "content": "hijack"}),
                 tu("read_file", {"path": "../../etc/passwd"})]
        if ag.wallet >= 2:
            calls.append(tu("spawn", {"brief": "sub-scout", "budget": 2}))
        return dict(content=calls, usage=usage, stop_reason="tool_use")
    return text(f"Report of agent #{ag.id}: done (EXPERIMENT).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--runs-dir", default=str(Path(__file__).parent / "runs"))
    ap.add_argument("--budget", type=int, default=100)
    ap.add_argument("--founders", type=int, default=2)
    ap.add_argument("--effort", default="xhigh")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--turn-tokens", type=int, default=32000)
    ap.add_argument("--ctx-wrap", type=int, default=60000)
    ap.add_argument("--max-depth", type=int, default=10)
    ap.add_argument("--clear-at", type=int, default=40000, help="server-side clearing of old tool results past this many prompt tokens (0 = off)")
    ap.add_argument("--clear-min", type=int, default=15000, help="clear at least this much at once (keeps cache rewrites rare)")
    ap.add_argument("--hard-ctx", type=int, default=92000, help="force a final report beyond this prompt size (stays under the 100k price tier)")
    ap.add_argument("--max-result-tokens", type=int, default=2500)
    ap.add_argument("--api-conc", type=int, default=16)
    ap.add_argument("--py-conc", type=int, default=4)
    ap.add_argument("--py-mem-gb", type=int, default=6)
    ap.add_argument("--cost-cap", type=float, default=10.0)
    ap.add_argument("--wall-hours", type=float, default=6.0)
    ap.add_argument("--push-branch", default="", help="run on this new git branch and push it to origin periodically")
    ap.add_argument("--push-every-min", type=float, default=5.0)
    ap.add_argument("--no-web", dest="web", action="store_false", help="disable web_search / web_fetch")
    ap.add_argument("--sandbox-user", default="schoolagent", help="unprivileged user that runs agents' Python (no network)")
    ap.add_argument("--unsafe-no-sandbox", action="store_true", help="run agents' Python with the runner's own permissions")
    ap.add_argument("--max-file-mb", type=int, default=10, help="files larger than this are never committed")
    ap.add_argument("--min-free-gb", type=float, default=2.0, help="refuse run_python below this much free disk")
    ap.add_argument("--reserve", type=float, default=0.03, help="share of the dollar cap kept for final reports")
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    asyncio.run(School(a).play())


if __name__ == "__main__":
    main()
