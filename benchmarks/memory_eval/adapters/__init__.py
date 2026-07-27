"""Adapters that bind the harness to core (egpt) — imported lazily.

Everything under ``harness/`` is stdlib-only and runs offline. The bridges to the
seed's ``egpt.eval.suite`` / ``egpt.eval.agent_as_judge`` and to a real LLM live
here so that importing the harness never requires the (rebrand-moving) core to be
installed. These modules are exercised only in ``--judge llm`` / suite mode.
"""
