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

from .filter import DEFAULT_MODEL, PrivacyFilter, Span, redact as redact_text
from .ru_postpass import ru_postpass as ru_postpass_apply
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
    p.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Drop spans with confidence below this threshold (0..1).",
    )
    p.add_argument(
        "--ru-postpass",
        action="store_true",
        help="Run the Russian regex post-pass after the model.",
    )
    p.add_argument(
        "--no-model",
        action="store_true",
        help="Skip the model entirely; rely on --ru-postpass only. "
             "Useful for debugging the regex layer or quick offline checks.",
    )
    p.add_argument("--num-threads", type=int, default=None, help="torch.set_num_threads")
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


def _detect(pf: PrivacyFilter | None, text: str, args: argparse.Namespace) -> list[Span]:
    spans = [] if pf is None else pf.detect(text, min_score=args.min_score)
    if args.ru_postpass or pf is None:
        spans = ru_postpass_apply(text, spans)
    return spans


def _run_one(
    pf: PrivacyFilter | None,
    text: str,
    args: argparse.Namespace,
) -> None:
    placeholder = args.placeholder
    mask_char = args.mask_char
    as_json = args.json
    spans = _detect(pf, text, args)
    redacted = redact_text(text, spans, placeholder=placeholder, mask_char=mask_char)
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


def _run_suite(pf: PrivacyFilter | None, args: argparse.Namespace) -> None:
    placeholder = args.placeholder
    mask_char = args.mask_char
    as_json = args.json
    results = []
    for name, text in SAMPLES.items():
        spans = _detect(pf, text, args)
        redacted = redact_text(text, spans, placeholder=placeholder, mask_char=mask_char)
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
    pf: PrivacyFilter | None
    if args.no_model:
        if not args.ru_postpass:
            print("--no-model requires --ru-postpass (otherwise nothing is detected).",
                  file=sys.stderr)
            return 2
        pf = None
    else:
        pf = PrivacyFilter(
            model_name=args.model,
            device=args.device,
            num_threads=args.num_threads,
        )

    if args.suite:
        _run_suite(pf, args)
        return 0

    text = _read_input(args)
    _run_one(pf, text, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
