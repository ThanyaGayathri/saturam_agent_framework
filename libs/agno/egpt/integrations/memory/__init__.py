"""Memory substrate backends (SM-G1 #99, SM-V1 #95).

The **integrations** half of the memory seam. `egpt/memory/` defines the ports and composes them;
the concrete engines live here and are reached only by registry key, never imported inward
(ADR 0001).
"""
