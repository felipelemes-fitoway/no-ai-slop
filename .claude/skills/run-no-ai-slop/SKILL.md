---
name: run-no-ai-slop
description: Run, test, try out, or eval the no-ai-slop writing skill from this repo's working tree - edit a draft, detect slop, check that the skill auto-triggers (and stays quiet when it should), lint text for slop patterns, and build/validate the plugin zip. Use after changing skills/no-ai-slop/SKILL.md or eval.md, or when asked to run, check, build, or package this repo.
---

# Run no-ai-slop

This repo has no app: the product is a prompt skill (`skills/no-ai-slop/SKILL.md` +
`eval.md`) plus a Python packager (`scripts/build_plugin.py`). Almost every PR edits the
skill text, so "running it" means loading the skill **from the working tree** into a
headless `claude -p` and looking at what it writes. `driver.py` does that and adds a
deterministic slop lint on top.

All paths below are relative to the repo root.

## Prerequisites

`python3` (3.11 works; CI uses 3.12) and the `claude` CLI, logged in. Nothing to install.
Optional: `node`/`npx` to test the `npx skills` install path.

## Agent path: `driver.py`

```bash
D=.claude/skills/run-no-ai-slop/driver.py
F=.claude/skills/run-no-ai-slop/fixtures/slop-post.md   # 115-word draft with ~10 planted patterns

python3 $D check          # build + validate plugin zip, same as CI     (~1s)
python3 $D lint $F        # deterministic scan, no model; exit 2 = findings
python3 $D edit $F        # /no-ai-slop edit mode -> output + lint of the edited draft  (~20s)
python3 $D detect $F      # "is this slop?" mode -> must quote lines, not rewrite      (~10s)
python3 $D trigger $F     # plain "make this less AI-sounding" ask -> must call the Skill tool
python3 $D negative       # factual question, no draft -> must NOT call the skill
```

Options go **before** the subcommand:

```bash
python3 $D --model claude-haiku-4-5-20251001 detect $F        # cheaper/faster; default is `sonnet` (or $NO_AI_SLOP_MODEL)
python3 scripts/build_plugin.py && \
  python3 $D --plugin dist/no-ai-slop-plugin-1.0.6.zip detect $F   # test the packaged zip, not the tree
```

Any file works as input, or `-` for stdin. Each model run prints the full answer, time, cost,
and saves it to `dist/runs/<timestamp>-<mode>.md` (gitignored). Runs are independent, so
`edit`, `detect`, `trigger`, and `negative` can run in parallel.

Exit codes: `0` ok, `1` wrong behavior (no "What changed" in edit, detect rewrote or quoted
nothing, trigger didn't load the skill, negative did, skill failed to load, claude errored),
`2` the lint found slop in text that should be clean.

To A/B a SKILL.md change: run `edit` on the same input before and after, diff the two files
in `dist/runs/`. Output varies run to run; look at patterns across 2-3 runs, not one diff.

## How the driver loads the skill (for hand-rolled runs)

```bash
R=$PWD; cd "$(mktemp -d)" && claude -p "/no-ai-slop:no-ai-slop is this slop? $(cat "$R/.claude/skills/run-no-ai-slop/fixtures/slop-post.md")" \
  --plugin-dir "$R" --tools "" --no-session-persistence < /dev/null; cd "$R"
```

`--plugin-dir <repo root>` loads `skills/` as a session-only plugin; no install, edits are
live. `--tools ""` for slash-command runs (the skill needs no tools); `--tools Skill` when
testing auto-triggering.

## Human path

Install it as a user would (README): `npx skills add petergyang/no-ai-slop --skill no-ai-slop --global --yes`,
then type `/no-ai-slop <draft>` in any agent. To try the local tree instead, isolate HOME:

```bash
R=$PWD; H=$(mktemp -d); (cd "$H" && HOME="$H" npx -y skills add "$R" --skill no-ai-slop --global --yes)
ls -la "$H/.claude/skills/"   # no-ai-slop -> ../../.agents/skills/no-ai-slop
```

## Gotchas

- **The slash name depends on the folder name.** There is no `.claude-plugin/plugin.json`
  (only `.codex-plugin/`), so Claude Code names the plugin after the directory or zip stem:
  repo root -> `/no-ai-slop:no-ai-slop`, a clone in `~/work/repo` -> `/repo:no-ai-slop`,
  the built zip -> `/no-ai-slop-plugin-1.0.6:no-ai-slop`. The driver derives it the same way.
- **A wrong slash command fails silently.** `claude -p "/typo:no-ai-slop ..."` does not
  error; the text goes to the model, which answers without the skill (and a detect run
  still looks plausible). The driver reads the `init` event's `skills` list and exits 1
  if the skill isn't there.
- **Relative `--plugin-dir` breaks when cwd changes.** The driver runs claude in a temp dir
  (so this repo's own `.claude/skills` don't load too) and resolves `--plugin` to an
  absolute path first. Hand-rolled runs from another cwd need an absolute path.
- **`claude -p` without stdin waits 3s** and prints "no stdin data received". Add `< /dev/null`.
- **The em-dash rule vs. "preserve voice".** The fixture's line "I was skeptical at first -
  I thought..." has one em dash. SKILL.md says none in short copy, but also says keep the
  writer's voice. Sonnet goes both ways: some runs keep the dash (citing voice), others split
  the sentence. When it keeps it, `edit` exits 2 with a single `em dashes` finding. That's the rules conflicting, not a driver bug; read the
  finding before "fixing" anything.
- **The lint is a floor, not the eval.** It catches word lists (parsed live from the
  `Banned outright:` and `Often-empty phrases:` lines in SKILL.md, so renaming those labels
  breaks it) and regex-shaped patterns. It can't judge voice, invented claims, synonym
  cycling, or robotic rhythm; read the output against `eval.md` for those.
- **`build_plugin.py` packages an explicit file list.** A new file under
  `skills/no-ai-slop/` (e.g. the existing `agents/openai.yaml`) is left out of the zip and
  `check` still passes; add it to both `build_plugin()` and `validate_build()`'s `expected` set.
- **`npx skills add --global` writes to ~50 agent dirs** under `$HOME` (`~/.agents`,
  `~/.claude/skills`, `~/.codex`, ...). Always override `HOME` when testing it. Two targets
  (Eve, PromptScript) report "does not support global skill installation"; that's expected
  and the command still exits 0. It needs network (it fetches the `skills` npm package):
  under `env -i` with the proxy variables stripped it exited 1.

## Troubleshooting

- `driver: skill 'X:no-ai-slop' not loaded; skills matching 'slop' = []` -> `--plugin`
  points at a dir without `skills/no-ai-slop/SKILL.md`, or a path that doesn't exist.
- `driver: could not find 'Banned outright:' line` -> SKILL.md's word-list line was
  renamed; update `skill_list()` callers in `driver.py`.
- `Missing package source: skills/no-ai-slop/eval.md` from `check` -> a file the packager
  copies by name was moved or renamed; update the paths in `scripts/build_plugin.py`.
