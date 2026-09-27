#!/usr/bin/env python3
"""Drive the no-ai-slop skill from the working tree through a headless `claude -p`.

Subcommands:
  check                 build + validate the plugin zip (scripts/build_plugin.py --check)
  lint FILE             deterministic slop scan of any text (word lists parsed from SKILL.md)
  edit FILE             run the skill in edit mode, save output, lint the edited draft
  detect FILE           run the skill in detect mode ("is this slop?"), sanity-check the report
  trigger FILE          natural-language ask (no slash command): did Claude auto-load the skill?
  negative              factual question with no draft: the skill must NOT load

Exit codes: 0 ok, 1 error (claude failed / build failed / skill did the wrong thing),
2 lint findings in the text that should be clean.
Outputs land in dist/runs/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILL_MD = ROOT / "skills" / "no-ai-slop" / "SKILL.md"
RUNS = ROOT / "dist" / "runs"

# Regexes for patterns SKILL.md names but that are not plain word lists.
PATTERNS = {
    "binary contrast": r"\b(?:isn't|is not|not just|not only)\b[^.\n]{0,80}[.,;]\s*(?:it's|it is|but|they're)\b",
    "throat-clearing opener": r"\b(?:here's the thing|here's what i mean|let me be clear|i'll be honest|the uncomfortable truth)\b",
    "faux-insight setup": r"\b(?:nobody tells you|no one tells you|most people (?:skip|get wrong|miss)|everyone misses)\b",
    "colon reveal": r"(?m)(?:^|[.!?]\s+)([A-Z][^.:\n]{2,50}: [a-z]\w*)",
    "superficial -ing analysis": r",\s*(?:highlighting|underscoring|reflecting|showcasing)\b",
    "importance puffery": r"\b(?:testament to|pivotal moment|plays? a vital role|solidif(?:y|ies) its position|underscores? its significance)\b",
    "weasel attribution": r"\b(?:experts agree|studies show|many argue|widely regarded|industry reports suggest)\b",
    "interpretive metadiscourse": r"\b(?:the key point is|as you can see|this distinction matters|matters more than it sounds)\b",
    "rhetorical setup": r"\b(?:what if i told you|think about it:|plot twist:)",
    "summary-recap ending": r"(?m)^(?:in conclusion|ultimately|overall),",
}


def skill_list(label: str) -> list[str]:
    """Pull a comma-separated list from a 'Label: a, b, c.' line in SKILL.md."""
    text = SKILL_MD.read_text(encoding="utf-8")
    m = re.search(rf"^{re.escape(label)}:\s*(.+)$", text, re.M)
    if not m:
        sys.exit(f"driver: could not find '{label}:' line in {SKILL_MD.relative_to(ROOT)}")
    first_sentence = m.group(1).split(". ")[0].rstrip(".")
    return [w.strip() for w in first_sentence.split(",") if w.strip()]


def lint_text(text: str) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    lower = text.lower()
    for label, kind in (("Banned outright", "banned word"), ("Often-empty phrases", "empty phrase")):
        for w in skill_list(label):
            if re.search(rf"\b{re.escape(w.lower())}\b", lower):
                findings.append((kind, w))
    for kind, rx in PATTERNS.items():
        for m in re.finditer(rx, text, re.I if kind != "colon reveal" else 0):
            findings.append((kind, (m.group(m.lastindex or 0)).strip()))
    dashes = text.count("—") + len(re.findall(r"\s--\s", text))
    words = len(text.split())
    if (words < 300 and dashes > 0) or dashes > 2:
        findings.append(("em dashes", f"{dashes} in {words} words (rule: none in short copy, 1-2 in long)"))
    return findings


def print_findings(findings: list[tuple[str, str]], title: str) -> None:
    print(f"== LINT {title}: {len(findings)} finding(s)")
    for kind, snippet in findings:
        print(f"  - {kind}: {snippet!r}")


def split_edit(output: str) -> tuple[str, str]:
    """Return (edited draft, what-changed section)."""
    m = re.search(r"(?im)^\W*what changed\W*$", output)
    if not m:
        return output, ""
    return output[: m.start()], output[m.start():]


def skill_id(plugin: str) -> str:
    """`<plugin>:no-ai-slop`. The repo has no .claude-plugin/plugin.json, so Claude Code names
    the plugin after the directory basename or the zip stem, e.g. `no-ai-slop-plugin-1.0.6`."""
    p = Path(plugin).resolve()
    return f"{p.stem if p.suffix == '.zip' else p.name}:no-ai-slop"


def run_claude(prompt: str, args: argparse.Namespace, tools: str) -> dict:
    cmd = [
        "claude", "-p", prompt,
        "--plugin-dir", str(Path(args.plugin).resolve()),  # child runs in a temp cwd
        "--tools", tools,
        "--no-session-persistence",
        "--model", args.model,
        "--output-format", "stream-json", "--verbose",
    ]
    # Run from an empty dir so no project skills/CLAUDE.md leak in (this repo's own
    # .claude/skills/run-no-ai-slop would otherwise load too).
    with tempfile.TemporaryDirectory() as cwd:
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=args.timeout)
    tool_uses, result, loaded = [], None, None
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("subtype") == "init":
            loaded = ev.get("skills", [])
        elif ev.get("type") == "assistant":
            tool_uses += [c for c in ev["message"]["content"] if c.get("type") == "tool_use"]
        elif ev.get("type") == "result":
            result = ev
    if proc.returncode != 0 or not result or result.get("subtype") != "success":
        sys.stderr.write(proc.stderr[-2000:] + proc.stdout[-2000:])
        sys.exit(f"driver: claude failed (exit {proc.returncode})")
    # An unknown /slash-command is NOT an error in `claude -p`: it is sent as plain text and
    # the model answers without the skill. Fail loudly instead of grading a skill-less answer.
    if args.skill not in (loaded or []):
        sys.exit(f"driver: skill {args.skill!r} not loaded; skills matching 'slop' = {[x for x in loaded or [] if 'slop' in x]}")
    return {"text": result["result"], "tool_uses": tool_uses,
            "cost": result.get("total_cost_usd"), "secs": round(time.time() - t0, 1)}


def save(mode: str, text: str) -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"{time.strftime('%Y%m%d-%H%M%S')}-{mode}.md"
    out.write_text(text, encoding="utf-8")
    return out


def report(mode: str, run: dict) -> Path:
    out = save(mode, run["text"])
    print(run["text"])
    print(f"\n== {mode}: {run['secs']}s, ${run['cost']}, saved {out.relative_to(ROOT)}")
    return out


def read_input(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def cmd_check(args: argparse.Namespace) -> int:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "build_plugin.py"), "--check"]).returncode


def cmd_lint(args: argparse.Namespace) -> int:
    findings = lint_text(read_input(args.file))
    print_findings(findings, args.file)
    return 2 if findings else 0


def cmd_edit(args: argparse.Namespace) -> int:
    run = run_claude(f"/{args.skill} {read_input(args.file)}", args, tools="")
    report("edit", run)
    draft, changed = split_edit(run["text"])
    if not changed:
        print("== FAIL: no 'What changed' section in the output")
        return 1
    findings = lint_text(draft)
    print_findings(findings, "edited draft")
    return 2 if findings else 0


def cmd_detect(args: argparse.Namespace) -> int:
    run = run_claude(f"/{args.skill} is this slop? {read_input(args.file)}", args, tools="")
    report("detect", run)
    text, problems = run["text"], []
    if re.search(r"(?im)^\W*what changed\W*$", text):
        problems.append("output has a 'What changed' section (detect must not rewrite)")
    if not re.search(r"[\"“].{4,}?[\"”]", text):
        problems.append("no quoted lines (detect must quote each pattern it found)")
    if re.search(r"\b(?:\d{1,3}\s*%|\d+\s*/\s*10)\b.{0,30}\b(?:ai|slop|likely)\b", text, re.I):
        problems.append("looks like it scored the draft / guessed authorship")
    for p in problems:
        print(f"== FAIL: {p}")
    return 1 if problems else 0


def skill_calls(run: dict) -> list[str]:
    return [t["input"].get("skill", "") for t in run["tool_uses"] if t["name"] == "Skill"]


def cmd_trigger(args: argparse.Namespace) -> int:
    run = run_claude(f"Can you make this less AI-sounding? {read_input(args.file)}", args, tools="Skill")
    report("trigger", run)
    calls = skill_calls(run)
    print(f"== Skill tool calls: {calls or 'none'}")
    return 0 if args.skill in calls else 1


def cmd_negative(args: argparse.Namespace) -> int:
    run = run_claude("What year did the Apollo 11 mission land on the Moon? One sentence.", args, tools="Skill")
    report("negative", run)
    calls = skill_calls(run)
    print(f"== Skill tool calls: {calls or 'none'}")
    return 1 if args.skill in calls else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=os.environ.get("NO_AI_SLOP_MODEL", "sonnet"))
    ap.add_argument("--plugin", default=str(ROOT),
                    help="plugin dir or built .zip to load (default: repo root = working tree)")
    ap.add_argument("--timeout", type=int, default=300)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    for name in ("lint", "edit", "detect", "trigger"):
        sub.add_parser(name).add_argument("file", help="text file, or - for stdin")
    sub.add_parser("negative")
    args = ap.parse_args()
    args.skill = skill_id(args.plugin)
    return globals()[f"cmd_{args.cmd}"](args)


if __name__ == "__main__":
    sys.exit(main())
