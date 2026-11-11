---
name: speckit-modes-tag
description: Propose and write a [mode:...] tag for each task in tasks.md (direct, bestof:N or gauntlet)
compatibility: Requires spec-kit project structure with .specify/ directory
metadata:
  author: Lourens Botha (CDA)
  source: modes:commands/speckit.modes.tag.md
---

# Tag Tasks with Build Modes

Give each task in `tasks.md` the cheapest mode that will still produce the quality it needs, so `/speckit-modes-implement` spends effort where it matters.

## User Input

```text
$ARGUMENTS
```

The user may give instructions such as "gauntlet everything in US1", "bestof:4 for the parser tasks" or "all direct except the hero". Those override your own choices.

## Steps

1. **Find the feature.** Run `.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` (Windows: `.specify/scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks`) from the repo root and parse `FEATURE_DIR`. If tasks.md is missing, tell the user to run `/speckit-tasks` and stop.

2. **Read** `tasks.md`, `spec.md` (especially `## Quality Bar`) and `plan.md`. Load `.specify/extensions/modes/modes-config.yml` if it exists to learn `default_mode` and `best_of_n.n`.

3. **Choose a mode per task** using these rules:
   - `[mode:direct]` — the task is mechanical or has one obviously right answer: setup, scaffolding, config, migrations, wiring, renames, writing tests from a contract, docs updates. Most tasks belong here.
   - `[mode:bestof:N]` — the task has several plausible designs and an objective judge exists (tests, a benchmark, the acceptance criteria): an algorithm, a parser, a data model, a tricky refactor, an API shape. Omit `:N` to use the configured N.
   - `[mode:gauntlet]` — quality is judged by comparison with something real and taste matters: user-facing UI, copy, visual design, performance-critical paths with a reference benchmark, anything the Quality Bar names. Only tag gauntlet if `spec.md` has a `## Quality Bar`, or a per-task bar is given as `[mode:gauntlet bar=<reference>]`. If gauntlet looks right but no bar exists, tag `bestof` instead and tell the user to run `/speckit-modes-bar`.

   Never tag a test-writing task (TDD tests that must fail first) as gauntlet.

4. **Show the proposal** as a table (Task ID, short description, proposed mode, one-line reason) and the estimated relative cost: direct = 1, bestof:N = N + 1, gauntlet = unbounded (2 per round). In interactive mode, wait for approval or edits. In automated mode, proceed.

5. **Write the tags** into `tasks.md`. Insert the tag after the existing `[P]` / `[USn]` markers and before the description, and replace any existing `[mode:...]` tag:

   ```text
   - [ ] T014 [P] [US1] [mode:gauntlet] Build landing hero in src/components/Hero.tsx
   - [ ] T015 [US1] [mode:bestof:3] Implement search ranking in src/search/rank.ts
   - [ ] T016 [US1] [mode:direct] Wire Hero into src/pages/index.tsx
   ```

   Do not change task IDs, order, checkboxes or any other text.

## Done When

- [ ] Every unchecked task in tasks.md has exactly one `[mode:...]` tag
- [ ] No gauntlet tag exists without a Quality Bar or a per-task `bar=`
- [ ] The user approved the table (interactive mode)