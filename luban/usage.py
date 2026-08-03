"""Token accounting from what the API actually reports.

Every API response carries exact token counts — input, output, and the two cache
figures. luban discarded all of them and estimated instead, at 4 chars/token against a
measured 2.94. That is a 36% undercount on the one number the user acts on, and it made
the /compact nudge fire roughly 54,000 tokens LATE: the threshold was compared against an
estimate, so a session reported as 67,861 tokens was really about 92,231.

So: never estimate what the server already told you. These numbers cost nothing extra —
they arrive with the response either way.

Standard library only. Nothing here may raise into the agent loop: a broken counter must
never break a turn.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


# Cached input is billed at roughly a tenth of fresh input. "Effective" tokens weight it
# so the running figure tracks what is actually being consumed rather than a raw sum that
# makes a well-cached session look far more expensive than it is.
CACHE_READ_WEIGHT = 0.1


@dataclass
class Usage:
    """One response's counts. Field names mirror the API's."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # What the prompt would have been WITHOUT server-side tool-result clearing. The API
    # reports this alongside usage when context editing fires, so the saving is measured
    # rather than claimed.
    original_input_tokens: int = 0
    # Reasoning models report the thinking portion of output separately. It is ALREADY
    # inside output_tokens — reported, never added — and at high effort it is the
    # dominant cost, so output looks inexplicably large without a line that says why.
    reasoning_tokens: int = 0

    @property
    def cleared_tokens(self) -> int:
        return max(0, self.original_input_tokens - self.context_tokens)

    @property
    def context_tokens(self) -> int:
        """What the model actually READ this call.

        The API reports `input_tokens` EXCLUDING anything served from cache, so summing
        the three is the only way to get real context size. Using input_tokens alone
        understates a well-cached turn by most of the prompt.
        """
        return (self.input_tokens + self.cache_creation_input_tokens
                + self.cache_read_input_tokens)

    @property
    def effective_input(self) -> float:
        return (self.input_tokens + self.cache_creation_input_tokens
                + self.cache_read_input_tokens * CACHE_READ_WEIGHT)


def from_response(msg) -> Usage:
    """Read usage off any response object. Never raises — returns zeros if absent."""
    u = getattr(msg, "usage", None)
    if u is None:
        return Usage()
    def n(name: str) -> int:
        try:
            return int(getattr(u, name, 0) or 0)
        except (TypeError, ValueError):
            return 0
    orig = 0
    cm = getattr(msg, "context_management", None)
    if cm is not None:
        try:
            orig = int(getattr(cm, "original_input_tokens", 0) or 0)
        except (TypeError, ValueError):
            orig = 0
    return Usage(n("input_tokens"), n("output_tokens"),
                 n("cache_creation_input_tokens"), n("cache_read_input_tokens"), orig,
                 n("reasoning_tokens"))


@dataclass
class Ledger:
    """Session-cumulative totals, plus the most recent call.

    `last` is what answers "how big is my context right now" — it is a measurement of the
    request just sent, not a guess about the one coming. `calls` counts model calls, not
    user turns: one turn with four tool round-trips is four calls, which is exactly why
    an agentic session burns tokens faster than a chat.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    calls: int = 0
    cleared_tokens: int = 0
    reasoning_tokens: int = 0
    last: Usage = None  # type: ignore[assignment]
    # Sub-ledgers keyed by the model that bought the tokens. A mid-session /model switch
    # means the cumulative total was bought at two different rates, and pricing all of it
    # at whichever model happens to be current blends dollars that were never the same
    # dollars. Sub-ledgers carry no by_model of their own.
    by_model: dict = field(default_factory=dict)

    @property
    def blind(self) -> bool:
        """True when calls were made but every usage field came back zero.

        That is what a non-Anthropic response shape looks like from here: OpenAI reports
        `prompt_tokens` / `completion_tokens` / `prompt_tokens_details.cached_tokens`, so
        every field this reads is absent and `from_response` returns zeros. Nothing
        crashes — luban simply reports nothing, and the /compact nudge falls back to the
        4-chars/token estimator that measured 36% wrong. A provider switch would silently
        reinstate the exact defect this module exists to remove, so it has to announce
        itself.
        """
        return self.calls > 0 and self.total_tokens == 0

    def add(self, u: Usage, model: str = "", *, context: bool = True) -> None:
        """Record one model call. `context=False` for a SIDE CALL — fold, compact, memory
        flush, reflect. They cost real money and must be counted, but they send their own
        payload rather than the conversation, so letting one set `last` would report the
        session's context size as whatever that side call happened to send."""
        self.input_tokens += u.input_tokens
        self.output_tokens += u.output_tokens
        self.cache_creation_input_tokens += u.cache_creation_input_tokens
        self.cache_read_input_tokens += u.cache_read_input_tokens
        self.cleared_tokens += u.cleared_tokens
        self.reasoning_tokens += u.reasoning_tokens
        self.calls += 1
        if context:
            self.last = u
        if model:
            sub = self.by_model.get(model)
            if sub is None:
                sub = self.by_model[model] = Ledger()
            sub.add(u, context=context)  # no model => no recursion

    @property
    def context_tokens(self) -> int:
        """Current context size — from the LAST call, not the sum.

        Summing across calls answers "what did I spend"; the last call answers "how full
        is the window". Conflating them is how a spend figure gets compared against a
        window threshold.
        """
        return self.last.context_tokens if self.last else 0

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_creation_input_tokens + self.cache_read_input_tokens)

    @property
    def effective_tokens(self) -> float:
        """Cache-weighted spend — the figure that tracks consumption."""
        return (self.input_tokens + self.output_tokens
                + self.cache_creation_input_tokens
                + self.cache_read_input_tokens * CACHE_READ_WEIGHT)

    @property
    def cache_hit_rate(self) -> float:
        """Share of input served from cache. Low here on a long session means the cached
        prefix is being invalidated — the most common cause of a bill growing faster than
        the conversation."""
        read = self.cache_read_input_tokens
        total_in = read + self.input_tokens + self.cache_creation_input_tokens
        return read / total_in if total_in else 0.0


