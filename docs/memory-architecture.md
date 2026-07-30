# luban's memory architecture

*How luban remembers things — and how to maintain your own agent memory well.*

This is the "why" behind the memory features. If you just want the commands, see
the **Long-term memory** section of the [README](../README.md). This document is
the mental model: what the different stores are *for*, why they're kept separate,
and the practices that keep the system healthy over months of use.

---

## The problem: a context window is not a memory

An LLM has no memory of its own. Everything it "knows" in a conversation is just
text sitting in its context window, and that window is small, expensive, and
wiped clean at the start of every session. So "giving an agent memory" really
means one thing: **deciding what text to put back into the window, and when.**

That framing — *context engineering* — is the whole game. Every design choice
below is an answer to "what earns a place in the context window, and how does it
get there?"

The mistake that causes most agent-memory pain is treating memory as one big
bucket: shove everything the agent might ever need into one store and hope
retrieval sorts it out. It doesn't. Different kinds of information have different
lifecycles, and mixing them causes **context rot** — the slow accumulation of
stale, low-value text that gets injected into every future turn and quietly
degrades the agent's judgment. luban avoids this by keeping **three separate
stores**, each matched to a kind of information.

---

## The three stores

| Store | Where | What it holds | Loaded into context? | Lifecycle |
|---|---|---|---|---|
| **Session** | `~/.luban/sessions/<id>.json` | The **full verbatim transcript** of a conversation, per project | **No** — re-read on demand (`--continue`, `--resume`, the `sessions` tool) | Kept indefinitely; the raw record |
| **Journal** | `~/.luban/memory/journal/YYYY-MM-DD.md` | Append-only, timestamped one-liners: *what happened, what was decided, what's next* | **Yes**, but only **today + yesterday** | Auto-decays — old days simply stop being loaded |
| **Facts** | `~/.luban/memory/*.md` + `MEMORY.md` index | Durable truths, **one per file**, with an always-loaded index | **Index always; full fact via `recall`** | Permanent until you `forget` it |

Plus the always-on blocks that frame every turn:

- **`SOUL.md`** — luban's *character and standing behavior*. How it should work,
  conventions to always follow, boundaries never to cross. Not about you — it's
  shareable verbatim.
- **`USER.md`** — *who you are*: role, expertise, environment, preferences. The
  one fact set that must stay always-visible, because the model can't know it's
  relevant until it's already too late to go fetch it.
- **The project's memory file** — `LUBAN.md`, or `CLAUDE.md`, or `AGENTS.md` in the
  project root: what's true inside *this* codebase and nowhere else.

Together with the fact index and the recent journal, these share **one budget** —
see *One shared budget for everything always-on* below.

**The one-line intuition:** the session is the full conversation, the journal is
a lightweight diary of it, and facts are the few durable truths distilled from
it. They are three levels of compression — **not three copies of the same thing.**

---

## First principles: why three, not one

The research literature on agent memory has converged on a small taxonomy of
memory *types*, and luban's stores map onto it almost exactly. This is not an
accident — it's what falls out when you take lifecycles seriously.

| Memory type | What it is | luban's implementation |
|---|---|---|
| **Working** | The live context window right now | The current session's messages + the injected memory block |
| **Episodic** | Instance-specific: *what happened, when, in what context* — kept verbatim | **Session transcripts** (full fidelity) + the **journal** (a compressed timeline) |
| **Semantic** | Abstracted, generalized, durable knowledge | **Facts** + their index + **USER.md** |
| **Procedural** | How to behave, how to do a task | **SOUL.md** + skills + config |

The single most important rule in the whole system comes from a well-known
finding about episodic memory:

> An agent that *summarizes at write time* collapses distinct episodes into
> semantic generalizations — destroying the episodic signal before it can be
> used.

In plain terms: **don't turn "what happened" into a permanent "fact."** They are
different tiers. A session is episodic — rich, specific, and safe to let age out
of context (the transcript is still on disk if you need it). A fact is semantic —
a durable truth that deserves to be re-injected forever. If you write session
narrative into the fact store, you get the worst of both: the episode is
flattened *and* the fact store fills with noise that pollutes every future turn.

This is exactly the bug the memory-hygiene work fixed. Before a `/compact`,
luban writes to the **journal** (episodic timeline), never to **facts**
(semantic) — and it's structurally prevented from doing otherwise: the flush
turn is handed *only* the `journal` tool, and any other tool call is rejected at
dispatch. The full episode is preserved losslessly in the session transcript.
Each tier stays in its lane.

### The graduation question

The deep question in any memory system is: **when does an episode become a
fact?** The literature calls this the *transition policy*, and there is no
automatic answer — it's a judgment call, and getting it wrong in the
"promote too eagerly" direction is what causes context rot.

luban's policy is deliberately conservative: **episodic → semantic promotion
happens only through an explicit `remember` (by you) or during `/reflect`.**
Most sessions produce zero new facts, and that's correct. A good fact passes a
simple bar:

