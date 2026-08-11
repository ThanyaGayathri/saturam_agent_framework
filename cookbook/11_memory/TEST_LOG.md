# Test Log: 11_memory

> Tests not yet run. Run each file and update this log.

### 01_agent_with_memory.py

**Status:** PENDING

**Description:** Tests persistent agent memory updates and retrieval for one user.

---

### 02_agentic_memory.py

**Status:** PENDING

**Description:** Tests agentic memory create, recall, delete, and update operations.

---

### 03_agents_share_memory.py

**Status:** PENDING

**Description:** Tests memory sharing across two agents using the same database.

---

### 04_custom_memory_manager.py

**Status:** PENDING

**Description:** Tests custom memory manager instructions and memory retrieval behavior.

---

### 05_multi_user_multi_session_chat.py

**Status:** PENDING

**Description:** Tests sequential multi-user, multi-session memory behavior.

---

### 06_multi_user_multi_session_chat_concurrent.py

**Status:** PENDING

**Description:** Tests concurrent multi-user, multi-session memory behavior.

---

### 07_share_memory_and_history_between_agents.py

**Status:** PENDING

**Description:** Tests shared session history and memory across two agents.

---

### 08_memory_tools.py

**Status:** PENDING

**Description:** Tests MemoryTools and WebSearchTools integration with user memory.

---

### 09_memory_provider_seam.py

**Status:** PASS

**Description:** Exercises the SM-C1 MemoryProvider seam end to end with two in-file substrate
stand-ins: write to both substrates, consolidate episodes into semantic facts, and read a fused
slice back as prompt context. Then runs the same consumer function against a second config
(provider `external`, mode `frontier`) to show the swap is configuration only, followed by a
bi-temporal point-in-time read across an invalidation and a cross-tenant isolation check.

**Result:** Success (re-run after the PR #60 round-4 fixes). Mode A and Mode B produce identical
output from the same consumer function, and the whole run is **byte-identical across 10 executions**
-- verified, because an earlier version of this claim was false: consolidation grouped episodes in
UUID-driven scan order, so four distinct outputs appeared across eight runs and the two mode blocks
sometimes disagreed within a single process.
Consolidation merged 2 episodes into 2 facts (the two statements are distinct, so nothing
deduped). The point-in-time read returns the fact `as_of=200` and not `as_of=400`, confirming
invalidation is expire-and-reinsert rather than a delete. A read scoped to tenant `globex`
returns 0 of tenant `acme`'s records. Requires no database, no model, and no API key.

Two defects in this file's reference substrates were found by review and fixed: `upsert` stored the
caller's object rather than a copy, so the "two independent substrates" shared one row and
`graph.invalidate()` also stamped `expired_at` on the vector copy -- the bi-temporal demo worked
partly for the wrong reason. And `invalidate` overwrote `t_invalid` instead of taking the tightest
bound, which is the in-place clobber its own comment cites SM-R10 for avoiding. Verified after the
fix: `vector row IS graph row -> False`, and two invalidations leave one current belief with
`t_invalid=250`.

---
