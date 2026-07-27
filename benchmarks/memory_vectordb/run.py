"""CLI entrypoint for the SM-R3 vector-store benchmark (v2, real data + per-tenant scale).

Run from the repo root, e.g.:

    python -m benchmarks.memory_vectordb.run \
        --backends pgvector qdrant milvus \
        --per-tenant 100000 --tenants 10 \
        --tenancy filter collection partition \
        --queries 1000 --k 10 --ef 64 --repeats 3 --seed 7

Scale is expressed PER TENANT (total = per_tenant x tenants). Needs the GloVe ANN dataset
(see README); path via MEMBENCH_ANN_HDF5. Results land in results/.
"""

from __future__ import annotations

import argparse
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .harness import run_case
from .realdata import load_train_vectors

RESULTS_DIR = Path(__file__).parent / "results"

_COLUMNS = [
    ("backend", "backend"),
    ("total_vectors", "total"),
    ("per_tenant", "per-tenant"),
    ("num_tenants", "tenants"),
    ("tenancy", "tenancy"),
    ("recall_at_k", "recall@k"),
    ("p95_ms", "p95 ms"),
    ("p95_per_repeat_ms", "p95/rep"),
    ("cross_tenant_leaks", "leaks"),
    ("insert_qps", "insert q/s"),
]


def _table(rows: List[Dict]) -> str:
    head = "| " + " | ".join(lbl for _, lbl in _COLUMNS) + " |"
    sep = "| " + " | ".join("---" for _ in _COLUMNS) + " |"
    lines = [head, sep]
    for r in rows:
        if r.get("error"):
            cells = [
                str(r.get("backend", "?")),
                "",
                str(r.get("per_tenant", "")),
                str(r.get("num_tenants", "")),
                str(r.get("tenancy", "")),
            ]
            cells += ["ERROR"] * (len(_COLUMNS) - len(cells))
        else:
            cells = [str(r.get(key, "")) for key, _ in _COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="SM-R3 memory vector-store benchmark (v2)")
    ap.add_argument("--backends", nargs="+", default=["pgvector"])
    ap.add_argument(
        "--per-tenant", type=int, default=100_000, help="vectors per tenant"
    )
    ap.add_argument("--tenants", type=int, default=10)
    ap.add_argument(
        "--tenancy",
        nargs="+",
        default=["filter"],
        choices=["filter", "collection", "partition"],
    )
    ap.add_argument("--queries", type=int, default=1000)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--ef", type=int, default=64, help="matched HNSW ef_search")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    total_needed = args.per_tenant * args.tenants
    print(f"Loading ANN dataset (need >= {total_needed} vectors)...", flush=True)
    train = load_train_vectors(limit=total_needed)
    print(f"Loaded train {train.shape}", flush=True)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: List[Dict] = []

    for backend in args.backends:
        for tenancy in args.tenancy:
            if tenancy == "partition" and backend != "pgvector":
                continue
            label = f"{backend} {args.per_tenant}x{args.tenants} {tenancy}"
            print(f"\n=== running: {label} ===", flush=True)
            try:
                m = run_case(
                    backend=backend,
                    per_tenant=args.per_tenant,
                    num_tenants=args.tenants,
                    tenancy=tenancy,
                    train=train,
                    k=args.k,
                    num_queries=args.queries,
                    ef_search=args.ef,
                    seed=args.seed,
                    repeats=args.repeats,
                )
                print(json.dumps(m, indent=2), flush=True)
                rows.append(m)
            except Exception as e:  # noqa: BLE001 — a failing case must not abort the sweep
                print(f"ERROR on {label}: {e}", flush=True)
                traceback.print_exc()
                rows.append(
                    {
                        "backend": backend,
                        "per_tenant": args.per_tenant,
                        "num_tenants": args.tenants,
                        "tenancy": tenancy,
                        "error": str(e),
                    }
                )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = f"_{args.tag}" if args.tag else ""
    (RESULTS_DIR / f"results_{stamp}{tag}.json").write_text(json.dumps(rows, indent=2))
    md = f"# SM-R3 benchmark results v2 ({stamp})\n\ndataset=GloVe-100-angular, k={args.k}, ef_search={args.ef}, queries={args.queries}, repeats={args.repeats}, seed={args.seed}\n\n"
    md += _table(rows) + "\n"
    (RESULTS_DIR / f"summary_{stamp}{tag}.md").write_text(md)
    print(f"\nWrote results_{stamp}{tag}.json + summary")
    print("\n" + _table(rows))


if __name__ == "__main__":
    main()
