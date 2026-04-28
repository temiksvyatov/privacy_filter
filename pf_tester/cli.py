"""CLI for testing OpenAI Privacy Filter on raw strings, files or stdin.

Examples:

    python -m pf_tester.cli "Alice was born on 1990-01-02 and lives in Berlin."
    python -m pf_tester.cli -f notes.txt
    cat notes.txt | python -m pf_tester.cli
    python -m pf_tester.cli --suite        # run built-in test fixtures
    python -m pf_tester.cli --json "..."   # machine-readable output
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable

from rich.console import Console
from rich.table import Table

from .filter import DEFAULT_MODEL, PrivacyFilter, Span
from .samples import SAMPLES

console = Console()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="pf_tester",
        description="Test harness for the openai/privacy-filter model.",
    )
    p.add_argument("text", nargs="?", help="Inline text to scan (omit to read stdin).")
    p.add_argument("-f", "--file", help="Read input from a file instead.")
    p.add_argument("--model", default=DEFAULT_MODEL, help="HuggingFace model id.")
    p.add_argument("--device", default=None, help="`cpu`, `cuda`, `cuda:0`, or an int.")
    p.add_argument("--placeholder", default=None, help="Override the redaction placeholder.")
    p.add_argument(
        "--mask-char",
        default=None,
        help="Replace every PII char with this single character (preserves length).",
    )
    p.add_argument(
        "--stars",
        action="store_true",
        help="Shortcut for --mask-char '*'.",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of pretty output.")
    p.add_argument("--suite", action="store_true", help="Run the built-in PII sample suite.")
    args = p.parse_args(argv)
    if args.stars:
        if args.mask_char and args.mask_char != "*":
            p.error("--stars conflicts with --mask-char")
        args.mask_char = "*"
    return args


def _read_input(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            return fh.read()
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("No input. Pass text, use -f, or pipe via stdin.")


def _render_pretty(text: str, spans: Iterable[Span], redacted: str) -> None:
    spans = list(spans)
    table = Table(title=f"Detected spans ({len(spans)})", show_lines=False)
    table.add_column("Entity", style="cyan", no_wrap=True)
    table.add_column("Text", style="magenta")
    table.add_column("Range", style="green")
    table.add_column("Score", style="yellow", justify="right")
    for s in spans:
        table.add_row(s.entity, s.text.strip(), f"{s.start}-{s.end}", f"{s.score:.4f}")

    console.rule("[bold]Input")
    console.print(text)
    console.rule("[bold]Detections")
    console.print(table if spans else "[dim]No PII detected.[/dim]")
    console.rule("[bold]Redacted")
    console.print(redacted)


def _run_one(
    pf: PrivacyFilter,
    text: str,
    placeholder: str | None,
    mask_char: str | None,
    as_json: bool,
) -> None:
    spans = pf.detect(text)
    redacted = pf.redact(text, placeholder=placeholder, spans=spans, mask_char=mask_char)
    if as_json:
        print(json.dumps(
            {
                "input": text,
                "redacted": redacted,
                "spans": [s.to_dict() for s in spans],
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        _render_pretty(text, spans, redacted)


def _run_suite(
    pf: PrivacyFilter,
    placeholder: str | None,
    mask_char: str | None,
    as_json: bool,
) -> None:
    results = []
    for name, text in SAMPLES.items():
        spans = pf.detect(text)
        redacted = pf.redact(text, placeholder=placeholder, spans=spans, mask_char=mask_char)
        if as_json:
            results.append({
                "name": name,
                "input": text,
                "redacted": redacted,
                "spans": [s.to_dict() for s in spans],
            })
        else:
            console.rule(f"[bold blue]{name}")
            _render_pretty(text, spans, redacted)
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    pf = PrivacyFilter(model_name=args.model, device=args.device)

    if args.suite:
        _run_suite(pf, args.placeholder, args.mask_char, args.json)
        return 0

    text = _read_input(args)
    _run_one(pf, text, args.placeholder, args.mask_char, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