> Will this still be true in a month, and outside this one project?

A user preference, a standing decision, an environment truth — yes. Session
events, task progress, "we discussed X today" — no; those belong in the journal
and the transcript.

---

## One shared budget for everything always-on

Whatever is sent on *every* turn competes for the same scarce thing: the model's
attention. `SOUL.md`, `USER.md`, the fact index, the recent journal, and the
project's own memory file are therefore governed by **one budget**, not five
separate caps.

That was a correction, and the reasoning behind it is worth stating plainly
because it generalizes to any agent-memory system:

- **Five independent caps couldn't express the thing that matters.** Each file
  could sit comfortably under its own limit while the total was far too large.
  The only property with real consequences is how much of the model's attention
  the always-on block consumes *in total*.
- **Every time a cap bit, the temptation was to raise it or make it
  configurable.** A config knob for this is an admission that the right value is
  unknown, handed to the user — who has even less basis to choose it. The budget
  is instead *derived*: hold always-on near a tenth of the working context, at a
  measured (not assumed) characters-per-token ratio, minus what the base prompt
  and tool schemas already spend.
- **Nothing is silently cut.** The earlier design head-truncated each file to fit
  and told only the model, never the human — so a real profile lost its tail and
  luban looked like it was ignoring instructions it had never seen. Now a file is
  trimmed only if it *alone* exceeds the entire budget, which means something went
  wrong rather than something grew. Going over the total produces a warning to the
  person who can act on it.
- **Each contributor has its own remedy**, because they aren't the same kind of
  thing. Hand-edited prose (`SOUL.md`, `USER.md`, project memory) can be
  compacted. The index is generated from the fact files, so it shrinks by curating
  *them*, never by editing it. The journal already self-limits to recent days and
  needs nothing.

The deeper lesson: **rationing is not curation.** A cap is a way of avoiding the
judgment call about what deserves to be there, and it fails quietly. The budget's
real job is to be a forcing function for the curation pass below.

## Retrieval: a fetch, not a search

`recall` looks like a search tool and mostly isn't one, by design.

The index — every fact's slug and one-line description — is already in the prompt
on **every turn**. The model therefore doesn't need to *discover* what exists; it
can read the catalog and name what it wants. Passing an exact slug returns that
note whole. This is the intended path, and it's why the system needs no
embeddings, no vector store, and no ranking model — which also means it works on
a locked-down machine with no network.

A fuzzy query is the fallback, not the main road. It scores facts by how much of
the query they cover (normalized for length, so a long note can't win by
accident), follows `[[wikilinks]]` one level, and returns the best few. Two
details matter more than the scoring:

- **A miss says so honestly.** It reports that nothing matched *and* that this is
  not evidence the fact doesn't exist, pointing back at the index. Otherwise the
  agent's natural response to a failed lookup is to save a duplicate.
- **Documents are not facts.** A long, hand-maintained document living in the
  memory folder — an issue tracker, say — will win almost any search about a
  problem it once recorded, simply by quoting it. Such files are excluded from
  fuzzy matching and surface only when named exactly. Filing a document as a fact
  is the actual error; tuning the ranking only masks it.

## Curation: the mechanism that makes the rest work

Every store that can be written to needs a **retire path**, or it becomes a
landfill. `/reflect` is that path, and it runs as an isolated turn so the live
conversation is untouched.

It is handed the **complete** fact store — not a search result. An earlier version
gave the curator a capped `recall` view, roughly a tenth of what it was being
asked to curate, which is why consolidation never happened. *Don't ration the
curator.* Along with the store it gets duplicate candidates and the always-on
ledger, then works through: survey → merge duplicates into one better note →
resolve contradictions in favour of what's true now → delete anything the
transcripts, journal, or project files already record → graduate → tighten →
report. Every change is an ordinary write, shown as a diff you confirm.

**Graduation is the delicate step.** A pattern about *how you want work done* is a
standing instruction, not a look-up detail — and a recallable fact cannot govern
behaviour, because the agent can't know to recall it before acting. Such things
belong in `USER.md`. But `USER.md` is sent every turn, so every line costs
forever: graduation is a **trade**, not an append. The bar is "would I want this
in front of the model on every turn for the next year?", the unit is one tight
line folded into an existing bullet where possible, and if the file is near budget
something weaker gets tightened or dropped in the *same* edit. Promoting nothing
is a perfectly good outcome.

Deleting is safe, and that's what makes ruthlessness reasonable: every session
transcript and journal day stays on disk permanently. A deleted fact loses the
*re-injection*, not the record.

---

## Inspiration: the Markdown "LLM wiki" pattern

