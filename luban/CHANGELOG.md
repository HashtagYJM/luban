# luban changelog

Release notes, newest first. Bundled inside the package so luban can show
"what's new" and reconcile its enhancement tracker offline, with no network.
Each entry tags the tracker IDs (E-/F-) it resolves.

## v0.5.25 — folding lands where it aims, and says so honestly

### `/compact` no longer ends a conversation that used web search (E33)

A web search arrives as a pair — the block that asks and the block that answers — and the API rejects a request where the asking block has no answer after it. v0.5.24 stripped any unanswered block before sending, and did it at each of the places that send. Compaction was a fourth place, added earlier and never given the same treatment, so compacting a conversation that had web-searched failed on the spot and kept failing. Folding had the same gap waiting.

The rule now lives inside the two functions that actually talk to the model, so every request — a turn, a fold, a compaction, a subagent, or anything added later — passes through it. The guard was never wrong; the list of places calling it was, and there is no longer a list.

### A journal entry that is too long now says so (E34)

The journal is sent whole on every turn and keeps the newest entries that fit. One very long entry stays within that budget and still pushes out whole earlier days, so the timeline collapses to a few hours of one day. "Keep entries to a few lines" was written in three places and bound nothing, because there was no signal at the moment an entry was actually written.

Entries over the size guide are still written — the content is worth more than the rule — and now come back with their own size, the guide, and where the detail belongs instead. The guide is derived from the journal's share of the budget rather than picked, and the tool now states the number instead of "a few lines".

### A fold now aims at the whole prompt, not just the conversation

Folding triggers on everything sent in a turn — the conversation plus the system prompt, memory blocks, skills catalog and tool guidance that ride along with it. But it sized the part it kept as a share of the window spent entirely on conversation, leaving no room for the rest. So a fold aimed comfortably below the threshold landed above it, the warning returned on the very next turn at a higher number, and folding ran again, and again — each one the most expensive call in a session, because it sends the whole early conversation uncached.

The kept span is now sized against the whole prompt, with the always-on part subtracted first. A fold lands where it aims, and the warning stays gone until the conversation genuinely grows back.

### The context figure survives a fold

The size shown is measured from the last call to the model, and a fold makes no call with the shortened conversation. Nothing updated the figure afterwards, so the "consider /compact" note printed directly beneath a successful fold repeated the number from before it — a fold that had just freed a large part of the window read as one that had done nothing at all.

Folding now records what the next call will send, and the next real measurement replaces it.

### Every way a fold can decline now says so, and stops repeating itself

Three outcomes leave the conversation unchanged: no cut point exists, the older span is too small to be worth the cache it costs, or the bulk of the context is in the recent turns a fold has to keep. One of them printed nothing at all — after announcing "folding now…", the session simply went quiet, which is indistinguishable from a fold that silently failed.

Each now states what it found. And because being over the threshold is a standing condition rather than an event, the warning no longer reprints every turn: it waits for the context to grow materially, and when folding genuinely cannot help any further it says so once, points at `/compact`, and stops offering. A fold blocked only by a result that is still being worked on stays retryable, since that clears on its own as the conversation moves on.

### One oversized tool result no longer defeats folding entirely

Reading a large file or document puts a single very large result into the recent turns that folding exists to preserve. Folding the older conversation then frees almost nothing, correctly by its own rules, and the context stays over the threshold with nothing able to bring it down.

When context is over the threshold, a single result large enough to dominate the window now has its content dropped from what is sent, in place, leaving the request valid with no cut point needed. A marker states how much was dropped and points at the full result, which stays verbatim in the session transcript on disk and can be read back at any time. The result being worked on right now is never touched, and neither is a web search result — that pair cannot be edited without breaking every later request, so web searches remain bounded only by `/compact`.

## v0.5.24 — a web search no longer ends the conversation, and memory survives a failed write

### Clearing a web search result no longer bricks the session (E33)

With `context_editing = true`, a long session that had used web search would start returning an error on every send, and the only way out was to start a new conversation.

A web search arrives as a pair: the block that asks, and the block that answers. The pair cannot be split — the API rejects a request where the asking block has no answer after it. Server-side clearing removes stale tool output to save context, and it was allowed to clear an aged-out search answer while leaving the question standing, which made every subsequent request invalid. Because luban sends the full conversation each turn, the clear was re-applied to a fresh copy every time, so the failure repeated instead of passing.

Three changes. Web search is now excluded from clearing, alongside the memory tools. Any question left without its answer is stripped from the conversation before it is sent, wherever it sits — not only at the end. And if this rejection is seen anyway, luban turns context editing off for the rest of the session and continues on the ordinary path, so the conversation survives at the cost of higher context use rather than ending.

The exclusion matches the tool by name, so it holds for both the basic and the newer web search tool versions.

### Memory survives a failed write

Writing a file with Python's `write_text` empties it before the replacement is written, so a write interrupted partway — a full disk, a quota, a process killed at the wrong moment — destroyed content that was never in question. The file tools and session files already avoided this; the memory store did not.

