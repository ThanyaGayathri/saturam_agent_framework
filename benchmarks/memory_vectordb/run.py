"""CLI entrypoint for the SM-R3 vector-store benchmark.

Run from the repo root, e.g.:

    .venvs/demo/bin/python -m benchmarks.memory_vectordb.run \
        --backends pgvector qdrant milvus \
        --scales 100000 1000000 \
        --tenancy filter collection \
        --tenants 50 --queries 200 --k 10

Results are written to benchmarks/memory_vectordb/results/ as JSON (raw) and a
markdown table (summary), which the DECISION.md ADR cites.
"""

from __future__ import annotations

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .harness import run_case

RESULTS_DIR = Path(__file__).parent / "results"

_COLUMNS = [
    ("backend", "backend"),
    ("n_records", "records"),
    ("tenancy", "tenancy"),
    ("num_tenants", "tenants"),
    ("recall_at_k", "recall@k"),
    ("p50_ms", "p50 ms"),
    ("p95_ms", "p95 ms"),
    ("p99_ms", "p99 ms"),
    ("insert_qps", "insert q/s"),
]


def _markdown_table(rows: List[Dict]) -> str:
    header = "| " + " | ".join(label for _, label in _COLUMNS) + " |"
    sep = "| " + " | ".join("---" for _ in _COLUMNS) + " |"
    lines = [header, sep]
    for r in rows:
        if r.get("error"):
            cells = [
                str(r.get("backend", "?")),
                str(r.get("n_records", "?")),
                str(r.get("tenancy", "?")),
            ]
            cells += ["ERROR"] * (len(_COLUMNS) - 3)
        else:
            cells = [str(r.get(key, "")) for key, _ in _COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="SM-R3 memory vector-store benchmark")
    ap.add_argument("--backends", nargs="+", default=["pgvector"])
    ap.add_argument("--scales", nargs="+", type=int, default=[100_000])
    ap.add_argument(
        "--tenancy", nargs="+", default=["filter"], choices=["filter", "collection"]
    )
    ap.add_argument("--tenants", type=int, default=50)
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--dim", type=int, default=768)
    ap.add_argument(
        "--tag", default="", help="optional label added to output filenames"
    )
    args = ap.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: List[Dict] = []

    for backend in args.backends:
        for n in args.scales:
            for tenancy in args.tenancy:
                label = f"{backend} n={n} tenancy={tenancy}"
                print(f"\n=== running: {label} ===", flush=True)
                try:
                    m = run_case(
                        backend=backend,
                        n=n,
                        num_tenants=args.tenants,
                        tenancy=tenancy,
                        k=args.k,
                        num_queries=args.queries,
                        dim=args.dim,
                    )
                    print(json.dumps(m, indent=2), flush=True)
                    rows.append(m)
                except Exception as e:  # noqa: BLE001 — a failing backend must not abort the sweep
                    print(f"ERROR on {label}: {e}", flush=True)
                    traceback.print_exc()
                    rows.append(
                        {
                            "backend": backend,
                            "n_records": n,
                            "tenancy": tenancy,
                            "error": str(e),
                        }
                    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = f"_{args.tag}" if args.tag else ""
    json_path = RESULTS_DIR / f"results_{stamp}{tag}.json"
    md_path = RESULTS_DIR / f"summary_{stamp}{tag}.md"
    json_path.write_text(json.dumps(rows, indent=2))

    md = f"# SM-R3 benchmark results ({stamp})\n\n"
    md += f"dim={args.dim}, k={args.k}, queries={args.queries}, tenants={args.tenants}\n\n"
    md += _markdown_table(rows) + "\n"
    md_path.write_text(md)

    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    print("\n" + _markdown_table(rows))


if __name__ == "__main__":
    main()
