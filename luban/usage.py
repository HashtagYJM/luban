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

from dataclasses import dataclass


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
                 n("cache_creation_input_tokens"), n("cache_read_input_tokens"), orig)


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
    last: Usage = None  # type: ignore[assignment]

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

    def add(self, u: Usage) -> None:
        self.input_tokens += u.input_tokens
        self.output_tokens += u.output_tokens
        self.cache_creation_input_tokens += u.cache_creation_input_tokens
        self.cache_read_input_tokens += u.cache_read_input_tokens
        self.cleared_tokens += u.cleared_tokens
        self.calls += 1
        self.last = u

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
# Per MILLION tokens, Anthropic list prices. There is no API that returns these, so a
# hardcoded table is the only option — and a hardcoded table goes stale. Two honest
# caveats travel with every figure this produces:
#
#   1. These are LIST prices. A request routed through a company wrapper may be billed
#      differently, or charged back internally on another basis entirely.
#   2. Prices change. Nothing here can detect that; the table is only as fresh as the
#      release it shipped in.
#
# So the output says "estimated", names the model it priced, and says nothing at all for
# a model it does not know — a wrong number is worse than no number for something a
# person budgets against.
#
# Cache multipliers are ratios of the input rate, not separate prices: a 5-minute cache
# write costs 1.25x input, a read 0.1x. That is the whole reason caching is the biggest
# lever available — a read is a tenth of the price of the same tokens sent fresh.
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10

PRICES = {           # model prefix -> (input $/MTok, output $/MTok)
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}


def _rates(model: str):
    """Longest-prefix match, so a suffixed or aliased id still prices. None = unknown."""
    best = None
    for prefix, rates in PRICES.items():
        if model.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, rates)
    return best[1] if best else None


def cost(led: "Ledger", model: str) -> float | None:
    """Estimated spend in dollars. None when the model is not in the table."""
    rates = _rates(model)
    if rates is None:
        return None
    inp, out = rates
    return (led.input_tokens * inp
            + led.cache_creation_input_tokens * inp * CACHE_WRITE_MULT
            + led.cache_read_input_tokens * inp * CACHE_READ_MULT
            + led.output_tokens * out) / 1_000_000


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
        ("", ""),
        ("session total", f"{led.total_tokens:,} tokens"),
        ("cache-weighted", f"{led.effective_tokens:,.0f} tokens  <- what you are spending"),
        ("cache hit rate", f"{led.cache_hit_rate:.0%}"),
    ]
    if led.cleared_tokens:
        rows.append(("cleared by luban", f"{led.cleared_tokens:,} tokens of stale tool "
                                         f"output never re-sent"))
    spend = cost(led, model) if model else None
    if spend is not None:
        inp, outp = _rates(model)
        rows += [
            ("", ""),
            ("estimated spend", f"${spend:,.2f}   at list prices for {model}"),
            ("  fresh input", f"${led.input_tokens*inp/1e6:,.2f}"),
            ("  cache write", f"${led.cache_creation_input_tokens*inp*CACHE_WRITE_MULT/1e6:,.2f}"),
            ("  cache read", f"${led.cache_read_input_tokens*inp*CACHE_READ_MULT/1e6:,.2f}"),
            ("  output", f"${led.output_tokens*outp/1e6:,.2f}"),
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
        out.append(f"\n  no price on file for {model} — tokens only.\n")
    elif spend is not None:
        out.append("\n  spend is ESTIMATED from Anthropic list prices bundled with this\n"
                   "  release. Your actual bill may differ if requests are routed or\n"
                   "  charged back on another basis.\n")
    return "".join(out)
