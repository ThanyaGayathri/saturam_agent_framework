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


def bound_by_tokens(
    memory_slice: MemorySlice, *, max_tokens: int, model_id: str = "gpt-4o", preamble: str = ""
) -> MemorySlice:
    """``memory_slice``, cut to the longest **prefix** of its ranked records that fits ``max_tokens``.

    ``records`` is already the fused, cross-tier ranking ``read``/``aread`` produced -- a prefix
    cut, not a re-rank, is what "top-k, token-bounded" means once a ranking already exists: the
    same top-k rule ``MemoryQuery.limit`` applies by count, applied by rendered token cost
    instead. A record that does not fit stops the cut rather than being skipped in favour of a
    smaller one further down the ranking -- skipping would silently promote a worse-ranked record
    over a better one the caller asked to see first.

    **Priced as the actual joined string, not a sum of independent per-record costs.** Two
    blocks tokenized separately and then joined with ``"\\n"`` do not cost the same as the one
    joined string -- BPE tokenization is not additive across a boundary it never saw (round-3
    review, PR #147: verified empirically with real ``tiktoken`` that 10+11-token blocks cost 22
    once joined, not 21). Each candidate prefix is therefore rendered and tokenized as a whole,
    matching exactly what ``MemorySlice.as_context`` -- one flattened ``"\\n".join`` over every
    kept record's lines -- actually produces. This makes the cut ``O(n)`` calls to
    ``count_text_tokens`` over strings of growing length rather than ``O(n)`` calls over
    constant-size ones, which is deliberate: correctness against the real join, not the cheaper
    additive approximation, is the point of a "token-bounded" contract.

    ``preamble``, if given, is priced as a mandatory line the final render is joined ahead of --
    :func:`assemble_bounded_context` passes ``working.as_context()`` here so the join between the
    (never-dropped) working half and the first surviving persistent record is priced too, closing
    the same gap for that boundary. An empty ``preamble`` (the default) reproduces
    ``MemorySlice.as_context``'s own join exactly, with no mandatory content ahead of the ranked
    records -- and a ``preamble`` that alone exceeds ``max_tokens`` still returns ``records=[]``
    rather than raising, the same "real, distinguishable empty answer" ``max_tokens=0`` already
    documents below.

    ``fusion`` on the returned slice carries the same dict as ``memory_slice.fusion`` plus
    ``token_budget``/``tokens_used``/``dropped_by_tokens``, the same "extra keys are free" floor
    :func:`~egpt.memory.types.fusion_diagnostics` documents -- so a caller reading fusion
    diagnostics can tell a token cut happened without a second return value. ``tokens_used``
    includes ``preamble``'s own cost when one is given, since it is now part of what was
    actually spent to produce the final render.
    """
    require_token_budget(max_tokens)

    kept: List[MemoryRecord] = []
    lines: List[str] = [preamble] if preamble else []
    used = count_text_tokens(preamble, model_id) if preamble else 0
    for record in memory_slice.records:
        candidate_lines = lines + render_context_lines(record.tier.value, record.content)
        cost = count_text_tokens("\n".join(candidate_lines), model_id)
        if cost > max_tokens:
            break
        kept.append(record)
        lines = candidate_lines
        used = cost

    by_tier: Dict[MemoryTier, List[MemoryRecord]] = {}
    for record in kept:
        by_tier.setdefault(record.tier, []).append(record)

    fusion = dict(memory_slice.fusion)
    fusion["token_budget"] = max_tokens
    fusion["tokens_used"] = used
    # Only the *first* overflowing record was actually rendered/tokenized and rejected; every
    # record ranked after it is transitively excluded by the prefix cut without being evaluated
    # individually. Still an accurate count of what did not make it in -- just not a claim that
    # each one was independently checked and found too large on its own.
    fusion["dropped_by_tokens"] = len(memory_slice.records) - len(kept)

    return replace(memory_slice, records=kept, by_tier=by_tier, fusion=fusion)


def assemble_bounded_context(
    working: WorkingMemory, persistent: MemorySlice, *, max_tokens: int, model_id: str = "gpt-4o"
) -> str:
    """One token-bounded prompt string: ``working``'s buffer, verbatim, then as much of
    ``persistent``'s ranking as the remaining budget allows.

    The working half is never truncated here -- ``WorkingMemory`` already bounds itself
    (``offload``, ``max_scratchpad_entries``, ``max_summary_chars``); this function's own bound
    applies to the *persistent* half. Delegates the whole cut to :func:`bound_by_tokens` with
    ``working.as_context()`` passed as ``preamble`` -- pricing the real working+persistent join
    exactly (round-3 review, PR #147) rather than subtracting the working half's standalone cost
    from ``max_tokens`` and pricing the persistent half against what's left, which leaves the one
    join separator between them unpriced. A working buffer whose rendered cost already meets or
    exceeds ``max_tokens`` leaves nothing for the persistent half -- a real, distinguishable
    answer (:func:`bound_by_tokens` accepts a ``preamble`` that alone exceeds the budget) rather
    than a silent overflow past the caller's budget.
    """
    require_token_budget(max_tokens)
    working_text = working.as_context()
    bounded = bound_by_tokens(persistent, max_tokens=max_tokens, model_id=model_id, preamble=working_text)
    return working.as_context(bounded)
