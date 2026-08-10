"""SM-R11: MemoryProvider interface spec (design/spec deliverable).

This package is a decision/spec artifact, not framework core. It defines the
``MemoryProvider`` seam that SM-C1 implements, plus runnable adapters and a
config-only swap test that proves the seam is plug-and-play.

Nothing here imports or mutates ``egpt`` core; the seed surfaces are referenced
by name only. See SPEC.md for the full specification and reuse dispositions.
"""
