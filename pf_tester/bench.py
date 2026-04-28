"""Throughput / latency micro-benchmark for openai/privacy-filter.

Run after the model has been pulled at least once, e.g.:

    python -m pf_tester.bench --runs 5
    python -m pf_tester.bench --device cpu --runs 3 --batch-size 4
"""

from __future__ import annotations

import argparse
import statistics
import time

from .filter import DEFAULT_MODEL, PrivacyFilter
from .samples import SAMPLES


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1)))))
    return ordered[k]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pf_tester.bench")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--device", default=None, help="cpu / cuda / cuda:0")
    p.add_argument("--runs", type=int, default=3, help="passes over the sample suite")
    p.add_argument("--batch-size", type=int, default=1, help="0/1 = run one-by-one")
    p.add_argument("--num-threads", type=int, default=None)
    p.add_argument("--warmup", type=int, default=1)
    args = p.parse_args(argv)

    print(f"Loading model {args.model} on {args.device or 'auto'}…")
    pf = PrivacyFilter(model_name=args.model, device=args.device, num_threads=args.num_threads)

    texts = list(SAMPLES.values())
    total_chars = sum(len(t) for t in texts)
    print(f"Sample suite: {len(texts)} docs, {total_chars} chars total.")

    for _ in range(max(0, args.warmup)):
        pf.detect(texts[0])

    per_doc_latencies: list[float] = []
    per_run_throughput: list[float] = []
    span_total = 0

    for run_idx in range(1, args.runs + 1):
        t0 = time.perf_counter()
        if args.batch_size > 1:
            results = pf.detect_batch(texts, batch_size=args.batch_size)
            span_total += sum(len(r) for r in results)
            elapsed = time.perf_counter() - t0
        else:
            elapsed = 0.0
            for t in texts:
                ts = time.perf_counter()
                spans = pf.detect(t)
                d = time.perf_counter() - ts
                per_doc_latencies.append(d * 1000)
                span_total += len(spans)
                elapsed += d

        per_run_throughput.append(total_chars / max(elapsed, 1e-9))
        print(f"  run {run_idx}: {elapsed * 1000:.1f} ms total, "
              f"{total_chars / max(elapsed, 1e-9):.0f} chars/s")

    print()
    print(f"Spans detected (total over runs): {span_total}")
    print(f"Throughput mean: {statistics.mean(per_run_throughput):.0f} chars/s")
    if per_doc_latencies:
        print(
            "Per-doc latency: "
            f"p50={_percentile(per_doc_latencies, 50):.1f} ms, "
            f"p90={_percentile(per_doc_latencies, 90):.1f} ms, "
            f"p99={_percentile(per_doc_latencies, 99):.1f} ms, "
            f"mean={statistics.mean(per_doc_latencies):.1f} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