Two further choices made a narrow window into a lasting one. `MEMORY.md` is rebuilt from the fact files, but it was only regenerated when a fact was written, and startup recreated it only when it was missing entirely — so a truncated index, which is not missing, survived every restart. And the error handling assumed a failed write left the previous content alone, so nothing reported the loss. The result was a store that quietly listed fewer facts than it held, with every fact intact on disk.

Every whole-file write in the memory store now replaces the file rather than emptying it, and the index is rebuilt at startup instead of only when absent. The journal is deliberately unchanged: it appends, and appending never truncates. There is one shared implementation of this write for the whole codebase.

## v0.5.23 — folding keeps the work you are in the middle of

### Fold at the boundary before the target, not after it

A fold may only cut where the kept conversation starts on one of your own messages, because a span that opens on an orphaned tool result is rejected by the API. Every intermediate message in an agentic turn is a tool result, so cut points exist only where you typed, and nothing limits how far apart those are.

Folding searched forward from its size target for the next cut point, which failed two ways on the same conversation. A turn larger than the window folding wants to keep has no cut point after the target at all, so folding reported that nothing could be folded without splitting a tool call and left the conversation whole. Then a short turn after a long one put the only cut point after the target at the very end, so the fold succeeded, summarised away the run still being worked on, and reported it as a success.

Folding now searches backward instead, which can only ever keep more of the conversation than the target rather than less. Folding still declines when no earlier cut point exists — a single unbroken run from the first message — and now says so accurately.

### Measure context density against the uncleared prompt

With server-side clearing of stale tool results enabled, the model reads less than the local conversation holds. Characters per token were measured against the cleared count, which inflated the ratio and every folding threshold derived from it, so folds became rarer and smaller exactly when tool output was heaviest. The measurement now uses the prompt size before clearing, which the API already reports.

### Fold before context fills

Folding now runs automatically when context crosses 70% of the configured threshold. It still announces the fold before and after, keeps recent turns verbatim, and preserves the full transcript on disk. Set `auto_fold = false` to restore the prompt.

A failed fold is attempted only once per session. This prevents an unavailable backend from charging another failed model call after every turn. A fold declined because the removable span is still too small remains eligible later as the conversation grows.

### Keep the conversation cache reusable

The fact index and journal now ride behind the conversation cache breakpoint. A memory write therefore changes only the volatile tail instead of invalidating the cached conversation before it. Cache entries request a one-hour lifetime and fall back to the existing five-minute form if a backend rejects the `ttl` field; the fallback keeps caching active.

`/usage` now includes folding, compaction, memory flushes, and reflection. These side calls send their own payloads, so they count toward spend without replacing the displayed size of the live conversation.

### Switch providers with `/model` (E32)

`/model` can now switch between the existing Anthropic-compatible client and an OpenAI Responses client in the same session. Add an optional `build_openai_client()` to `~/.luban/client_local.py`; `gpt-*`, `o1`, `o3`, and `o4` model IDs route there, while every other model continues through the original client unchanged.

The adapter lives in luban's tested core and adds no dependency on the OpenAI SDK. It translates tools, tool results, reasoning effort, encrypted reasoning state, stop reasons, and usage into luban's existing surface. Cached input is subtracted before mapping usage because OpenAI includes cached tokens in its input total and Anthropic does not.

### The publication guard covers the publication

The leak guard now scans every tracked file, including source comments and test prose, for field measurements as well as internal identifiers. Public explanations retain the mechanism and omit data from real installations.

## v0.5.21 — see what you spend, and stop paying for it twice

### What it costs, in dollars

`/usage` now estimates spend, not just tokens — the number you actually budget against.
It breaks out fresh input, cache write, cache read and output separately, because they are
priced very differently: a cache read costs a **tenth** of the same tokens sent fresh, while
output costs **five times** input.

That last point matters for expectations. Caching cuts *input* cost, and output is untouched,
so a session whose token repeats drop by 68% sees a bill drop closer to 50%.

Rates come from a vendored subset of LiteLLM's model pricing database (MIT), refreshed with
`scripts/refresh_prices.py` — 137 models across Anthropic, OpenAI, Google and DeepSeek. It
ships inside the wheel, so it works offline like everything else.

Using published per-model rates rather than hand-written ones matters beyond upkeep: cache
economics differ by provider. Anthropic charges 1.25x input for a cache write and 0.1x for a
read; OpenAI charges for reads and **not at all** for writes. A single global multiplier
would invent a charge that doesn't exist.

Two caveats travel with every figure, and luban prints them: these are **list** prices from a
snapshot, so your actual bill may differ if requests are routed or charged back on another
basis; and for a model it has no price for, it says so and shows tokens only rather than
guessing.

### The conversation is now cached

luban marked exactly one spot for caching — the end of the stable system block — so
everything after it was billed at full price on every call, including the entire
conversation. Your conversation is byte-identical from one call to the next, and you were
paying for it every time.

The effect compounds: the cached amount stays **flat** while the uncached amount **grows
with the conversation**, so the cache hit rate falls the longer you work. It gets worse,
never better — which is what "my token use keeps climbing" feels like from the inside.

