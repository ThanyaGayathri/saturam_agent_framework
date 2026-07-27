"""SM-R13 memory eval harness (plan + runnable skeleton).

This package is the *plan deliverable* for SM-R13 (issue #88): a reusable
evaluation backbone that measures memory quality (QA correctness, retrieval
recall@k / nDCG@k, temporal reasoning, bi-temporal "true at T", decay, latency,
token cost) by driving a ``MemoryProvider`` (the SM-R11 seam, #86) and scoring
through the seed's ``egpt.eval.suite`` (``Case`` / ``run_cases``).

It is intentionally self-contained under ``benchmarks/`` so it merges cleanly
while the core is being rebranded, and so SM-X7 (#143) can lift it onto
``eval/suite.py`` when it implements the harness for real.
"""
