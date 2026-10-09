"""Experiment harness: Fable 5.1 solo vs Haiku 5.5 (solo / 50-vote / 50-swarm).

All model calls go DIRECTLY to https://api.anthropic.com with the key in
SWARM_API_KEY (never the session's own proxy). Every call is logged with
usage and cost; a global dollar cap stops new calls once reached.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from charters import build_system

FABLE = "claude-fable-5-1"
HAIKU = "claude-haiku-5-5"

PRICES = {  # USD per million tokens (platform.claude.com pricing page, 2026-10-09)
    FABLE: dict(inp=10.0, out=50.0, cw=12.5, cr=0.25),
    HAIKU: dict(inp=0.10, out=0.50, cw=0.125, cr=0.01),
}
HAIKU_LONG = dict(inp=0.50, out=2.50, cw=0.625, cr=0.05)  # prompts > 100k tokens


def call_cost(model: str, u: dict, batch: bool = False) -> float:
    inp = u.get("input_tokens") or 0
    out = u.get("output_tokens") or 0
    cw = u.get("cache_creation_input_tokens") or 0
    cr = u.get("cache_read_input_tokens") or 0
    r = PRICES[model]
    if model == HAIKU and inp + cw + cr > 100_000:
        r = HAIKU_LONG
    c = (inp * r["inp"] + out * r["out"] + cw * r["cw"] + cr * r["cr"]) / 1e6
    return c * 0.5 if batch else c


class BudgetStop(Exception):
    pass


ANSWER_RE = re.compile(r"ANSWER:\s*\**\s*(-?[\d][\d,]*)")


def parse_answer(text: str | None):
    if not text:
        return None
    m = ANSWER_RE.findall(text)
    if not m:
        return None
    try:
        return int(m[-1].replace(",", ""))
    except ValueError:
        return None


def text_of(content: list[dict]) -> str:
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


# ----------------------------------------------------------------------------- API layer
class API:
    def __init__(self, out_dir: Path, cap_usd: float, mock: bool = False,
                 haiku_conc: int = 48, fable_conc: int = 16):
        self.out_dir = out_dir
        self.cap = cap_usd
        self.spent = 0.0
        self.mock = mock
        self.sem = {HAIKU: asyncio.Semaphore(haiku_conc), FABLE: asyncio.Semaphore(fable_conc)}
        self.log_lock = asyncio.Lock()
        self.ncalls = 0
        if not mock:
            import anthropic
            key = os.environ.get("SWARM_API_KEY") or Path(os.environ["SWARM_API_KEY_FILE"]).read_text().strip()
            hdrs = {}
            if os.environ.get("SWARM_WORKSPACE_ID"):  # needed for multi-workspace (user-scoped) keys
                hdrs["anthropic-workspace-id"] = os.environ["SWARM_WORKSPACE_ID"]
            self.client = anthropic.AsyncAnthropic(
                api_key=key, base_url="https://api.anthropic.com", max_retries=10,
                timeout=anthropic.Timeout(1800.0, connect=30.0), default_headers=hdrs)

    async def create(self, *, tag: dict, model: str, system: str | None, messages: list, max_tokens: int,
                     effort: str, tools: list | None = None, tool_choice: dict | None = None) -> dict:
        if self.spent >= self.cap:
            raise BudgetStop(f"cap ${self.cap:.2f} reached (spent ${self.spent:.2f})")
        kw = dict(model=model, max_tokens=max_tokens, messages=messages,
                  thinking={"type": "adaptive"},
                  extra_body={"output_config": {"effort": effort}, "cache_control": {"type": "ephemeral"}})
        if system:
            kw["system"] = system
        if tools:
            kw["tools"] = tools
            kw["tool_choice"] = tool_choice or {"type": "auto"}
        async with self.sem[model]:
            t0 = time.time()
            if self.mock:
                msg = await mock_response(kw, tag)
            else:
                msg = await self._stream(kw)
            dt = time.time() - t0
        usage = msg["usage"]
        cost = call_cost(model, usage)
        self.spent += cost
        self.ncalls += 1
        rec = dict(t=time.time(), latency=round(dt, 2), model=model, effort=effort, stop=msg.get("stop_reason"),
                   usage={k: usage.get(k) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")},
                   thinking_tokens=(usage.get("output_tokens_details") or {}).get("thinking_tokens"),
                   cost=cost, **tag)
        async with self.log_lock:
            with open(self.out_dir / "calls.jsonl", "a") as f:
                f.write(json.dumps(rec) + "\n")
        msg["_cost"] = cost
        msg["_latency"] = dt
        return msg

    async def _stream(self, kw):
        import anthropic
        last = None
        for attempt in range(4):
            try:
                async with self.client.messages.stream(**kw) as s:
                    m = await s.get_final_message()
                d = m.model_dump(mode="json", exclude_none=True)
                return d
            except (anthropic.APIConnectionError, anthropic.APITimeoutError, anthropic.InternalServerError,
                    anthropic.RateLimitError) as e:
                last = e
                await asyncio.sleep(10 * (attempt + 1) + random.random() * 5)
            except anthropic.APIStatusError as e:
                if getattr(e, "status_code", 0) in (529, 503, 502, 500):
                    last = e
                    await asyncio.sleep(15 * (attempt + 1))
                else:
                    raise
        raise last


def clean_content(content: list[dict]) -> list[dict]:
    """Blocks to send back as an assistant turn (keep thinking + signatures intact)."""
    keep = []
    for b in content:
        t = b.get("type")
        if t == "thinking":
            keep.append({"type": "thinking", "thinking": b.get("thinking", ""), "signature": b.get("signature", "")})
        elif t == "redacted_thinking":
            keep.append({"type": "redacted_thinking", "data": b["data"]})
        elif t == "text":
            keep.append({"type": "text", "text": b.get("text", "")})
        elif t == "tool_use":
            keep.append({"type": "tool_use", "id": b["id"], "name": b["name"], "input": b.get("input", {})})
    return keep


# ----------------------------------------------------------------------------- solo arms
SOLO_PROMPT = """Solve the following problem. You have no tools, calculator, or code execution, so reason carefully by hand and check your work. End your response with a final line of exactly the form:
ANSWER: <integer>

