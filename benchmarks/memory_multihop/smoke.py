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
    # [4] Provider content-filter handling. Real 2Wiki passages trip Azure's filter
    # (3/949 measured at n=120) and an unhandled refusal killed a complete 3-method run.
    # Assert the invariant offline: filter refusals are absorbed and counted, everything
    # else still propagates. No credentials and no network needed.
    from harness import openie, reader

    class _Filtered(Exception):
        pass

    class _FakeLLM:
        def __init__(self, exc):
            self._exc = exc

        def complete(self, *a, **kw):
            raise self._exc

    cf = _Filtered(
        "Error code: 400 - The response was filtered due to the prompt triggering Azure "
        "OpenAI's content management policy."
    )
    before_o, before_r = len(openie.CONTENT_FILTERED), len(reader.READER_FAILURES)

    assert openie.extract_triples("passage", _FakeLLM(cf)) == [], "filtered passage must yield no triples"
    assert openie.extract_query_entities("q?", _FakeLLM(cf)) == [], "filtered query must yield no entities"
    assert reader.answer("q?", [], {}, _FakeLLM(cf)) == "", "filtered reader call must score as no-answer"
    assert len(openie.CONTENT_FILTERED) - before_o == 2, "both openie refusals must be recorded"
    assert len(reader.READER_FAILURES) - before_r == 1, "reader refusal must be recorded"

    # A non-filter error must still surface — the handler must not swallow real bugs.
    for fn, args in (
        (openie.extract_triples, ("p", _FakeLLM(RuntimeError("boom")))),
        (reader.answer, ("q", [], {}, _FakeLLM(RuntimeError("boom")))),
    ):
        try:
            fn(*args)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"{fn.__name__} swallowed a non-content-filter exception")

    # Leave the module counters as we found them.
    del openie.CONTENT_FILTERED[before_o:]
    del reader.READER_FAILURES[before_r:]
    print("[4] content-filter handling OK: refusals absorbed + counted, other errors propagate")

    print("SMOKE OK")


if __name__ == "__main__":
    main()
