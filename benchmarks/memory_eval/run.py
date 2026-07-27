"""CLI entrypoint for the memory eval harness.

Examples
--------
Offline smoke (CI-safe, deterministic, zero LLM spend) — all fixtures, both baselines:
    python run.py --mode fixture --benchmark all --provider all

Full run on the real corpora with the real LLM judge (gated: needs the dev venv,
network to download, and gpt-5.4/5.5 credentials in .env):
    python run.py --mode full --benchmark longmemeval --provider recency_decay --judge llm

Writes results/<UTC>_<mode>.json (machine payload) + results/summary_<UTC>.md
(human view), and applies the CI gate (harness/gate.py).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness.baselines import PROVIDERS, get_answerer, get_provider  # noqa: E402
from harness.datasets import get_loader, list_benchmarks  # noqa: E402
from harness.gate import check_all  # noqa: E402
from harness.judge import StubJudge  # noqa: E402
from harness.provider import RunConfig  # noqa: E402
from harness.report import render_markdown  # noqa: E402
from harness.runner import run_benchmark  # noqa: E402

_RESULTS = Path(__file__).resolve().parent / "results"


def _load(name: str, mode: str):
    spec = get_loader(name)
    if mode == "fixture":
        return spec.fixture()
    if spec.download is None or spec.build is None:
        raise SystemExit(f"benchmark {name!r} has no downloader; use --mode fixture")
    raw = spec.download()
    return spec.build(raw)


def _build_judge(kind: str):
    if kind == "stub":
        return StubJudge()
    if kind == "llm":
        from adapters.egpt_suite import LLMJudge  # lazy: needs egpt + credentials

        return LLMJudge()
    raise SystemExit(f"unknown judge {kind!r}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Memory eval harness (LongMemEval/LOCOMO/DMR/custom)")
    ap.add_argument("--mode", choices=["fixture", "full"], default="fixture")
    ap.add_argument("--benchmark", default="all", help="'all' or one of: " + ", ".join(list_benchmarks()))
    ap.add_argument("--provider", default="all", help="'all' or one of: " + ", ".join(sorted(PROVIDERS)))
    ap.add_argument("--judge", choices=["stub", "llm"], default="stub")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--no-gate", action="store_true", help="skip the CI pass/fail gate")
    ap.add_argument("--tag", default="", help="optional label in the output filename")
    args = ap.parse_args(argv)

    benches = list_benchmarks() if args.benchmark == "all" else [args.benchmark]
    providers = sorted(PROVIDERS) if args.provider == "all" else [args.provider]

    judge = _build_judge(args.judge)
    answerer = get_answerer("stub")  # LLM answerer path lands with LLMJudge; stub keeps CI offline
    cfg = RunConfig(k=args.k)

    reports: List[dict] = []
    for bname in benches:
        bench = _load(bname, args.mode)
        bench.validate()
        for pname in providers:
            rep = run_benchmark(bench, get_provider(pname), judge, answerer, cfg)
            reports.append(rep)
            print(f"ran {bname}/{pname}: QA={rep['qa_accuracy']['value']:.2f}")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tag = f"_{args.tag}" if args.tag else ""
    _RESULTS.mkdir(exist_ok=True)
    json_path = _RESULTS / f"results_{stamp}_{args.mode}{tag}.json"
    md_path = _RESULTS / f"summary_{stamp}_{args.mode}{tag}.md"
    json_path.write_text(json.dumps({"mode": args.mode, "generated_utc": stamp, "reports": reports}, indent=2) + "\n")
    md_path.write_text(
        render_markdown(
            reports,
            title="Memory eval — run summary",
            generated_utc=stamp,
            mode=args.mode,
        )
    )
    print(f"\nwrote {json_path.relative_to(_RESULTS.parent)}")
    print(f"wrote {md_path.relative_to(_RESULTS.parent)}")

    if not args.no_gate:
        passed, failures = check_all(reports)
        if failures:
            print("\nGATE FAILURES:")
            for f in failures:
                print(f"  - {f}")
        else:
            print("\nGATE: PASS")
        return 0 if passed else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
