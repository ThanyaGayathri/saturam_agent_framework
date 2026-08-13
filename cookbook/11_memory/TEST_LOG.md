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
stand-ins: write to both substrates, consolidate episodes into semantic facts, and read a merged
slice back as prompt context. Then runs the same consumer function against a second config
(`mode=FRONTIER`) to show the mode reaches the provider without any call-site edit, followed by a
bi-temporal point-in-time read across an invalidation and a cross-tenant isolation check.

**Result:** Success (re-run after the scope reset). The run is **byte-identical across 10
executions** -- verified by hashing, because an earlier version of this claim was false:
consolidation grouped episodes in UUID-driven scan order, so four distinct outputs appeared across
eight runs. Grouping now walks content order.

Both mode blocks produce identical output from the same consumer function. Consolidation merged
2 episodes into 2 facts (the statements are distinct, so nothing deduped), and the merge reports
`method=score_sum` -- there is no weighted or RRF fusion in SM-C1, which is SM-H2's (#132). The
point-in-time read returns the fact `as_of=200` and not `as_of=400`, confirming invalidation is
expire-and-reinsert rather than a delete. A read scoped to tenant `globex` returns 0 of tenant
`acme`'s records. Requires no database, no model, and no API key.

Two defects in this file's reference substrates were found by review and fixed: `upsert` stored the
caller's object rather than a copy, so the "two independent substrates" shared one row and
`graph.invalidate()` also stamped `expired_at` on the vector copy -- the bi-temporal demo worked
partly for the wrong reason. And `invalidate` overwrote `t_invalid` instead of taking the tightest
bound, which is the in-place clobber its own comment cites SM-R10 for avoiding. Verified after the
fix: `vector row IS graph row -> False`, and two invalidations leave one current belief with
`t_invalid=250`.

Note that `scan` here takes no cursor. The `since` / `after_id` pair it used to model was removed
along with the consolidation resumption protocol; a resumable walk is SM-L1's (#122).

---
