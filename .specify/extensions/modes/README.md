# Spec Kit Build Modes

A Spec Kit extension that lets you pick how each task gets built:

| Mode | What happens | Cost | Use for |
|---|---|---|---|
| `direct` | Do exactly what the task says, once (stock implement) | 1× | Setup, wiring, config, tests from contracts, most tasks |
| `bestof:N` | N independent attempts, tests gate them, a separate blind judge picks one | N+1× | Algorithms, parsers, data models, API shapes: several good designs, an objective judge |
| `gauntlet` | Builder vs a harsh, blind critic comparing against a real reference; loop until ours wins | Unbounded (2 agents per round) | UI, copy, visual design, performance against a named benchmark |

Gauntlet is based on Matt Shumer's gauntlet loop technique, as packaged by RoboNuggets (https://github.com/robonuggets/gauntlet-loop, CC BY 4.0).

## Install

From your project root (Spec Kit ≥ 0.9 already initialised):

```bash
specify extension add --dev /path/to/spec-kit-modes
```

Optional config:

```bash
cp .specify/extensions/modes/config-template.yml .specify/extensions/modes/modes-config.yml
```

## Workflow

```text
/speckit.specify ...           → optional hook: /speckit.modes.bar   (sets the Quality Bar in spec.md)
/speckit.plan
/speckit.tasks                 → optional hook: /speckit.modes.tag   (writes [mode:...] per task)
/speckit.modes.implement       (instead of /speckit.implement)
```

Examples:

```text
/speckit.modes.implement                         # tags decide, untagged = default_mode
/speckit.modes.implement --mode bestof --n 4     # untagged tasks get best of 4
/speckit.modes.implement --mode gauntlet --only T014 --max-rounds 10
/speckit.modes.implement --mode direct --force-mode   # bruteforce everything, ignore tags
```

Task tags in tasks.md:

```text
- [ ] T014 [P] [US1] [mode:gauntlet] Build landing hero in src/components/Hero.tsx
- [ ] T015 [US1] [mode:bestof:3] Implement search ranking in src/search/rank.ts
- [ ] T016 [US1] [mode:direct] Wire Hero into src/pages/index.tsx
- [ ] T020 [US2] [mode:gauntlet bar=https://linear.app/pricing] Build pricing table
```

## What every mode shares

- **Done checks**: three to six yes/no questions per task, each verifiable in about ten seconds, reported PASS/FAIL with evidence. They gate every candidate before any comparison, and a task can't be ticked off while one fails.
- **Plain briefs**: subagents get a short coworker-style brief with context, reasons and an example of good, not role-play, numbered steps or capitals.
- **Fresh-eyes reviewers**: judges and critics are told "none of these is yours" and never see the builder's reasoning.
- **Effort per role**: medium for direct work and best-of-N builders, high for judges and critics and for gauntlet builders, xhigh for the lead on long gauntlet runs. Set it under `effort:` in the config.
- **Lessons**: recurring gaps go into `modes/lessons.md` and are included in every later brief, so a correction sticks.
- **Handoff**: `modes/handoff.md` is kept current; when a session gets long, start fresh with `/speckit.modes.implement --resume`.
- **Small lead context**: diffs, screenshots and logs go to files; only short reports stay in the main conversation.

## Notes

- Gauntlet needs a bar that is **named, fetchable and comparable**. Without one the critic invents the comparison and approves everything, so the command refuses to run gauntlet without a bar.
- `gauntlet.max_rounds: 0` means no cap (the technique's default). Set a cap if cost matters.
- Best-of-N uses git worktrees per attempt and cleans them up afterwards.
- Works best in agents that can spawn subagents (Claude Code). Without subagents, attempts run sequentially from the brief alone.
- Outputs land in `specs/<feature>/modes/`: `run-log.md`, `progress.md`, one detail file per task.