There is now a **second cache breakpoint** on the end of the conversation, so each call
writes a cache entry covering everything so far and the next call reads it, paying only for
what is new. Nothing about what the model sees changes; only what it has to re-read.

Note this interacts with `context_editing`: cleared tool results sit inside the newly cached
prefix, so each clear invalidates it. That is a further reason context editing stays off
unless you have measured a reason to want it.

## v0.5.20 — the conversation gets a lifecycle

### Why your token use grew

The Messages API is **stateless**: every call re-sends the entire conversation — system
prompt, tool schemas, every prior message, every tool result. In a working session the
conversation quickly becomes the overwhelming majority of that, and an agentic turn makes
many calls, so one turn with several tool round-trips re-sends all of it many times over.

And luban had **no lifecycle for that history**. It grew until you typed `/compact`, which
reset the whole session. Every context feature up to now — budgets, caps, memory curation —
governed the small remainder.

### Folding

When context crosses 70% of your threshold, luban now offers to **fold**: the oldest span is
summarized, recent turns stay verbatim, and the session keeps its identity and thread. It
alerts and asks — it never folds silently.

This is the contract luban already uses for the journal, applied one level up: **bounded
window, full record on disk, omission stated.** The full transcript is written to disk
*before* anything folds, and the marker left in its place names how many messages were folded
and the session file they still live in, readable with the `sessions` and `read_file` tools.
Folding changes only what is *sent*, never what is stored.

It differs from `/compact`, which stays for a deliberate fresh start: folding is partial and
repeatable, compaction is all-or-nothing.


### Token consumption you can actually see (and a nudge that fired far too late)

luban was **throwing away the exact token counts the API returns on every response** and
estimating instead, at 4 chars/token against a measured 2.94. That 36% undercount governed
the `/compact` nudge, so the nudge fired when the *estimate* reached your threshold — about
substantially later than your configured threshold. The estimate also counted only message text, ignoring the system prompt and
tool schemas entirely.

Now every number is measured:

- **A live line under each response:** context against your threshold, output this turn,
  cache hit rate, and cumulative session spend.
- **`/usage`** — fresh vs cached input, output, session total, and a **cache-weighted**
  figure that reflects what you are actually consuming (cached input bills at roughly a
  tenth, so a raw sum badly overstates a well-cached session).
- The `/compact` nudge now uses real context size, including the system prompt and tools.

If your bill grew faster than your conversation, the cache hit rate in `/usage` is the first
thing to look at — a low rate on a long session means the cached prefix keeps being
invalidated.

### Stale tool output is no longer re-sent forever

Tool results could be 20,000 characters each and accumulated for the life of a session,
re-sent in full on every subsequent turn. luban now uses the Claude API's server-side
**context editing** to clear old tool results once a session grows, keeping the recent ones
and never touching memory-tool results. `/usage` reports exactly how many tokens this saved.

This is **not** automatic compaction: your conversation is never summarised or dropped, only
stale tool *output*, and the full transcript stays on disk. On a backend without the feature,
luban detects that once and carries on unchanged.

**It ships OFF.** Set `context_editing = true` in `config.toml` to enable it. This is the one
change that alters the shape of the API request, and it could not be exercised against your
proxy from the development machine — so measure a normal session with `/usage` first, turn it
on, and measure again. Everything else in this release only *reads* what the API already
returns and cannot cost you anything.

### Skill descriptions are no longer truncated

Two hidden caps cut them — 240 characters for frontmatter, 80 for a plain `.md`. The
description **is** the trigger text the model matches your task against, so cutting it
defeats the feature it belongs to: in field use, descriptions several
times that length were reduced to the cap, discarding most of the trigger text on exactly
the richest skills. v0.5.19 made one of them warn rather than removing it, which produced a
warning *per skill per command* — a screenful of noise on any real catalog. Both caps and
the warning are gone; the skills catalog is governed by the one shared always-on budget
instead.

## v0.5.19 — the journal stops eating your context

Three fixes to the always-on context block, two of them regressions in v0.5.18.
Tracker items: **E31**, the **E30** description residual, plus **E27/E28** confirmed fixed in
v0.5.18 and **E29** now obsolete.

### The journal was 71% of everything sent every turn (E31)

In field use the journal grew to dominate the always-on block — the largest contributor by
a wide margin, and the only component with no size bound at all. A single busy day's file
could exceed the entire budget on its own.

This was a regression in the previous release. Replacing five per-file caps with one shared
budget was right for four of them and wrong for the journal, and the docstring was left still
promising a truncation the code had stopped doing.

The journal is genuinely different from the other always-on files, which is why it is the one
that gets a bound:

- It **grows by design** — luban is instructed to write an entry at the close of every
  working block, in every project. Following the instruction is what caused the breach.
- It has **no curation lever**. `/reflect` merges and deletes *facts*; there is no equivalent
  for a timeline, and there shouldn't be — a journal is append-only by definition.
- Trimming it is **lossless**. Every day file stays on disk and every transcript is kept, so
  showing fewer days is choosing a window, not deleting anything.

