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


def _k(n: float) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else f"{n:.0f}"


def turn_line(led: Ledger, warn_tokens: int) -> str:
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
    bits.append(f"session {_k(led.effective_tokens)}")
    return "  [" + " · ".join(bits) + "]"


def report(led: Ledger, warn_tokens: int) -> str:
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
    out = ["token usage (measured from API responses, not estimated):\n"]
    for label, value in rows:
        out.append("\n" if not label else f"  {label:<21}{value}\n")
    if led.cache_hit_rate < 0.5 and led.calls > 3:
        out.append("\n  note: a low cache hit rate on a long session usually means the "
                   "cached\n  prefix keeps being invalidated — check /context for cache "
                   "eligibility.\n")
    return "".join(out)