# ---------------------------------------------------------------------- pricing ----
# Rates come from luban/prices.json, a vendored subset of LiteLLM's
# model_prices_and_context_window.json (MIT). Refresh with scripts/refresh_prices.py.
#
# WHY VENDORED DATA RATHER THAN A HAND-WRITTEN TABLE
# The hand-written table was not wrong — its numbers matched LiteLLM's to the cent. It was
# unmaintainable: keeping it current meant a person re-reading a pricing page and editing
# constants, which rots silently and gives no signal when it has.
#
# WHY PER-MODEL CACHE RATES RATHER THAN GLOBAL MULTIPLIERS
# The old code assumed a cache write costs 1.25x input and a read 0.1x. True for Anthropic;
# not true generally. In this very file, `gpt-5` carries a cache READ cost and no cache
# WRITE cost at all, because OpenAI does not charge for writes. A global multiplier cannot
# express that, so it silently invents a charge that does not exist. Per-model rates are
# what make a provider switch a data change rather than a code change.
#
# WHY NOT `pip install litellm`
# Zero dependencies is an invariant (see ARCHITECTURE.md), and the target machine blocks
# arbitrary installs. The data is just data; the library is not needed to read it.
#
# WHAT THIS STILL CANNOT DO
# It is a snapshot. Prices change and nothing here detects that, so every figure is
# labelled an estimate. What changed is that refreshing is now a command, not an act of
# authorship.
_PRICES: dict | None = None


def _prices() -> dict:
    global _PRICES
    if _PRICES is None:
        try:
            path = Path(__file__).with_name("prices.json")
            _PRICES = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            _PRICES = {}      # missing or corrupt table: show tokens, never a wrong price
    return _PRICES


def rates(model: str) -> dict | None:
    """Per-token rates for a model, by longest-prefix match. None when unknown.

    Longest-prefix so a dated or suffixed id (`claude-haiku-4-5-20251001`) still prices
    off its base entry.
    """
    best_key = ""
    for key in _prices():
        if model.startswith(key) and len(key) > len(best_key):
            best_key = key
    return _prices()[best_key] if best_key else None


def _cost_one(led: "Ledger", model: str) -> float | None:
    r = rates(model)
    if r is None:
        return None
    inp = r.get("input_cost_per_token", 0.0)
    # A missing cache rate means the provider does not charge separately for it — bill
    # those tokens at the plain input rate rather than inventing a premium.
    return (led.input_tokens * inp
            + led.cache_creation_input_tokens * r.get("cache_creation_input_token_cost", inp)
            + led.cache_read_input_tokens * r.get("cache_read_input_token_cost", inp)
            + led.output_tokens * r.get("output_cost_per_token", 0.0))


def unpriced(led: "Ledger", model: str = "") -> list[str]:
    """Models in this ledger with no rates on file."""
    names = list(led.by_model) or ([model] if model else [])
    return [m for m in names if rates(m) is None]


def cost(led: "Ledger", model: str = "") -> float | None:
    """Estimated spend in dollars, attributed per model. None if anything is unpriced.

    One unpriced model means the total would silently omit part of the bill, and a
    figure that is quietly too low is worse than no figure for something a person is
    trying to stay under.
    """
    if led.by_model:
        total = 0.0
        for m, sub in led.by_model.items():
            one = _cost_one(sub, m)
            if one is None:
                return None
            total += one
        return total
    return _cost_one(led, model)


def _k(n: float) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else f"{n:.0f}"