So the journal now fills newest-first within 30% of the shared budget, cutting on day
boundaries — and when a single day is itself too large, on entry boundaries, never mid-line.
**What was left out is always stated**, with a pointer to the day files, because a bound
nobody is told about is indistinguishable from a bug. Your `SOUL.md` and `USER.md` are still
never cut.

There is also a guard against the whole class of mistake, not just this instance: a test now
requires **every** always-on component to be either bounded to a declared allowance or to have
a remedy that can actually shrink it. Unbounded *and* nothing-can-fix-it fails the build —
which is precisely the state the journal was in.

### Two smaller fixes

- **The over-budget warning printed twice at startup**, once from each of two code paths
  stating the same total in different words. Now once, from the one that also names the
  biggest contributor and its correct remedy.
- **Long skill descriptions cut mid-word, silently** (the tail of E30). They now cut on a
  word boundary and warn you, so you can put the trigger words first.

### A shared budget needs a remedy for every contributor

v0.5.18 replaced five per-file
caps with one shared always-on budget, but only the *accounting* was made shared — the
*remedy* stayed where it was. `offer_tidy` considered only `SOUL.md` and `USER.md`, then
picked the larger of those two. With a bloated fact index it therefore offered to compact
an innocent 2,000-char `USER.md` while never naming the 30,000-char culprit, and the
project memory file (`LUBAN.md`/`CLAUDE.md`/`AGENTS.md`) had no remedy at all.

Now every contributor declares one, because they are not the same kind of thing:

| contributor | remedy |
| --- | --- |
| `SOUL.md`, `USER.md` | compact — an LLM rewrite you confirm as a diff |
| your project's memory file | **reported, never rewritten** — see below |
| fact index | `/reflect` — it is machine-generated from the fact files, so it shrinks by curating them |
| journal | none needed — it is bounded to a share of the budget (see above) and states what it omitted |

`SOUL.md` and `USER.md` are your own prose in your own home directory, so luban offers to
tidy them. Your **project's** memory file is different in kind: it lives in a repo, it may
be under version control and shared with colleagues, and an agent proposing to rewrite a
shared file is not the same act as tidying your personal profile — a confirmation prompt
doesn't make it the same. luban names it as your biggest contributor and leaves it to you.

The over-budget notice now lists **every** contributor with its size, names whichever is
actually biggest, and offers the remedy that fits it — skipping self-limiting contributors
so the journal at the top cannot stall the offer. The `/reflect` ledger also counts the
project memory file, which it previously omitted from the very total it was policing.

## v0.5.18 — one budget instead of five caps, and skills that actually trigger

The headline is a simplification: **luban's config gets smaller, not bigger.**

### Your always-on files are no longer capped individually

luban used to hold five separate size limits — one each for `SOUL.md`, `USER.md`, the fact
index, the journal, and the project memory file. Each cut its own file **silently** when
it was exceeded. In field use a profile well over the cap silently lost its tail — including
rules that had been deliberately promoted into always-on context on luban's own advice.

There is now **one budget for the whole always-on block**, and **nothing is ever silently
cut**. Files are sent whole. If the total runs over, luban tells you and the model, and
offers to compact — going over is a prompt to consolidate, not a quiet quota. A single
file is only ever trimmed if it exceeds the entire budget on its own, which means
something went wrong rather than that your profile grew, and it says so loudly.

Practically: a big `USER.md` now reaches the model intact.

### Skills whose descriptions were invisible

A skill using YAML's multi-line description form —

```yaml
description: >
  Methodology for quant research: plan-then-code,
  TDD, verify before scaffold.
```

— was listed in the catalog with its description showing as a bare `>`. luban read only
the first line, so the folded text was dropped, which meant the model had **no trigger
text at all** for exactly the skills whose descriptions were long enough to need folding
— the richest ones. It could only guess from the folder name, so they usually went
unloaded. Both block forms are now read properly, and frontmatter with no usable
description warns instead of failing silently.

### Memory the model can trust mid-turn

The fact index in the prompt was captured once per message and reused for the rest of that
turn. So if luban saved a fact and then looked, it could be told the fact it had just
written didn't exist — which is precisely what makes it save a duplicate. The index is now
re-rendered on every model call.

### Search stops being hijacked by big documents

Two fixes. Relevance no longer rises with document length, so the largest generic file
stopped outranking the small fact you actually asked for. And the enhancement tracker —
a large hand-maintained document that happens to live in the memory folder — no longer
competes in keyword search at all. It quotes past search queries verbatim, so it won any
search about a problem it had recorded. It is still reachable by name and by its path;
it just isn't a search result any more.

### Config

Nothing new to learn — this release **removes** settings rather than adding them. The
per-file size limits are gone entirely, along with the knobs for them.

## v0.5.17 — graduation is a trade, not a dumping ground

A follow-up to v0.5.16, fixing a flaw in it.

v0.5.16 taught `/reflect` to **graduate** recurring preferences out of the fact store and
into `USER.md`. That was right in principle, but it created an inbound pipeline into a
capped file without telling the curator a cap existed — so the fix for clutter in one place
quietly opened a path to clutter in another. Worse: the fact store degrades *gracefully*
when it overflows (it drops descriptions but keeps every fact), while `USER.md` does not —
past its limit the **end of the file is simply cut**. Graduation was moving knowledge from
the forgiving store into the unforgiving one.