luban's fact store is a deliberate implementation of the **Markdown knowledge-base
pattern** popularized by Andrej Karpathy (["LLM
Wiki"](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)): rather
than a vector database or an opaque RAG pipeline, you keep a set of **plain
markdown files** that the agent incrementally compiles and maintains, with a few
navigation conventions on top.

The canonical pattern is three layers — **raw sources → wiki pages → a schema/index**
— run in a loop of *ingest → query → lint*. luban maps onto it one-to-one:

| LLM-wiki pattern | luban |
|---|---|
| Raw sources | Session transcripts |
| Wiki pages (atomic notes) | `memory/*.md` fact files |
| `index.md` (catalog) | `MEMORY.md` |
| `log.md` (timeline) | `journal/` |
| Schema (the rules) | `SOUL.md` + the built-in memory hygiene instruction |

Two properties make this pattern work, and luban enforces both:

- **Atomic notes.** One fact per file. Small, single-purpose, easy to update or
  delete without disturbing anything else.
- **Update before duplicate.** When something changes, edit the existing note —
  don't add a second one that contradicts it. luban's memory instruction says
  exactly this: *update or forget stale facts instead of duplicating.*

And a guardrail the pattern insists on: **the index is machine-maintained, never
hand-edited.** `MEMORY.md` is rebuilt from the component files; you (and the
agent) edit the facts, and the index follows. Editing the index directly is how
a wiki drifts out of sync with itself.

### Why plain files (and not a vector database)

- **Portable and inspectable.** It's just text. You can read it, `grep` it, diff
  it, put it in git, or hand it to a colleague. Nothing is hidden in an
  embedding you can't audit. (Because it's just a folder, you can relocate the
  whole store to a cloud-synced directory with the `LUBAN_HOME` environment
  variable — see the README's "Sync across devices" section — and your memory
  follows you between machines.)
- **Zero infrastructure.** No vector store, no embedding model, no network — which
  matters a great deal in a locked-down environment. Retrieval is by *name* via
  the index + `recall`, not by similarity search.
- **The transcripts are still your verbatim store.** If you ever want
  "search everything I've ever done," the session transcripts already hold it
  losslessly — a search over them can be added without changing the model.

There's a whole family of memory systems that take the opposite bet — store
*everything* verbatim and lean on vector/semantic search to surface it later
(see *Further reading*). That trades "spend tokens deciding what to keep" for
"spend infrastructure storing and searching everything." It's a reasonable bet
when you have the infrastructure; luban deliberately doesn't require any, which
is the right call for offline and corporate-locked machines.

---

## How to maintain *your own* agent memory well

Whether you use luban or any other markdown-backed agent, these practices keep
the system healthy:

1. **Keep the tiers separate.** Diary entries are not facts. If you catch the
   agent writing "we talked about X today" into a permanent note, that's rot in
   the making — move it to the journal.
2. **Raise the bar for facts.** Before saving one, ask the month-and-outside-this-project
   question. When in doubt, don't. An empty fact store is far healthier than a
   noisy one.
3. **Update, don't duplicate.** One truth, one note. Edit it when it changes;
   `forget` it when it's dead.
4. **Let the index be rebuilt, not written.** Curate the notes; leave `MEMORY.md`
   to the machine.
5. **Reflect periodically.** `/reflect` is the *lint* pass — a moment to promote
   real facts out of the journal, prune stale ones, and catch contradictions.
   Do it occasionally, not every turn.
6. **Cross-link with `[[wikilinks]]`.** When one fact refers to another, link it
   by name (`[[some-other-note]]`). `recall` **follows** these one level, pulling
   in the linked notes alongside the match, so a small hub note that points at
   several others is a genuinely useful shape. A link to a note that doesn't exist
   yet is harmless — it marks something worth writing later. They also pay off
   outside luban: point a graph-capable markdown editor (Obsidian, Logseq) at
   `~/.luban/memory/` and you get backlinks and a graph view for free, because the
   files are already in that shape. No app is required to *use* the memory; one
   just makes it navigable to browse.
7. **Watch the total, not each file.** Everything always-on shares one budget
   (below). Adding a line to `USER.md` spends the same budget the fact index and
   the journal draw on — so promoting something is a trade, not an append.

---

## Further reading

- Andrej Karpathy, ["LLM Wiki" gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
  — the markdown-knowledge-base pattern this design follows.
- ["From Storage to Experience: A Survey on the Evolution of LLM Agent Memory
  Mechanisms"](https://arxiv.org/pdf/2605.06716) — the memory-type taxonomy.
- ["Memory for Autonomous LLM Agents: Mechanisms, Evaluation, and Emerging
  Frontiers"](https://arxiv.org/html/2603.07670v1).
- On episodic vs semantic and the "summarize-at-write-time" hazard:
  [Episodic Memory for AI Agents](https://atlan.com/know/episodic-memory-ai-agents/).
- Verbatim-store + semantic-search systems (the opposite design bet) — e.g.
  spatial-metaphor stores like *MemPalace*, and vector-memory frameworks — are
  worth knowing as the contrasting philosophy; luban trades their retrieval power
  for zero-dependency portability.
