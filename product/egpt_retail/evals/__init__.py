"""Golden question sets and the publish gate they feed (R15, D14, G13).

See ``golden.py`` for the golden-set container (a thin label over ``egpt.eval``'s own
``Case``/``run_cases`` -- deliberately not a parallel storage layer), ``rehearsal.py`` for the
human rehearsal-review record a fresh score always reopens, and
``egpt_retail.executors.golden_gate`` for the deterministic publish-gate executor that reads
both and decides block or allow. ``golden_sets.py`` builds the real sets for the agent skills
that exist in ``egpt_retail.skills`` today.
"""
