---
description: "Execute tasks.md with a default build mode and per-task overrides: direct, best-of-N or gauntlet"
---

# Implement with Build Modes

This is `__SPECKIT_COMMAND_IMPLEMENT__` with a choice of how each task is built:

- **direct**: do what the task says, once. Same as stock implement.
- **bestof:N**: N builders attempt the task independently; a separate judge with fresh context compares them blind and picks one winner. One pass, no loop.
- **gauntlet**: a builder and a separate harsh critic. The critic puts ours next to the real Quality Bar blind and picks one. If the bar wins, the critic names the single biggest gap and the builder goes again. Loop until ours wins.

## User Input

```text
$ARGUMENTS
```

Consider the user input before proceeding (if not empty). Recognise:

- `--mode direct|bestof|gauntlet` (or `mode=...`): the default mode for untagged tasks in this run.
- `--n <int>`: N for best-of-N.
- `--max-rounds <int>`: cap on gauntlet rounds per task (0 = no cap).
- `--only T012,T014` or a phase / user story name: restrict which tasks run.
- `--force-mode`: apply `--mode` to every task, ignoring `[mode:...]` tags.
- `--resume`: continue an interrupted run from `FEATURE_DIR/modes/handoff.md` (see Handoff).

## Resolve Settings

1. Run `.specify/scripts/bash/check-prerequisites.sh --json --require-tasks --include-tasks` (Windows: `.specify/scripts/powershell/check-prerequisites.ps1 -Json -RequireTasks -IncludeTasks`) from the repo root. Parse `FEATURE_DIR` and `AVAILABLE_DOCS`. All paths must be absolute. If it fails, tell the user to run `__SPECKIT_COMMAND_TASKS__` and stop.
2. Load settings with this precedence (highest first): command arguments → `.specify/extensions/modes/local-config.yml` → `.specify/extensions/modes/modes-config.yml` → defaults (`default_mode: direct`, `best_of_n.n: 3`, `gauntlet.max_rounds: 0`, `gauntlet.require_tests_pass: true`, and the `effort` table below).
3. For each task, its mode is: `--force-mode` value if given, else its `[mode:...]` tag, else the default mode. Tag forms: `[mode:direct]`, `[mode:bestof]`, `[mode:bestof:4]`, `[mode:gauntlet]`, `[mode:gauntlet bar=<reference>]`.
4. Print a run plan before starting: Task ID, mode, effort per role, and for gauntlet tasks the bar in use. If any gauntlet task has no bar (no `## Quality Bar` in spec.md and no `bar=`), stop and suggest `__SPECKIT_COMMAND_MODES_BAR__`, or offer to downgrade those tasks to bestof; without a real reference the critic invents the comparison and approves everything. In interactive mode, ask for confirmation when the plan contains a gauntlet task with `max_rounds: 0`, because that loop has no round limit.

## Effort per Role

