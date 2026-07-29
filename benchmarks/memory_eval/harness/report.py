"""Render metric reports to a markdown summary (mirrors SM-R3 results/summary_*.md).

Pure formatting: takes the list of report dicts ``aggregate``/``run_benchmark``
produce and emits a table + notes. Kept separate so the JSON payload stays the
machine contract and the markdown is a human view of the same numbers.
"""

from __future__ import annotations

from typing import List, Optional


def _fmt_opt(d: Optional[dict], key: str = "value", pct: bool = True) -> str:
    if not d or d.get(key) is None:
        return "-"
    v = d[key]
    return f"{v * 100:.1f}%" if pct else f"{v:.3f}"


def _fmt_float(v: Optional[float], suffix: str = "") -> str:
    return "-" if v is None else f"{v:.2f}{suffix}"


def render_markdown(reports: List[dict], *, title: str, generated_utc: str, mode: str) -> str:
    lines: List[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"- Generated (UTC): {generated_utc}")
    lines.append(f"- Mode: {mode}")
    lines.append(f"- Judge: {reports[0]['judge'] if reports else '-'}")
    lines.append("")
    if mode == "fixture":
        lines.append(
            "> NOTE: fixture mode runs the tiny committed smoke set. Numbers prove the "
            "harness computes end-to-end and that metrics discriminate providers; they "
            "are NOT for ranking real systems. Use `--mode full` on the real corpora for that."
        )
        lines.append("")

    header = (
        "| benchmark | provider | k | QA | temporal | recall@k | nDCG@k | bitemporal | as_of_leak | "
        "decay(supp/outr) | ingest p95 | query p95 | cost |"
    )
    sep = "| " + " | ".join(["---"] * 13) + " |"
    lines.append(header)
    lines.append(sep)

    for r in reports:
        decay = r.get("decay")
        decay_s = "-" if not decay else f"{decay['suppression_at_k']:.2f}/{decay['outranked']:.2f}"
        cost = r.get("cost")
        cost_s = "-" if not cost else f"${cost['cost_usd']:.4f}"
        row = [
            r.get("benchmark", "-"),
            r.get("provider", "-"),
            str(r.get("k", "-")),
            _fmt_opt(r.get("qa_accuracy")),
            _fmt_opt(r.get("temporal_accuracy")),
            _fmt_opt(r["retrieval"].get("recall_at_k")),
            _fmt_opt(r["retrieval"].get("ndcg_at_k"), pct=False),
            _fmt_opt(r["bitemporal"].get("accuracy")),
            _fmt_float(r["bitemporal"].get("as_of_leak_rate")),
            decay_s,
            _fmt_float(r["latency_ms"]["ingest"]["p95"], "ms"),
            _fmt_float(r["latency_ms"]["query"]["p95"], "ms"),
            cost_s,
        ]
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append(
        "Columns: QA = LLM-judged answer correctness; temporal = QA over "
        "{temporal, knowledge_update, as_of_t}; recall@k / nDCG@k over probes with gold "
        "evidence; bitemporal = QA over {as_of_t, knowledge_update}; as_of_leak = fraction of "
        "as-of probes whose top-1 retrieval is NOT valid at T (two-sided: future OR "
        "stale-superseded; SM-R10 t_valid<=T<t_invalid; lower better); "
        "decay supp/outr = stale-target suppression@k / gold-outranks-stale (higher better)."
    )
    lines.append("")
    return "\n".join(lines)