def turn_line(led: Ledger, warn_tokens: int, model: str = "") -> str:
    """The per-turn status line — small enough to sit under every response.

    Shows context against the threshold because that is the number with a cliff, and
    session spend because that is the number the user is trying to stay under.
    """
    if not led.last:
        return ""
    u = led.last
    pct = led.context_tokens / warn_tokens if warn_tokens else 0
    bits = [f"ctx {_k(led.context_tokens)}/{_k(warn_tokens)} ({pct:.0%})",
            f"+{_k(u.output_tokens)} out"]
    if u.cache_read_input_tokens:
        bits.append(f"{led.cache_hit_rate:.0%} cached")
    if led.cleared_tokens:
        bits.append(f"-{_k(led.cleared_tokens)} cleared")
    spend = cost(led, model) if model else None
    bits.append(f"session ${spend:.2f}" if spend is not None
                else f"session {_k(led.effective_tokens)}")
    return "  [" + " · ".join(bits) + "]"


def report(led: Ledger, warn_tokens: int, model: str = "") -> str:
    """The /usage view: measured, never estimated."""
    if not led.calls:
        return "no model calls yet this session.\n"
    rows = [
        ("model calls", f"{led.calls:,}"),
        ("context now", f"{led.context_tokens:,} tokens of {warn_tokens:,} "
                        f"({led.context_tokens/warn_tokens:.0%})" if warn_tokens
                        else f"{led.context_tokens:,} tokens"),
        ("", ""),
        ("input (fresh)", f"{led.input_tokens:,}"),
        ("input (cache write)", f"{led.cache_creation_input_tokens:,}"),
        ("input (cache read)", f"{led.cache_read_input_tokens:,}  billed ~0.1x"),
        ("output", f"{led.output_tokens:,}"),
    ]
    if led.reasoning_tokens:
        rows.append(("  of which reasoning", f"{led.reasoning_tokens:,}  billed as output"))
    rows += [
        ("", ""),
        ("session total", f"{led.total_tokens:,} tokens"),
        ("cache-weighted", f"{led.effective_tokens:,.0f} tokens  <- what you are spending"),
        ("cache hit rate", f"{led.cache_hit_rate:.0%}"),
    ]
    if led.cleared_tokens:
        rows.append(("cleared by luban", f"{led.cleared_tokens:,} tokens of stale tool "
                                         f"output never re-sent"))
    spend = cost(led, model) if model else None
    if spend is not None and len(led.by_model) > 1:
        # More than one model bought these tokens. Show what each cost rather than one
        # blended figure that belongs to neither.
        rows.append(("", ""))
        rows.append(("estimated spend", f"${spend:,.2f}   at list prices"))
        for m, sub in led.by_model.items():
            rows.append((f"  {m}", f"${_cost_one(sub, m):,.2f}   {sub.calls:,} calls, "
                                   f"{sub.total_tokens:,} tokens"))
    elif spend is not None:
        priced = next(iter(led.by_model), model)
        r = rates(priced)
        inp = r.get("input_cost_per_token", 0.0)
        rows += [
            ("", ""),
            ("estimated spend", f"${spend:,.2f}   at list prices for {priced}"),
            ("  fresh input", f"${led.input_tokens*inp:,.2f}"),
            ("  cache write", f"${led.cache_creation_input_tokens*r.get('cache_creation_input_token_cost', inp):,.2f}"),
            ("  cache read", f"${led.cache_read_input_tokens*r.get('cache_read_input_token_cost', inp):,.2f}"),
            ("  output", f"${led.output_tokens*r.get('output_cost_per_token', 0.0):,.2f}"),
        ]
    out = ["token usage (measured from API responses, not estimated):\n"]
    for label, value in rows:
        out.append("\n" if not label else f"  {label:<21}{value}\n")
    if led.cache_hit_rate < 0.5 and led.calls > 3:
        out.append("\n  note: a low cache hit rate on a long session usually means the "
                   "cached\n  prefix keeps being invalidated — check /context for cache "
                   "eligibility.\n")
    if led.blind:
        return ("token usage: the backend returned no usage data on any of "
                f"{led.calls} call(s).\n\n"
                "  luban reads input_tokens / output_tokens / cache_* off each response —\n"
                "  the Anthropic shape. A backend reporting usage differently (OpenAI uses\n"
                "  prompt_tokens / completion_tokens) yields zeros here, and the /compact\n"
                "  nudge silently falls back to a 4-chars/token estimate that measured 36%\n"
                "  wrong. Treat context size as UNMEASURED until this is fixed.\n")
    if model and spend is None:
        # Say nothing rather than guess. A wrong number is worse than no number for
        # something a person budgets against.
        missing = ", ".join(unpriced(led, model)) or model
        out.append(f"\n  no price on file for {missing} — tokens only.\n")
    elif spend is not None:
        out.append("\n  spend is ESTIMATED from the list prices bundled with this release.\n"
                   "  Your actual bill may differ if requests are routed or charged back\n"
                   "  on another basis.\n")
    return "".join(out)