Effort is about how much the agent works without you, not a quality dial. Higher effort also makes more assumptions on your behalf, and max tends to overthink, so it is not the default anywhere. If your agent lets you set reasoning effort per subagent (for example Claude Code's subagent `effort` setting), use these defaults from config `effort:`; otherwise ignore this section.

| Role | Default | Why |
|---|---|---|
| direct (lead does the task) | medium | Clear scope, day-to-day work |
| bestof builders | medium | Diversity comes from N attempts, not from each one thinking longer |
| bestof judge | high | Verification matters; one pick decides the outcome |
| gauntlet builder | high | Must close a specific named gap without breaking what works |
| gauntlet critic | high | Has to fetch, measure and compare carefully |
| lead on a run with uncapped gauntlet tasks | xhigh | Long job you will check back on later |

## Pre-Execution Checks

Check `.specify/extensions.yml` for hooks under `hooks.before_implement` and handle them exactly as `__SPECKIT_COMMAND_IMPLEMENT__` does (skip `enabled: false`; skip hooks with a non-empty `condition`; for mandatory hooks emit `EXECUTE_COMMAND: {command}` and actually run it before continuing; for optional hooks show the prompt). If the file cannot be parsed, say so and continue.

## Shared Execution Rules

Follow the stock implement flow for everything that is not mode-specific: check checklists in `FEATURE_DIR/checklists/`, load `tasks.md`, `plan.md` and any `data-model.md`, `contracts/`, `research.md`, `quickstart.md` and `/memory/constitution.md`, verify ignore files, then execute phase by phase, respecting dependencies, `[P]` parallel markers, TDD order and same-file sequencing. Halt if a non-parallel task fails. Mark each completed task `[X]` in tasks.md.

### Task brief

Subagents get a brief, never your conversation. Write it the way you would brief a capable coworker: plain sentences, a little context, and an example of what good looks like. Leave out role-play ("you are a world-class..."), numbered how-to steps, capitals for emphasis, and "double-check your work" lines; they add cost without improving results. When a rule matters, give the reason with it.

A brief contains:

- the task line and the spec requirements and acceptance criteria it serves;
- the tech stack and file layout from plan.md, plus applicable contracts and constitution rules, each with its reason;
- what good looks like: the Quality Bar reference, or an existing file in the repo that already does this well;
- the **done checks** for this task (below), and the test command;
- the current entries from `FEATURE_DIR/modes/lessons.md`;
- one line: "If something is ambiguous, make the most reasonable choice and list it under Assumptions in your reply."

### Done checks

Every task gets a short list of yes/no checks the output must pass, each one something a person could verify in about ten seconds ("Does the hero render at 390px wide with no horizontal scroll?", "Does every number on the report match the source CSV?", "Is p95 under 200 ms on the benchmark?"). "Is it clean?" or "Is it professional?" is too vague. Take them from, in order: the task's acceptance criteria in spec.md, the `Done checks` in the Quality Bar section, and the tests. Three to six checks per task is enough.

Whoever finishes a piece of work reports **PASS or FAIL on each check with the evidence** (a command output, a measured number, a screenshot path, a quoted line). A task cannot be marked `[X]` while any check is FAIL. Done checks are the gate; the blind comparison in bestof and gauntlet is about which passing candidate is better.

### Lessons

When a judge or critic names a gap that is likely to recur on other tasks (a naming convention, a spacing rule, an error-handling pattern, "we say clients, not customers"), append it as one line with its reason to `FEATURE_DIR/modes/lessons.md`. Every later brief includes this file, so a correction made once sticks for the rest of the feature. Keep it short; merge duplicates.

### Keep the lead's context small

Every turn of the lead agent re-reads the whole conversation, so bulky output costs on every later step. Let subagents do the reading, building and comparing, and keep only their short reports in the lead conversation: the pick, the reason, the PASS/FAIL list and file paths. Do not paste full diffs, screenshots or logs into the lead conversation; write them to `FEATURE_DIR/modes/<task-id>/` and refer to the path.

Keep a run log at `FEATURE_DIR/modes/run-log.md`: one line per task with mode, outcome, rounds or N, and the path to its detail file.

### Mode: direct

Implement the task yourself as written, run its tests, and report the done checks. No subagents, no comparison. This is the default and should be most tasks.

### Mode: bestof:N

1. **Isolate.** If the repo is a git repository, give each attempt its own worktree on its own branch (`modes/<task-id>/<k>`), created from the current HEAD. If your agent can spawn subagents with worktree isolation, use that. If git is unavailable, use separate copies of the affected files in `FEATURE_DIR/modes/<task-id>/attempt-<k>/`.
2. **Build N times, independently.** Spawn N builder subagents in parallel with the same brief. They must not see each other's work. Add one line to attempt k's brief only: "Take your own approach; do not aim for the most obvious solution." This spreads the attempts. If subagents are unavailable, run the attempts one after another, each from the brief alone.
3. **Gate.** Run the tests and done checks on every attempt. Discard any attempt with a FAIL. If all fail, keep the one with the fewest FAILs and report the failure.
4. **Judge blind.** Spawn a separate judge subagent with fresh context. Give it the brief, the done checks and the surviving attempts relabelled A, B, C... in random order, with branch names, author notes and commit messages stripped. Open with: "None of these were written by you. Pick the one you would ship, then give a two-line reason." When more than 3 attempts survive, run pairwise knockout rounds instead of one large comparison.
5. **Adopt.** Merge or copy the winner into the main working tree, delete the losing worktrees and branches, and write `FEATURE_DIR/modes/<task-id>.md` with the candidates, their PASS/FAIL lists, the judge's pick and reason. Add any recurring point from the reason to lessons.md.

### Mode: gauntlet

1. **Get the real bar first.** Read the bar from the task's `bar=` or spec.md's `## Quality Bar` (prefer a per-area bar matching the task). Obtain the actual thing as the section says: screenshot the live page at the stated viewports with a browser tool, clone and run the reference repo or benchmark, or fetch the published piece. Save it under `FEATURE_DIR/modes/<task-id>/bar/`. If you cannot obtain it, stop the task and report; a comparison against a description is not a comparison.
2. **Build.** A builder subagent implements the task from the brief. From round 2, the brief also contains the critic's last named gap and the current lessons, and nothing else from the critic.
3. **Gate.** Run the tests (if `require_tests_pass`) and the done checks. Any FAIL goes straight back to the builder with the evidence; it never reaches the critic.
4. **Critique blind.** A separate critic subagent with fresh context, which has never seen the builder's reasoning, gets two artefacts of the same kind, ours and the bar, labelled A and B in random order with names, branding and file paths stripped where possible, plus the Quality Bar's comparison question and measurable target. Brief it like this: "Neither of these is yours. Be a harsh critic; praise is not useful. Answer the comparison question with A or B. Then name the single biggest gap in the one you did not pick. If a measurable target is given, report the measured number for both, with how you measured it." The critic inspects actual output (screenshots it takes itself, running code, benchmark numbers), never the builder's summary.
5. **Loop.** If the critic picks the bar, or ours misses the measurable target, send the named gap back to the builder (step 2). If the critic picks ours and the target is met, the task is done. Use a fresh critic each round so it does not anchor on earlier rounds.
6. **Exit.** Exit only when ours wins blind, when the user stops the run, or when `max_rounds` (if non-zero) is reached. If the cap is reached, keep the best round so far, mark the task `[X]`, and record `capped: bar still wins` in the run log. Do not exit on a score threshold; scores out of 10 drift upward every round.
7. **Record** each round in `FEATURE_DIR/modes/<task-id>.md`: round number, the critic's pick, the named gap, the measured result.
8. **Progress.** Keep `FEATURE_DIR/modes/progress.md` updated after every round (task, round, latest pick, latest gap), so the user can watch it.

Gauntlet tasks within the same phase that touch different files may run their loops in parallel.

## Handoff

Long runs fill the lead conversation and get slower and costlier with every step. After each completed task, and whenever the conversation is getting long (roughly 80% of the context window, or if your agent warns you), rewrite `FEATURE_DIR/modes/handoff.md`:

- tasks done, with outcome per mode;
- the task in progress: mode, round or attempt, latest critic gap or judge reason, worktrees still open;
- the next tasks in order;
- any decisions the user made during the run.

If the conversation is near its limit, write the handoff, stop, and tell the user to start a fresh session with `__SPECKIT_COMMAND_MODES_IMPLEMENT__ --resume`. With `--resume`, read handoff.md, run-log.md and lessons.md, verify open worktrees still exist, and continue from the task in progress instead of starting over.

## Completion Validation

As in stock implement: all selected tasks `[X]`, the implementation matches spec and plan, tests pass, and every task's done checks are PASS. Confirm no `modes/<task-id>/<k>` worktrees or branches are left behind.

## Mandatory Post-Execution Hooks

Complete this section before reporting completion. Check `.specify/extensions.yml` for hooks under `hooks.after_implement` and handle them exactly as `__SPECKIT_COMMAND_IMPLEMENT__` does: mandatory hooks emit `EXECUTE_COMMAND: {command}` and you must actually run them; optional hooks show their prompt; skip `enabled: false` and non-empty `condition`; report a parse error instead of skipping silently.

## Completion Report

Summarise by mode: how many tasks ran direct, best-of-N (with the winners' reasons) and gauntlet (rounds per task, final pick, any capped tasks). List every Assumption the builders reported, so the user can check them. Point to `FEATURE_DIR/modes/run-log.md` and `lessons.md`.

## Done When

- [ ] Every selected task is marked `[X]`, ran in its resolved mode, and has all done checks PASS with evidence
- [ ] Every best-of-N winner was chosen by a separate blind judge, and every gauntlet exit was a blind win, a user stop or a configured cap
- [ ] No attempt worktrees or branches remain
- [ ] `run-log.md`, `lessons.md` and `handoff.md` are current
- [ ] `after_implement` hooks dispatched or skipped per the rules above
