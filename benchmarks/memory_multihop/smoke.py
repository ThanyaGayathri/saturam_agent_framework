"""Phase-0 smoke test: prove the skeleton runs before spending any budget.

Checks (no paid calls):
  1. eval set loads and is internally consistent (every gold title resolves).
  2. metrics module computes on a toy example.
  3. LLM + embedder backends *construct* from config (or report that .env is
     missing) — without issuing a request.

Run:
    PYTHONPATH=benchmarks/memory_multihop .venvs/multihop/bin/python smoke.py
"""

from __future__ import annotations

from pathlib import Path

from harness import metrics
from harness.dataset import load_eval_set, summarize
from harness.llm import load_env

HERE = Path(__file__).resolve().parent


def main() -> None:
    load_env()

    es = load_eval_set(HERE / "data" / "eval_set.json")
    titles = {p.title for p in es.passages}
    missing = [g for it in es.items for g in it.gold_titles if g not in titles]
    assert not missing, f"gold titles missing from corpus: {missing[:3]}"
    print("[1] eval set OK:", summarize(es))

    r = metrics.recall_at_k(["A", "B", "C"], ["B", "Z"], k=2)
    f1 = metrics.token_f1("26 April 1872", "26 april 1872")
    assert 0.49 < r < 0.51 and f1 == 1.0, (r, f1)
    print("[2] metrics OK: recall@2=%.2f token_f1=%.2f" % (r, f1))

    import os

    from harness.embeddings import resolve_embedder_spec

    have_azure = bool(os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"))
    have_openai = bool(os.getenv("OPENAI_API_KEY"))
    # SM-R4 Mode A (bge-base via sentence-transformers) needs no credentials, so a
    # credential check is the wrong readiness test for embeddings — ask the spec.
    spec = resolve_embedder_spec()
    embed_ready = spec.backend == "local" or bool(
        os.getenv("AZURE_EMBEDDER_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    )
    print(
        "[3] llm creds=%s (azure=%s openai=%s) | embedder=%s ready=%s"
        % (have_azure or have_openai, have_azure, have_openai, spec.tag, embed_ready)
    )
    if not (have_azure or have_openai):
        print("    -> LLM (OpenIE + reader) needs .env; the dense leg runs credential-free:")
        print("       PYTHONPATH=. python run.py --methods vector --retrieval-only")
    print("SMOKE OK")


if __name__ == "__main__":
    main()