Now:

- **`/reflect` sees the always-on budget.** Before it proposes promoting anything, it is
  shown how full `USER.md` and `SOUL.md` are, how much room is left, and that they do not
  degrade gracefully.
- **Graduation is a trade, not an append.** To promote a line, the same edit must tighten or
  drop weaker ones so the file stays under budget. The bar is explicit: would you want this
  in front of the model on *every* turn for the next year? Promoting nothing is a valid
  outcome.
- **New TIGHTEN step.** If `USER.md` is over budget, `/reflect` edits it down whether or not
  anything was promoted — because an over-budget `USER.md` is silently losing its tail right
  now.

The principle, stated once: **every store that can be written to needs a retire path.** The
fact store got one in v0.5.16; this gives one to the file it promotes into.

## v0.5.16 — memory that stays small and true

v0.5.15 made `recall`'s keyword matching smarter. That was the wrong fix: it tuned a
mechanism that shouldn't have been carrying the work. This release removes it from the
critical path and puts the effort into **curation** instead.

The reasoning is worth stating, because it changes how you should think about luban's
memory. Your fact store is small — a few dozen atomic notes — and luban already receives
an **index of every fact on every turn**. So looking something up was never a search
problem. It's a *fetch*: pick the entry from the index, read that file. Anthropic's own
agent memory works exactly this way and has no search layer at all.

### Reading memory

- **Fetch by name is now the primary path.** Passing a fact's slug returns that one fact
  directly — no ranking, no competing hits. The tool now tells the model to do this, which
  it previously did not: the parameter had no description at all, so the model guessed with
  prose and landed on the weak path while the exact name sat in its context.
- **A miss no longer implies absence.** "(no matches)" read to the model as *"that fact
  doesn't exist"* — so it would helpfully save a new one, and your store grew a duplicate.
  That was the real engine of memory clutter. The message now points at the index and says
  explicitly not to save a fact just because a search missed.
- Keyword matching still exists for exploring when you don't know the name. It's just no
  longer what the system depends on.

### Curating memory

- **`/reflect` now sees your entire fact store.** It previously inspected memory through
  the same capped search everything else used — about a tenth of what it was asked to
  curate. Rationing the curator is why consolidation never really happened. It now receives
  the whole store, plus a list of facts that look like duplicates of each other.
- **`/reflect` is a procedure, not a wish**: survey → merge duplicates → resolve
  contradictions → delete what's already recorded elsewhere → **graduate** → report.
- **Graduation is the part that was missing.** A preference that keeps coming up about how
  you want work done isn't a look-up detail — it's a standing instruction, and it belongs
  in `USER.md`, which is always in context. A stored fact can't shape behaviour you need
  *before* you'd think to look it up. `/reflect` now proposes that promotion.
- Deleting is safe, and the prompt says so: **every session transcript and journal day is
  kept permanently on disk.** Nothing is lost by pruning a fact, so the bar for keeping one
  should be high.

### When memory outgrows its budget

luban keeps a size budget on the always-on index. That isn't about saving money — the
whole store is a rounding error against the context window. It's that a bloated always-on
block **measurably reduces how well the model follows what's in it**.

Previously, going over budget silently trimmed descriptions to fit — the problem stayed
hidden forever. Now luban also *tells the model*, so it can suggest `/reflect` and explain
why. The budget becomes a prompt to consolidate rather than a quiet quota.

### Also

- Fixed the pre-push leak guard flagging ordinary English: a three-letter internal acronym
  was matching as a substring inside words like *evaluate* and *graduate*. Short acronyms
  now match whole words only; longer identifiers are unchanged.

### Deliberately not built

No fuzzy-matching library, no embeddings or vector database (they'd break the
zero-dependency offline install), no extra model call on every lookup, and no dumping the
whole store into context. Each was considered and rejected for a stated reason — the spec
records them.

## v0.5.15 — recall that finds things, context you can see, and turns that stop paying twice

### `recall` was silently missing facts it had on disk (E26)

The biggest fix here, and the root cause of a problem that looked like something else.

`recall` required **every** word of your query to appear in a fact — one ordinary absent
word ("how", "their", "against") zeroed an otherwise perfect match, and you got
"(no matches)" for a fact sitting right there. Field-reproduced on a real 24-fact store:
**6 of 8 natural-language questions failed.**

That doesn't just make search annoying — it *rots your memory*. The model asks, hears "no
such fact", reasonably concludes it doesn't exist, and saves a near-duplicate. And
`/reflect` couldn't clean up the mess, because `recall` is the tool it inspects memory with.

- Matching is now **scored, not all-or-nothing**: any meaningful word counts, and facts are
  ranked by how many distinct query words they match. Common words ("how", "the", "does")
  no longer count — so a naturally-phrased question finds the fact, while a query of
  nothing but filler correctly matches nothing.
- Plurals and possessives normalise: "coding styles" and "the user's coding style" both
  find `coding-style`.
