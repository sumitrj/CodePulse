# The explanation ladder

CodePulse introduces several new concepts at once. New concepts are a tax on
the reader. This page is the constitution for how we spend that tax — in the
README, the website, the skill, the panel, and every pitch. If copy anywhere
contradicts this ladder, the copy is wrong.

## Three rules

1. **One rung at a time.** Never mention a concept before the reader has the
   ones above it. The ladder below is the only allowed order.
2. **One new noun per rung.** Each rung may introduce exactly one word the
   reader must learn (*map, readers, questions, recipes*). Everything else is
   plain words they already know.
3. **Sentence, then example — never a definition.** Each concept is one
   talking sentence plus one concrete example. If it needs a third sentence,
   the concept is being explained wrong (or is on the wrong rung).

## The ladder

| # | Concept | The sentence | The example | Banned words |
|---|---|---|---|---|
| 1 | **The map** | CodePulse keeps one live map of everything in your repo and what touches what — across every language. | The YAML key three Python functions read is on the map, with edges. | entity-relation kernel, graph of record, system of record |
| 2 | **Two readers** | You read the map in an editor panel; your AI agents read the identical answers over MCP. | Ask "who touches DB_URL" by clicking, or your agent asks it as a tool call — same answer. | shared surface stance, delivery surfaces |
| 3 | **The questions** | You can ask the map six things: what is this, who touches it, what breaks if I change it, what changed, where does X live, what are the entry points. | Before editing `total()`, ask *what breaks* — it lists every dependent, hops deep. | verbs, query surface, boards (say "saved views" if needed) |
| 4 | **Fresh** | Save a file; the map is correct about two seconds later. | Only the saved file re-parses — never the whole repo. | incremental extraction, freshness NFR, kill-gate |
| 5 | **Honest** | The map only claims what it can prove; where it can't see, it says so. | A function called through dynamic dispatch shows "0 known callers — likely dynamic," not a guess. | honest-over-complete (internal), confidence scores |
| 6 | **Recipes** | A language is a YAML file, not code — six ship today, and writing a seventh needs no engine change. | The whole Python language support is 50 lines of YAML. | extraction kernel, dialect, tree-sitter (until asked how) |
| 7 | **The proof** | Same five questions with and without the map: about 8× fewer tokens, measured — and the benchmark is in the repo so you can measure it yourself. | demo/benchmark.md — you pick the questions, you score it. | revolutionary, blazing, game-changing, any superlative |

## What this forbids

- Opening with architecture. Nobody gets "engine, resolver, provenance" before
  rung 1 has landed.
- Introducing "recipes" before "map" (rung 6 before rung 1) — the most common
  temptation, because recipes are the clever part. Clever is not first; useful is first.
- Claiming without a number, and numbers without a method. Every performance
  claim links to the benchmark.
- More than one metaphor per rung. The map is *the* metaphor; don't stack
  "nervous system", "X-ray", or "GPS" on top of it.