PROBLEM
{statement}"""


async def run_solo(api: API, model: str, effort: str, problem: dict, arm: str, sample: int, max_tokens: int) -> dict:
    t0 = time.time()
    tag = dict(arm=arm, pid=problem["id"], sample=sample, agent=0)
    try:
        msg = await api.create(tag=tag, model=model, system=None, max_tokens=max_tokens, effort=effort,
                               messages=[{"role": "user", "content": SOLO_PROMPT.format(statement=problem["statement"])}])
    except BudgetStop as e:
        return dict(arm=arm, pid=problem["id"], sample=sample, error=str(e))
    except Exception as e:  # keep the run going; record the failure
        return dict(arm=arm, pid=problem["id"], sample=sample, error=f"{type(e).__name__}: {e}")
    txt = text_of(msg["content"])
    ans = parse_answer(txt)
    return dict(arm=arm, pid=problem["id"], sample=sample, answer=ans, correct=(ans == problem["answer"]),
                cost=msg["_cost"], wall=time.time() - t0, usage=msg["usage"], stop=msg.get("stop_reason"),
                text=txt, thinking_chars=sum(len(b.get("thinking", "")) for b in msg["content"] if b.get("type") == "thinking"))


async def run_vote(api: API, problem: dict, n: int, effort: str, max_tokens: int) -> dict:
    t0 = time.time()
    samples = await asyncio.gather(*[run_solo(api, HAIKU, effort, problem, "haiku_vote", i, max_tokens) for i in range(n)])
    answers = [s.get("answer") for s in samples if s.get("answer") is not None]
    counts = Counter(answers)
    top = counts.most_common()
    maj = top[0][0] if top else None
    tie = len(top) > 1 and top[0][1] == top[1][1]
    return dict(arm="haiku_vote", pid=problem["id"], answer=maj, correct=(maj == problem["answer"]), tie=tie,
                n=n, n_parsed=len(answers), distribution=[[a, c] for a, c in top],
                single_acc=sum(1 for s in samples if s.get("correct")) / n,
                pass_at_n=any(s.get("correct") for s in samples),
                cost=sum(s.get("cost", 0) for s in samples), wall=time.time() - t0,
                errors=[s["error"] for s in samples if "error" in s],
                samples=[{k: s.get(k) for k in ("sample", "answer", "correct", "cost", "usage", "stop", "wall")} for s in samples],
                texts=[s.get("text") for s in samples])


async def run_vote_batch(api: API, problems: list[dict], n: int, effort: str, max_tokens: int, out_dir: Path) -> list[dict]:
    """Same as run_vote for many problems at once, through the Message Batches API (50% price, separate rate limits)."""
    t0 = time.time()
    reqs = []
    for p in problems:
        for i in range(n):
            reqs.append({"custom_id": f"{p['id']}__{i}", "params": {
                "model": HAIKU, "max_tokens": max_tokens, "thinking": {"type": "adaptive"},
                "output_config": {"effort": effort},
                "messages": [{"role": "user", "content": SOLO_PROMPT.format(statement=p["statement"])}]}})
    idf = out_dir / "vote_batch_id.txt"
    if idf.exists():  # resume an already-submitted batch instead of paying twice
        batch = await api.client.messages.batches.retrieve(idf.read_text().strip())
        print(f"resuming vote batch {batch.id}", flush=True)
    else:
        batch = await api.client.messages.batches.create(requests=reqs)
        idf.write_text(batch.id)
        print(f"vote batch {batch.id}: {len(reqs)} requests submitted", flush=True)
    while True:
        await asyncio.sleep(30)
        b = await api.client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        rc = b.request_counts
        print(f"[batch] processing={rc.processing} succeeded={rc.succeeded} errored={rc.errored}", flush=True)
    samples = {p["id"]: [] for p in problems}
    res = await api.client.messages.batches.results(batch.id)
    async for entry in res:
        pid, i = entry.custom_id.split("__")
        s = dict(sample=int(i))
        if entry.result.type == "succeeded":
            m = entry.result.message.model_dump(mode="json", exclude_none=True)
            cost = call_cost(HAIKU, m["usage"], batch=True)
            api.spent += cost
            txt = text_of(m["content"])
            ans = parse_answer(txt)
            exp = next(p["answer"] for p in problems if p["id"] == pid)
            s.update(answer=ans, correct=(ans == exp), cost=cost, usage=m["usage"], stop=m.get("stop_reason"), text=txt)
            with open(out_dir / "calls.jsonl", "a") as f:
                f.write(json.dumps(dict(t=time.time(), latency=None, model=HAIKU, effort=effort, stop=m.get("stop_reason"),
                                        usage={k: m["usage"].get(k) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")},
                                        thinking_tokens=(m["usage"].get("output_tokens_details") or {}).get("thinking_tokens"),
                                        cost=cost, arm="haiku_vote", pid=pid, sample=int(i), agent=0, batch=True)) + "\n")
        else:
            s.update(error=entry.result.type)
        samples[pid].append(s)
    outs = []
    for p in problems:
        ss = sorted(samples[p["id"]], key=lambda s: s["sample"])
        answers = [s.get("answer") for s in ss if s.get("answer") is not None]
        top = Counter(answers).most_common()
        maj = top[0][0] if top else None
        outs.append(dict(arm="haiku_vote", pid=p["id"], answer=maj, correct=(maj == p["answer"]),
                         tie=len(top) > 1 and top[0][1] == top[1][1], n=n, n_parsed=len(answers),
                         distribution=[[a, c] for a, c in top], single_acc=sum(1 for s in ss if s.get("correct")) / n,
                         pass_at_n=any(s.get("correct") for s in ss), cost=sum(s.get("cost", 0) for s in ss),
                         wall=time.time() - t0, batch_id=batch.id, errors=[s["error"] for s in ss if "error" in s],
                         samples=[{k: s.get(k) for k in ("sample", "answer", "correct", "cost", "usage", "stop")} for s in ss],
                         texts=[s.get("text") for s in ss]))
    return outs


# ----------------------------------------------------------------------------- the swarm
TOOLS = [
    {"name": "spawn", "description": "Start a child agent (same model, same charter) with a brief and a number of agent-slots taken from your wallet (>= 1; the child occupies one, the rest it may spend on its own children). Returns the child's final report. Several spawn calls in one turn run in parallel.",
     "input_schema": {"type": "object", "properties": {
         "brief": {"type": "string", "description": "Scope, what is already known, and exactly what to report back."},
         "budget": {"type": "integer", "minimum": 1, "description": "Agent-slots to hand the child."}},
         "required": ["brief", "budget"]}},
    {"name": "ledger_read", "description": "Read the swarm's shared ledger (most recent entries if it is long).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "ledger_post", "description": "Post an entry to the shared ledger.",
     "input_schema": {"type": "object", "properties": {
         "label": {"type": "string", "enum": ["DERIVED", "CHECKED", "CONJECTURE", "DEAD_END", "LEAD"]},
         "text": {"type": "string"}}, "required": ["label", "text"]}},
    {"name": "amend_charter", "description": "Amend THE GAME (not the entrenched rules). Binds agents spawned afterwards; announced on the ledger.",
     "input_schema": {"type": "object", "properties": {
         "amendment": {"type": "string"}, "reason": {"type": "string"}}, "required": ["amendment", "reason"]}},
]


@dataclass
class Agent:
    id: int
    parent: int | None
    depth: int
    wallet: int  # slots it may hand out (excludes itself)
    brief: str
    turns: int = 0
    cost: float = 0.0
    out_tokens: int = 0
    max_prompt: int = 0
    report: str | None = None
    status: str = "running"
    children: list = field(default_factory=list)
    transcript: list = field(default_factory=list)
    t_start: float = 0.0
    t_end: float = 0.0


class Swarm:
    def __init__(self, api: API, problem: dict, game: str = "procedural", budget: int = 50, effort: str = "high",
                 max_turns: int = 14, ctx_wrap: int = 85_000, max_tokens: int = 32_000, max_depth: int = 8,
                 arm: str = "haiku_swarm", cutoff_policy: str = "force"):
        self.api, self.problem, self.game, self.budget = api, problem, game, budget
        self.effort, self.max_turns, self.ctx_wrap, self.max_tokens, self.max_depth = effort, max_turns, ctx_wrap, max_tokens, max_depth
        self.arm = arm
        self.cutoff_policy = cutoff_policy
        self.agents: dict[int, Agent] = {}
        self.ledger: list[dict] = []
        self.amendments: list[dict] = []
        self.next_id = 1
        self.events: list[dict] = []
        self.t0 = time.time()

    def ev(self, kind, **kw):
        self.events.append(dict(t=round(time.time() - self.t0, 2), kind=kind, **kw))

    def system(self):
        return build_system(self.game, self.problem["statement"], self.budget, [a["text"] for a in self.amendments],
                            turns=self.max_turns, turn_tokens=self.max_tokens, ctx=self.ctx_wrap)

    def new_agent(self, parent, depth, wallet, brief) -> Agent:
        a = Agent(id=self.next_id, parent=parent, depth=depth, wallet=wallet, brief=brief, t_start=time.time() - self.t0)
        self.next_id += 1
        self.agents[a.id] = a
        if parent:
            self.agents[parent].children.append(a.id)
        return a

    def ledger_text(self, limit_chars=24_000):
        lines = [f"[L{e['n']}] agent#{e['agent']} {e['label']}: {e['text']}" for e in self.ledger]
        out, total = [], 0
        for ln in reversed(lines):
            if total + len(ln) > limit_chars:
                break
            out.append(ln)
            total += len(ln) + 1
        omitted = len(lines) - len(out)
        head = f"(ledger has {len(lines)} entries" + (f"; {omitted} oldest omitted)" if omitted else ")")
        return head + "\n" + "\n".join(reversed(out)) if lines else "(ledger is empty)"

    async def run(self) -> dict:
        root = self.new_agent(None, 0, self.budget - 1, "")
        intro = (f"You are agent #1, the FOUNDER. Your wallet holds {root.wallet} agent-slots "
                 f"(the swarm cap is {self.budget} agents including you). You alone give the final answer. "
                 "Play the game: solve the PROBLEM.")
        self.ev("spawn", agent=1, parent=None, budget=self.budget)
        try:
            await asyncio.wait_for(self.run_agent(root, intro), timeout=60 * 60)
        except asyncio.TimeoutError:
            root.status = "timeout"
        ans = parse_answer(root.report)
        if ans is None:  # fall back to the last ANSWER line anywhere in the founder's own text
            for blk in reversed(root.transcript):
                ans = parse_answer(blk) if isinstance(blk, str) else None
                if ans is not None:
                    break
        return dict(arm=self.arm, game=self.game, pid=self.problem["id"], answer=ans, correct=(ans == self.problem["answer"]),
                    cost=sum(a.cost for a in self.agents.values()), wall=time.time() - self.t0,
                    n_agents=len(self.agents), max_depth=max(a.depth for a in self.agents.values()),
                    n_ledger=len(self.ledger), amendments=self.amendments, ledger=self.ledger, events=self.events,
                    agents={a.id: dict(parent=a.parent, depth=a.depth, brief=a.brief, report=a.report, status=a.status,
                                       turns=a.turns, cost=a.cost, out_tokens=a.out_tokens, max_prompt=a.max_prompt,
                                       children=a.children, t_start=a.t_start, t_end=a.t_end, wallet_end=a.wallet)
                            for a in self.agents.values()},
                    transcripts={a.id: a.transcript for a in self.agents.values()})

    async def run_agent(self, ag: Agent, intro: str):
        messages = [{"role": "user", "content": intro}]
        force = False
        nudge = None
        while True:
            if ag.turns >= self.max_turns - 1:
                force = True
                nudge = "WRAP UP NOW (rule E6): you are out of turns. Write your report without calling any tool."
            if nudge:
                last = messages[-1]
                if last["role"] == "user":
                    if isinstance(last["content"], str):
                        last["content"] = [{"type": "text", "text": last["content"]}]
                    last["content"].append({"type": "text", "text": nudge})
                else:
                    messages.append({"role": "user", "content": nudge})
                nudge = None
            tag = dict(arm=self.arm, game=self.game, pid=self.problem["id"], agent=ag.id, turn=ag.turns)
            try:
                msg = await self.api.create(tag=tag, model=HAIKU, system=self.system(), messages=messages,
                                            max_tokens=self.max_tokens, effort=self.effort, tools=TOOLS,
                                            tool_choice={"type": "none"} if force else {"type": "auto"})
            except BudgetStop as e:
                ag.status, ag.report = "budget_stop", f"(agent halted: {e})"
                break
            except Exception as e:
                ag.status, ag.report = "error", f"(agent crashed: {type(e).__name__}: {str(e)[:300]})"
                self.ev("error", agent=ag.id, err=str(e)[:300])
                break
            ag.turns += 1
            ag.cost += msg["_cost"]
            u = msg["usage"]
            ag.out_tokens += u.get("output_tokens") or 0
            prompt = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
            ag.max_prompt = max(ag.max_prompt, prompt)
            content = clean_content(msg["content"])
            ag.transcript.append(text_of(content))
            messages.append({"role": "assistant", "content": content})
            uses = [b for b in content if b["type"] == "tool_use"]
            if not uses:
                if msg.get("stop_reason") == "max_tokens" and not force:
                    if self.cutoff_policy == "continue":
                        nudge = ("Your last turn hit the per-turn output limit and was cut off; its unfinished work is lost. "
                                 "Continue: you may still use your tools (for example, split long computations across helpers).")
                        messages.append({"role": "user", "content": []})
                    else:
                        force, nudge = True, "Your last message hit the output limit. Write your report now, briefly, without calling any tool."
                    continue
                ag.report = text_of(content)
                ag.status = "done" if ag.status == "running" else ag.status
                break
            results = await asyncio.gather(*[self.do_tool(ag, b) for b in uses])
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": b["id"], "content": r}
                                                         for b, r in zip(uses, results)]})
            if prompt > self.ctx_wrap and not force:
                force = True
                nudge = "WRAP UP NOW (rule E6): your context is nearly full. Write your report without calling any tool."
        ag.t_end = time.time() - self.t0
        self.ev("finish", agent=ag.id, status=ag.status, turns=ag.turns)

    async def do_tool(self, ag: Agent, b: dict) -> str:
        name, inp = b["name"], b.get("input", {}) or {}
        if name == "ledger_read":
            self.ev("ledger_read", agent=ag.id)
            return self.ledger_text()
        if name == "ledger_post":
            label = str(inp.get("label", "LEAD"))[:12]
            text = str(inp.get("text", ""))[:3000]
            e = dict(n=len(self.ledger) + 1, agent=ag.id, label=label, text=text, t=round(time.time() - self.t0, 2))
            self.ledger.append(e)
            self.ev("ledger_post", agent=ag.id, n=e["n"], label=label)
            return f"Posted as L{e['n']}."
        if name == "amend_charter":
            am = str(inp.get("amendment", ""))[:1500]
            reason = str(inp.get("reason", ""))[:800]
            self.amendments.append(dict(agent=ag.id, text=am, reason=reason, t=round(time.time() - self.t0, 2)))
            n = len(self.amendments)
            self.ledger.append(dict(n=len(self.ledger) + 1, agent=ag.id, label="AMENDMENT", text=f"A{n}: {am} (reason: {reason})",
                                    t=round(time.time() - self.t0, 2)))
            self.ev("amend", agent=ag.id, n=n)
            return f"Amendment A{n} adopted; it binds agents spawned from now on."
        if name == "spawn":
            try:
                k = int(inp.get("budget", 1))
            except (TypeError, ValueError):
                k = 1
            brief = str(inp.get("brief", ""))[:6000]
            if k < 1:
                return "Spawn refused: budget must be at least 1."
            if k > ag.wallet:
                return f"Spawn refused: you asked for {k} slots but your wallet holds {ag.wallet}."
            if ag.depth + 1 > self.max_depth:
                return f"Spawn refused: maximum delegation depth {self.max_depth} reached."
            ag.wallet -= k
            child = self.new_agent(ag.id, ag.depth + 1, k - 1, brief)
            self.ev("spawn", agent=child.id, parent=ag.id, budget=k)
            intro = (f"You are agent #{child.id}, spawned by agent #{ag.id} at depth {child.depth}. "
                     f"Your wallet holds {child.wallet} agent-slots for children of your own.\n\nYOUR BRIEF FROM AGENT #{ag.id}:\n{brief}")
            await self.run_agent(child, intro)
            refund = child.wallet
            ag.wallet += refund
            rep = child.report or "(no report)"
            if len(rep) > 9000:
                rep = rep[:4500] + "\n...[report truncated]...\n" + rep[-4000:]
            return (f"REPORT FROM AGENT #{child.id} (status {child.status}, {child.turns} turns, "
                    f"{refund} unspent slots returned; your wallet now {ag.wallet}):\n{rep}")
        return f"Unknown tool {name}."


# ----------------------------------------------------------------------------- mock (dry run)
async def mock_response(kw, tag):
    await asyncio.sleep(random.uniform(0.01, 0.05))
    msgs = kw["messages"]
    first = msgs[0]["content"] if isinstance(msgs[0]["content"], str) else msgs[0]["content"][0]["text"]
    turn = sum(1 for m in msgs if m["role"] == "assistant")
    usage = dict(input_tokens=1500 + 800 * turn, output_tokens=900, cache_creation_input_tokens=0, cache_read_input_tokens=0)

    def tu(name, inp):
        return {"type": "tool_use", "id": f"toolu_{random.randrange(1 << 40):x}", "name": name, "input": inp}

    forced = kw.get("tool_choice", {}).get("type") == "none"
    if "tools" not in kw:
        ans = random.choice([42, 42, 41])
        return dict(content=[{"type": "thinking", "thinking": "mock", "signature": "x"}, {"type": "text", "text": f"work...\nANSWER: {ans}"}],
                    usage=usage, stop_reason="end_turn")
    if forced:
        return dict(content=[{"type": "text", "text": "Forced report. ANSWER: 42"}], usage=usage, stop_reason="end_turn")
    m = re.search(r"wallet holds (\d+)", first)
    wallet = int(m.group(1)) if m else 0
    if "FOUNDER" in first:
        if turn == 0:
            return dict(content=[tu("ledger_read", {}), tu("spawn", {"brief": "attack A", "budget": 20}),
                                 tu("spawn", {"brief": "attack B", "budget": 20}), tu("spawn", {"brief": "too big", "budget": 99})],
                        usage=usage, stop_reason="tool_use")
        if turn == 1:
            return dict(content=[tu("amend_charter", {"amendment": "Checks must use a different method.", "reason": "test"}),
                                 tu("spawn", {"brief": "verify", "budget": 3})], usage=usage, stop_reason="tool_use")
        return dict(content=[{"type": "text", "text": "Final.\nANSWER: 42"}], usage=usage, stop_reason="end_turn")
    if turn == 0:
        calls = [tu("ledger_post", {"label": "DERIVED", "text": f"agent {tag['agent']} finding"})]
        if wallet >= 2:
            calls += [tu("spawn", {"brief": "sub", "budget": max(1, wallet // 3)}), tu("spawn", {"brief": "sub2", "budget": max(1, wallet // 3)})]
        return dict(content=calls, usage=usage, stop_reason="tool_use")
    return dict(content=[{"type": "text", "text": f"Report from {tag['agent']}: value 42 (DERIVED)"}], usage=usage, stop_reason="end_turn")
