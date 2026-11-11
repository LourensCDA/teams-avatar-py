---
description: "Add or refresh a Quality Bar section in spec.md: a named, fetchable, comparable reference plus a measurable target"
---

# Set the Quality Bar

Gauntlet mode only produces quality if the thing it compares against is real. This command writes that reference into the feature's `spec.md` so `__SPECKIT_COMMAND_MODES_IMPLEMENT__` can use it.

## User Input

```text
$ARGUMENTS
```

If the user supplied a reference (URL, product, repo, named piece), use it. Otherwise propose bars as described below.

## Steps

1. **Find the feature.** Run `.specify/scripts/bash/check-prerequisites.sh --json --paths-only` (on Windows: `.specify/scripts/powershell/check-prerequisites.ps1 -Json -PathsOnly`) from the repo root and read `FEATURE_SPEC`. If it fails, tell the user to run `__SPECKIT_COMMAND_SPECIFY__` first and stop.

2. **Read `spec.md`** in full, including any existing `## Quality Bar` section.

3. **Choose the bar.** A bar must pass all three tests:
   - **Named**: a specific thing, not a category. "Stripe's pricing page" works; "award-winning SaaS sites" does not.
   - **Fetchable**: the critic can actually obtain it in this environment (screenshot the live page, open the repo, run the binary, read the published piece). If it cannot, it will hallucinate the comparison.
   - **Comparable**: ours and the bar can sit side by side and a judge can pick one.

   Bars that work by goal type:

   | Goal | Bar |
   |---|---|
   | Website, app, UI | Live site of a specific best-in-class product, screenshotted at the same viewport |
   | Game, 3D, visual | Screenshots or footage from a named shipped title |
   | Writing, docs | A specific published piece, same length and format |
   | Code, tooling, API | A named repo's implementation plus its benchmark or test suite |
   | Research, analysis | A named report or paper, judged on rigour and coverage |

   If the user gave no reference, offer **2 or 3 candidate bars**, one line each, prefer the hardest one the agent can genuinely reach, and **wait for the user's pick** before writing anything. In automated mode, pick the hardest fetchable one and say so.

   If the user does not yet know what good looks like, interview them instead of guessing: ask one specific question at a time (who uses this, what they compare it to today, what would make them give up on it) until you could name a bar yourself, then offer the candidates.

   If the feature has a measurable half (load time, p95 latency, bundle size, benchmark score, pass rate), name a target next to the reference. Taste plus a number beats taste alone.

4. **Write the section** into `spec.md`, replacing any existing `## Quality Bar` section, placed after the Success Criteria section (or at the end if there is none):

   ```markdown
   ## Quality Bar

   - **Reference**: <named thing> — <URL / repo / how to obtain it>
   - **How the critic gets it**: <screenshot at 1440px and 390px / clone and run X / read the article>
   - **Comparison question**: <the single A/B question the critic answers, e.g. "Which hero would a first-time visitor trust more?">
   - **Measurable target**: <metric and threshold, or "none">
   - **Per-area bars** (optional): <area>: <reference>
   - **Done checks**:
     - <yes/no question someone could verify in ten seconds>
     - <...three to six in total>
   ```

   Done checks are the pass/fail gate every candidate must clear before it is compared with the bar. Write them as yes/no questions with a checkable answer ("Is every price on the page the same as in pricing.json?", "Does Lighthouse performance score 90 or more on mobile?"). Avoid vague lines like "Is it polished?"; nobody can verify those quickly, so the agent ends up grading itself.

5. **Report** the bar you wrote, and suggest `__SPECKIT_COMMAND_MODES_TAG__` after tasks are generated.

## Done When

- [ ] `spec.md` contains exactly one `## Quality Bar` section with a named, fetchable, comparable reference and 3–6 done checks
- [ ] The user picked the bar, or (automated mode) the choice was stated
