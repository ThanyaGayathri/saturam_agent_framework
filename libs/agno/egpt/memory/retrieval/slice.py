"""Token-bounded ``MemorySlice`` assembly and context injection (SM-H6 #136).

``CoLocatedMemoryProvider.read``/``aread`` (SM-H2 #132) already return one fused,
cross-tier-**ranked** ``MemorySlice`` -- top-k across tiers is that ranking's job (RRF or
``score_sum``), not this module's. What is missing is a *token* bound: ``MemoryQuery.limit`` caps
``MemorySlice.records`` by count, and a count has no relationship to how many tokens a record's
rendered content costs the prompt it is injected into. This module cuts an already-ranked slice
down to a token budget (:func:`bound_by_tokens`) and combines the result with a
:class:`~egpt.memory.working.WorkingMemory` buffer (SM-C6 #94) into one bounded, injectable string
(:func:`assemble_bounded_context`) -- the interleaving question
:meth:`~egpt.memory.provider.MemoryProvider.assemble_context`'s own docstring named as "SM-H6's,
once it exists."

Rendering reuses :func:`~egpt.memory.types.render_context_lines` / ``MemorySlice.as_context`` /
``WorkingMemory.as_context`` -- the same injection-safe lines ``LearningStore.build_context``
(``learn/stores/protocol.py:78``) exists to produce for the seed's stores. No second renderer.
"""

from dataclasses import replace
from typing import Dict, List

from egpt.memory.tiers import MemoryTier
from egpt.memory.types import MemoryRecord, MemorySlice, render_context_lines
from egpt.memory.working import WorkingMemory
from egpt.utils.tokens import count_text_tokens

__all__ = ["assemble_bounded_context", "bound_by_tokens", "require_token_budget"]


def _record_tokens(record: MemoryRecord, model_id: str) -> int:
    """Tokens ``record`` costs once rendered -- the ``[tier] `` label line counts too."""
    rendered = "\n".join(render_context_lines(record.tier.value, record.content))
    return count_text_tokens(rendered, model_id)


def require_token_budget(max_tokens: int) -> None:
    """Refuse a ``max_tokens`` a caller cannot have meant, before any read or render runs.

    Mirrors ``MemoryQuery.limit``'s own validation (``types.py``): a negative budget is not a
    smaller slice, a bare ``records[:max_tokens]``-style cut on a negative bound truncates from
    the *end*, and a caller computing ``remaining = budget - spent`` going negative would get a
    near-full slice with the best-ranked record silently missing rather than an empty one.
    Exported so :meth:`~egpt.memory.provider.MemoryProvider.assemble_context` can reject a bad
    budget **before** issuing the persistent-tier read it would otherwise discard.
    """
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 0:
        raise ValueError(f"max_tokens must be an integer >= 0, got {max_tokens!r}.")


def bound_by_tokens(memory_slice: MemorySlice, *, max_tokens: int, model_id: str = "gpt-4o") -> MemorySlice:
    """``memory_slice``, cut to the longest **prefix** of its ranked records that fits ``max_tokens``.

    ``records`` is already the fused, cross-tier ranking ``read``/``aread`` produced -- a prefix
    cut, not a re-rank, is what "top-k, token-bounded" means once a ranking already exists: the
    same top-k rule ``MemoryQuery.limit`` applies by count, applied by rendered token cost
    instead. A record that does not fit stops the cut rather than being skipped in favour of a
    smaller one further down the ranking -- skipping would silently promote a worse-ranked record
    over a better one the caller asked to see first.

    ``fusion`` on the returned slice carries the same dict as ``memory_slice.fusion`` plus
    ``token_budget``/``tokens_used``/``dropped_by_tokens``, the same "extra keys are free" floor
    :func:`~egpt.memory.types.fusion_diagnostics` documents -- so a caller reading fusion
    diagnostics can tell a token cut happened without a second return value.
    """
    require_token_budget(max_tokens)

    kept: List[MemoryRecord] = []
    used = 0
    for record in memory_slice.records:
        cost = _record_tokens(record, model_id)
        if used + cost > max_tokens:
            break
        kept.append(record)
        used += cost

    by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
    for record in kept:
        by_tier.setdefault(record.tier, []).append(record)

    fusion = dict(memory_slice.fusion)
    fusion["token_budget"] = max_tokens
    fusion["tokens_used"] = used
    fusion["dropped_by_tokens"] = len(memory_slice.records) - len(kept)

    return replace(memory_slice, records=kept, by_tier=by_tier, fusion=fusion)


def assemble_bounded_context(
    working: WorkingMemory, persistent: MemorySlice, *, max_tokens: int, model_id: str = "gpt-4o"
) -> str:
    """One token-bounded prompt string: ``working``'s buffer, verbatim, then as much of
    ``persistent``'s ranking as the remaining budget allows.

    The working half is never truncated here -- ``WorkingMemory`` already bounds itself
    (``offload``, ``max_scratchpad_entries``, ``max_summary_chars``); this function's own bound
    applies to the *persistent* half, spending whatever ``max_tokens`` the working half's own
    render leaves over. A working buffer whose rendered cost already meets or exceeds
    ``max_tokens`` leaves nothing for the persistent half -- a real, distinguishable answer
    (:func:`bound_by_tokens` accepts ``max_tokens=0``) rather than a silent overflow past the
    caller's budget.
    """
    require_token_budget(max_tokens)
    spent = count_text_tokens(working.as_context(), model_id)
    remaining = max(max_tokens - spent, 0)
    bounded = bound_by_tokens(persistent, max_tokens=remaining, model_id=model_id)
    return working.as_context(bounded)
