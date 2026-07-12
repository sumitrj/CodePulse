# The A/B — decide for yourself

Same repo, same questions, two Claude Code sessions. One has the map, one
doesn't. You judge time, tokens, and accuracy. Nothing here is on the honor
system — every number comes off your own screen.

## Order matters: run WITHOUT first

**Session B — without CodePulse.** Open Claude Code in a clean checkout
(no setup run). Ask the five questions below, one per message. After the last
answer, record wall time, then run `/cost` and record tokens.

**Session A — with CodePulse.** Run the one command:

```
/path/to/CodePulse/setup.sh /path/to/your/repo
```

Start a fresh Claude Code session in the repo, approve the `codepulse` server
when prompted, ask the same five questions, record the same numbers.

## The five questions

Pick `<X>` = a function you know well; `<F>` = a feature you know the location of.
Knowing the ground truth is the point — you grade accuracy, not us.

1. What are the main entry points of this repo, and what does each one drive?
2. What is `<X>`, who uses it, and what breaks if I change its behavior?
3. Where does `<F>` live?
4. What did my current uncommitted changes touch, and who is at risk? *(make a small edit first)*
5. Explain this repo's architecture in ten lines.

## Score card

| | Session B (without) | Session A (with) |
|---|---|---|
| Wall time | | |
| Tokens (`/cost`) | | |
| Tool/search calls you watched it make | | |
| Accuracy, 1–5, your judgment | | |
| Anything it got wrong | | |

## What to expect (so you can catch us if it's false)

- A should answer questions 1, 2, 5 in one or two tool calls each — no file reading.
- A's answers cite `path :: name` with line numbers you can check.
- Where the map can't see (dynamic dispatch, string-built references), A should
  *say so* rather than guess. If it bluffs, that's an accuracy point off — ours.