- **The journal gets its own lane.** It matches line-by-line, so a short query used to bury
  a handful of facts under dozens of diary lines. Facts and journal are capped separately —
  and on truncation the journal keeps its **newest** entries, where before the newest were
  exactly what got cut.

Still pure standard library. No embeddings, no index to rebuild.

### `/context` — see what luban is actually loaded with

New command showing the always-on context sent every turn: each section (SOUL, USER,
project memory, tool guidance, skills, memory index, journal) with its size, the
stable/volatile split, tool-schema cost, and your conversation size.

It reports **real** token counts via the API rather than luban's internal estimate — that
estimate assumes ~4 characters per token and measured **~28% low** against the real
tokenizer, which is why the `/compact` nudge tends to fire late.

### Turns stop re-paying for the same context

luban re-sent the entire system prompt at full price every single turn. It now marks the
**stable** half (identity, your profile, project instructions, tool guidance, skills) as
cacheable — cache reads bill about a tenth of normal input.

Making that work needed a reordering: the **memory index and journal now come last**.
Caching matches on a prefix, so anything that changes invalidates everything after it — and
those two are exactly what luban rewrites mid-session whenever it saves a fact or a journal
line. Kept at the end, a memory write can no longer throw the cache away.

`/context` tells you whether your prefix is actually big enough to cache (below roughly
4,000 tokens nothing caches at all — and the API reports that failure silently). Set
`cache_prompt = false` to turn it off; unsupported backends fall back automatically.

### The tracker can retire an item nobody will ever fix

The enhancement tracker had exactly one way to close an issue: a release fixed it. So
anything *deliberately declined*, or solved outside luban, stayed Open forever — re-probed
on every upgrade, burying the issues that were still real.

An item can now close four ways: a **version** (fixed and verified), **wontfix** (a
deliberate design decision, with the reason recorded), **mitigated** (solved outside luban
core), or **obsolete**. Release notes can carry those verdicts in a **Decisions** section
and luban applies them on upgrade — so a decision reaches your tracker without a code
change having to happen first.

### Also

- Custom tools can contribute usage **`guidance`** to the system prompt, not just a
  description (E25) — useful once you have a suite of tools and the model needs to know
  when to reach for each and how they combine.

### Decisions

- **E22 — wontfix in core.** Standing per-project methodology reattaching at session start
  is best served by the project memory file (`LUBAN.md`/`CLAUDE.md`/`AGENTS.md`), which is
  auto-injected every session. An *enforced* auto-load hook would push skills into the
  always-on budget every session whether relevant or not — the exact opposite of the
  context work in this release. The project-memory gateway is the supported path.

## v0.5.14 — settings that take effect, turns that don't vanish, and a network that fights back

A batch of reliability fixes, most of them about the same failure shape: something goes
wrong and *nothing tells you*. A setting silently ignored, a write silently dropped, a
connection silently cut. Every one of these now speaks up.

### Your config settings actually take effect now (E19 follow-up)

`--sync-config` used to append new keys to the **end** of the file. In TOML, a `[table]`
header (like `[permissions]`) captures every key below it — so any key appended after
your `[permissions]` section became `permissions.effort`, `permissions.thinking`, etc.,
which nothing reads. Your setting was valid, present, and completely ignored.

- New keys are now inserted **above** the first `[table]` header, never at end-of-file.
- `luban --sync-config` now **repairs** an already-broken file: it lifts any swallowed
  top-level setting back above the header, keeping your value exactly as written.
- Every startup now **warns** about any setting that is present but being ignored, naming
  the table that captured it — so this can never hide again.
- `load_config` no longer swallows a parse error in silence; an unreadable `config.toml`
  says so on stderr instead of quietly reverting every setting to its default.

**If a setting of yours seems ignored, run `luban --sync-config` once.** It moves it back.

### A tool call cut off mid-turn no longer vanishes (E23, E24)

`max_tokens` is the ceiling on **one whole turn** — thinking + text + the tool call
combined. The old default (8192) was set before extended thinking existed, and raising
`effort` grows the thinking allocation without moving that ceiling. So reasoning could
consume the budget and the tool call at the end would be **cut off mid-write**.

luban has to strip a half-finished tool call (an unanswered one breaks the next request),
and it used to do that **silently** — so the model announced a write, no file changed, no
error appeared, and the model itself never learned the call was dropped, so it reported
success. That is the "it said it did it but nothing happened" symptom.

- A turn cut off mid-tool-call now tells **you** (a clear warning that nothing was
  written) and tells the **model** (so it retries the write smaller instead of assuming
  success). Bounded retries.
- `max_tokens` is now a **`config.toml` key** (default raised to 32000). Raise it if you
  run high/xhigh effort or ask for large writes. `--no-stream` clamps it (a large
  non-streamed response times out on the wire).
- The system prompt no longer invites the failure: an "I'll write the file now" and the
  actual tool call must be in the **same turn** — luban won't end a turn on work not done.

### The network fights back (transient-drop resilience)

