"""Refresh luban/prices.json from LiteLLM's model pricing database.

WHY THIS EXISTS
---------------
luban's cost estimate used a hand-written table of rates plus two global cache
multipliers (write 1.25x, read 0.1x). Those numbers were correct — they match LiteLLM's
to the cent — but maintaining them means a person re-reading a pricing page and editing
constants, which is exactly the kind of thing that rots silently.

LiteLLM publishes `model_prices_and_context_window.json`: MIT-licensed, community
maintained, ~1000 models across every provider, with per-model cache rates rather than
global multipliers. That last part is what makes multi-provider possible at all — OpenAI
has no separate cache-write charge and a different read discount, so a global multiplier
is an Anthropic-shaped assumption.

We vendor a SUBSET rather than taking the dependency: `pip install litellm` breaks luban's
zero-dependency invariant, and would not install on the target machine anyway. The data is
just data.

Run this on a networked machine; commit the result. Nothing at runtime fetches anything.

    python scripts/refresh_prices.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

UPSTREAM = ("https://raw.githubusercontent.com/BerriAI/litellm/main/"
            "model_prices_and_context_window.json")
OUT = Path(__file__).resolve().parent.parent / "luban" / "prices.json"

# Keep the vendored file small and legible. A provider prefix here is a statement that
# luban might plausibly be pointed at it — add one when that becomes true, not before.
KEEP_PREFIXES = ("claude-", "gpt-", "o1", "o3", "o4", "gemini-", "deepseek-")

# Only these keys are carried. The rest of an upstream entry (feature flags, provider
# routing, modalities) is not luban's concern and would go stale in our copy.
FIELDS = ("input_cost_per_token", "output_cost_per_token",
          "cache_read_input_token_cost", "cache_creation_input_token_cost",
          "cache_creation_input_token_cost_above_1hr", "max_input_tokens")


def main() -> int:
    print(f"fetching {UPSTREAM}")
    with urllib.request.urlopen(UPSTREAM, timeout=60) as r:
        raw = json.loads(r.read().decode("utf-8"))

    out: dict[str, dict] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict) or not name.startswith(KEEP_PREFIXES):
            continue
        if entry.get("mode") not in (None, "chat"):
            continue                      # skip image/embedding/audio models
        kept = {k: entry[k] for k in FIELDS if entry.get(k) is not None}
        if "input_cost_per_token" not in kept:
            continue                      # a row with no input price prices nothing
        out[name] = kept

    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT.relative_to(OUT.parent.parent.parent)}: "
          f"{len(out)} models, {kb:.0f} KB")
    print("source: BerriAI/litellm, MIT licence. Commit this file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