Corporate gateways and proxies cut long-lived streaming responses ("peer closed
connection without sending complete message body") and return overloaded errors under
load. The SDK's own retries cannot cover a stream that dies **after** it started — only a
fresh request can.

- luban now **retries** a turn killed mid-stream, automatically and announced (the
  response restarts, so it says so rather than looking like the model repeating itself).
- **Overload (429/529) backs off far harder** than a dropped stream — and honors the
  server's `retry-after`. Retries are jittered so many clients behind one gateway don't
  march back in lockstep and sustain the overload.
- When retries are exhausted, **`/retry`** resends your prompt verbatim — a flaky gateway
  no longer costs you the message you just typed.
- Failure messages name the real cause (a proxy hung up, or the backend is saturated) and
  the actual remedy — never a misleading "raise your timeout."

### Sessions are named threads you can pick (E21 follow-up)

Running two threads in one project folder now works.

- `/new [title]` saves the current thread and starts another; `/title` renames the
  current one; both make sessions tell-apart-able.
- `/resume <number|id|name>` reopens a specific session (from `/sessions`, now numbered);
  `luban -r <number|id|name>` does the same from the shell. Bare `/resume` and `-r` are
  unchanged. `/sessions all` spans every folder.
- Fixed: switching threads used to carry the journal flag across, so the thread you
  switched *to* silently skipped its journal entry.

### Memory hygiene (H1–H3)

- The journal window now shows the two most recent **non-empty** days, so it no longer
  goes blank after a weekend gap.
- The memory index, when it overflows its budget, now drops **descriptions** before it
  ever drops a **fact** — so a fact never silently disappears from what luban knows exists
  (roughly 200 facts fit instead of ~50).
- Fixed an inverted cap warning that claimed your *newest* journal entries were being
  dropped when the opposite was true.

### Also

- A rejected file path now names luban's real home directory in the error, so on a
  relocated home (`LUBAN_HOME`) the next attempt can use the right `~/.luban` alias.
- README documents the full in-session command set and the two-threads-in-one-folder
  workflow.

## v0.5.13 — always-on context you can see, and continuity you can trust

**Your always-on files are no longer silently truncated.** Every turn, luban injects
these into the system prompt — and anything past a cap was being dropped with only
the *model* told, never you:

| # | Layer | Cap (chars) |
|---|-------|------|
| 1 | base prompt + platform + memory hygiene | — |
| 2 | **SOUL.md** (identity & standing instructions) | 4,000 |
| 3 | **USER.md** (who you're working with) | **4,000** (was 2,000) |
| 4 | **memory index** (one line per fact) | 4,000 |
| 5 | **journal** (today + yesterday) | 3,000 |
| 6 | **project memory** (LUBAN.md → CLAUDE.md → AGENTS.md) | 8,000 |
| 7 | skills catalog (names + descriptions) | — |

- **Over-cap files now warn YOU** — at startup and in `/config` — naming the file,
  its size, the cap, and how much is being dropped. Previously the `[truncated]`
  marker only ever reached the model, so an over-long USER.md looked like luban
  ignoring your instructions when it had simply never seen them.
- **`USER_MAX` raised 2,000 → 4,000** (peer of SOUL.md). Caps stay: an uncapped
  always-on file bloats every turn with no signal.
- **`/config` prints your always-on budget**, so you can see it before it bites.
- **Write-routing** is now part of luban's memory conventions: a standing preference
  → edit **USER.md**; luban's character → **SOUL.md**; a detail needed only when
  relevant → **remember** (a fact); a repeatable procedure → **a skill**;
  codebase-only → **the project memory file**. And the rule behind it: *never store
  always-on behaviour as a recallable fact — it can't know to recall it before it
  acts.*

**Continuity is restored, not re-narrated.**

- New **`/resume`** restores this project's last session **from its transcript** —
  deterministic and project-scoped, so it can't wander onto another project's thread
  the way inferring "where we left off" from the journal could.
- Resume now **leads with the project name**, and warns loudly if a session belongs
  to a different project.
- On a plain `luban` start, if this folder has a saved session (e.g. one you
  compacted then exited), luban **reminds you it's there** and how to resume it —
  so a compacted session no longer looks lost. Set `auto_continue = true` to reopen
  it automatically.

**Compaction nudge no longer cries wolf.**

- The token estimate now counts the message **text**, not the Python dict repr (which
  was inflating every count with keys and punctuation).
- The nudge threshold is now the **`warn_tokens`** config key, default **150,000**
  (was a hardcoded 60,000 — a fraction of a modern context window).

## v0.5.12 — UTF-8 across the whole process tree (child processes)

- Holistic fix for the cp1252 family across the whole process tree: luban now sets
  UTF-8 mode (`PYTHONUTF8`) in the environment so every child process it spawns
  starts in UTF-8 — a Python script run via `run_command` no longer crashes on an
  arrow or emoji — and it decodes those children's output as UTF-8 too. This closes
  the "spawned children" surface that E12 (own streams) didn't cover, and a code
  guard now keeps every UTF-8 surface (streams, files, env, child pipes) honest. (E20)

## v0.5.11 — calmer thinking, config discovery, cumulative upgrade notes, grep alias

- Tuned the thinking defaults after field use: **effort now defaults to `medium`**
  (not `high`) so easy tasks stay fast, and thinking now runs **silently by
  default** — no more grey reasoning text on every turn. New `/verbose [on|off]`
  (and `thinking_verbose` in config.toml) shows the reasoning when you want it;
  `/effort` still goes up to `xhigh`/`max`, and `/thinking off` turns it off.
- New: **`/config`** prints your effective settings, and **`luban --sync-config`**
  appends any config keys a newer luban added — as commented lines, preserving all
  your values — so shipped-but-gated features (web search, subagents, thinking
  settings…) are discoverable instead of silently missing from an old config. On
  upgrade luban now points this out. (E19)
- The upgrade "what's new" banner and tracker reconciliation now read the **full
  cumulative span** of releases since your last-seen version, not just the newest —
  so a multi-version jump doesn't miss intermediate fixes. (E17)
- `grep` now resolves the `~/.luban` path alias like the other file tools (still
  never exposing `~/.luban/*.py`). (E18)

## v0.5.10 — extended thinking on by default, adjustable effort

- luban now requests **adaptive extended thinking** by default, with effort set to
  **high** — so capable models actually reason before answering instead of running
  with thinking off. Change either per-session with `/thinking [on|off]` and
  `/effort [low|medium|high|xhigh|max]`, or set the default in config.toml
  (`thinking`, `effort`). Backends that don't support these parameters degrade to a
  plain request automatically.
- Web search turns that hit the API's internal iteration limit (`pause_turn`) now
  resume automatically instead of returning a truncated answer.

## v0.5.9 — web search, subagents, smarter memory

- New (off by default): `web_search = true` in config.toml offers the model the
  API's server-side web search tool, so it can pull in current information instead
  of asking you to paste it. Set `web_search_tool_type` to match your backend. (E11)
- New (off by default): `subagents = true` offers a `spawn_subagent` tool — the
  model can run a fresh read-only sub-agent on a focused subtask (research or
  investigate in parallel) and get back just the answer. (E15)
- `recall` now follows `[[wikilinks]]` between facts, so a short "pointer" fact
  that references another pulls the linked fact in too — keeping project notes as
  live pointers instead of stale copies. (E9)

## v0.5.8 — resume-crash fix, UTF-8 everywhere, optional out-of-tree edits

- Fixed: resuming a session that was closed mid-tool-call (or truncated at
  max_tokens) no longer crashes. luban never persists or replays a history that
  ends in an unanswered tool_use, and repairs already-broken session files on
  resume. A failed turn is reported instead of killing the session. (E14)
- Fixed the cp1252 encoding issue at its root: the standard streams are pinned to
  UTF-8 at startup, `read_file` reads UTF-8, and a policy test now fails the build
  if any file I/O forgets to pin the encoding — so this class of bug can't creep
  back one surface at a time. (E12)
- New (off by default): `allow_out_of_tree_file_edits` in config.toml lets the
  file tools read/write files outside the project (e.g. a sibling repo) via the
  same diff-and-confirm as run_command, instead of forcing clunky shell
  workarounds. Default off for corporate safety. (E16)

## v0.5.7 — file tools reach a relocated home

- Fixed: with `LUBAN_HOME` set to a synced folder (e.g. OneDrive), the file tools
  rejected `~/.luban/…` paths because `~` expanded to the OS home instead of the
  relocated home — so luban couldn't edit its own memory, tracker, config, or
  skills on a synced setup. The `~/.luban` alias now resolves to `LUBAN_HOME`.
  (E10 — a regression from v0.5.5)

## v0.5.6 — Windows write-crash fix + offline upgrade hook

- Fixed a crash when writing files containing non-Latin-1 characters (arrows,
  em-dashes, emoji, CJK) on Windows: all file writes are now UTF-8 and atomic, so
  a failed write can no longer truncate a file to 0 bytes, and an un-encodable
  character is reported instead of crashing the session. (E7, E8)
- `grep` now returns a clear error for a path that doesn't exist or is outside the
  searchable scope, instead of a misleading "(no matches)". (E4a)
- `recall` now matches on fact content by token, so multi-word queries like
  "coding style" find a fact named "yjm-coding-style". (E6)
- Documented that the file tools are intentionally jailed more tightly than
  `run_command` outside the project and ~/.luban. (E4)
- New: on detecting a new installed version, luban prints a "what's new" banner
  from this bundled changelog and reconciles your enhancement tracker against it
  on your next message — offline, and on by default for everyone.

## v0.5.5 — relocatable home for cross-device sync

- `LUBAN_HOME` relocates the whole ~/.luban tree (memory, skills, config,
  sessions, client) to e.g. a OneDrive folder so it syncs across devices;
  `luban --set-home <path>` persists it.

## v0.5.4 — memory that stays clean

- `/compact` no longer writes session narrative into the permanent fact store;
  durable facts come only from an explicit `remember` or `/reflect`.

## v0.5.3 — USER.md split

- Personal user facts moved from SOUL.md into a separate USER.md.

## v0.5.2 — self-improvement batch

- ~/.luban file access, sessions tool, model config key, and the enhancement
  tracker loop. (E1, E2, E3, E5)
