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

__all__ = ["MAX_RECORDS_PER_CUT", "assemble_bounded_context", "bound_by_tokens", "require_token_budget"]

#: A hard ceiling on how many records one `bound_by_tokens` cut will ever examine, independent of
#: `max_tokens` or how many records `memory_slice` carries. The cut loop re-tokenizes the *entire*
#: accumulated joined prefix on every iteration (O(n) tokenizer calls over strings of growing
#: length, round-4 review PR #147) so that each step prices the real join rather than an additive
#: approximation -- deliberate (see `bound_by_tokens`'s own docstring), but unbounded against
#: `MemoryQuery.limit`'s own `MAX_LIMIT` (10_000, `types.py`) it is quadratic with no ceiling: a
#: caller pairing a generous `limit` with many small records gets silently slow behaviour. No
#: realistic prompt injects anywhere near this many individual memory records regardless of token
#: budget, so this is a backstop against a pathological (huge limit, huge max_tokens, tiny
#: records) combination, not a bound ordinary use should ever reach.
MAX_RECORDS_PER_CUT = 500


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
    documents below. **Spliced in verbatim, not through** :func:`~egpt.memory.types.render_context_lines`
    -- pass only already-rendered/escaped text (``working.as_context()`` is; unrendered,
    caller-supplied text is not) or the line-forgery protection every other line here gets is lost
    for this one.

    At most :data:`MAX_RECORDS_PER_CUT` records are ever examined, regardless of how many
    ``memory_slice`` carries or how large ``max_tokens`` is -- see that constant's own comment for
    why (round-4 review, PR #147: the O(n) tokenizer calls per cut are otherwise unbounded against
    ``MemoryQuery.limit``'s 10,000 ceiling).

    ``fusion`` on the returned slice carries the same dict as ``memory_slice.fusion`` plus
    ``token_budget``/``tokens_used``/``dropped_by_tokens``, the same "extra keys are free" floor
    :func:`~egpt.memory.types.fusion_diagnostics` documents -- so a caller reading fusion
    diagnostics can tell a token cut happened without a second return value. ``tokens_used``
    includes ``preamble``'s own cost when one is given, since it is now part of what was actually
    spent to produce the final render -- and can therefore **exceed** ``token_budget`` when
    ``preamble`` alone already does (the same case ``max_tokens=0``-style handling above admits);
    do not assume ``tokens_used <= token_budget`` as an invariant. ``dropped_by_tokens`` counts
    every record that did not make it in, but when ``preamble`` alone overflows, *zero* records
    were individually evaluated (the loop's very first candidate already exceeds ``max_tokens``) --
    the count is still accurate, it is just not a claim that each one was checked and rejected on
    its own merits, the same caveat that already applies to every record after the first overflow
    in the ordinary (no-preamble-overflow) case.
    """
    require_token_budget(max_tokens)

    kept: List[MemoryRecord] = []
    lines: List[str] = [preamble] if preamble else []
    used = count_text_tokens(preamble, model_id) if preamble else 0
    for record in memory_slice.records[:MAX_RECORDS_PER_CUT]:
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
    # Only the *first* overflowing record was actually rendered/tokenized and rejected (or, if
    # `preamble` alone already overflows, none were) -- every record after it is transitively
    # excluded by the prefix cut without being evaluated individually. Still an accurate count of
    # what did not make it in -- just not a claim that each one was independently checked.
    fusion["dropped_by_tokens"] = len(memory_slice.records) - len(kept)
    fusion["record_cap_hit"] = len(memory_slice.records) > MAX_RECORDS_PER_CUT

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
    join separator between them unpriced.

    **``max_tokens`` bounds the persistent half; it is not an upper bound on the returned
    string.** A working buffer whose rendered cost already meets or exceeds ``max_tokens`` leaves
    nothing for the persistent half (``bound_by_tokens`` returns ``records=[]``, a real,
    distinguishable answer -- see its own docstring), but the string this function then returns is
    ``working.as_context()`` **unmodified**, whose own cost can exceed ``max_tokens`` with no
    caller-visible signal in the returned ``str`` (round-4 review, PR #147: the diagnostics that
    would reveal it live on the internal, now-discarded ``MemorySlice`` ``bound_by_tokens``
    produced, not on this function's return value). The working half is a **floor this function
    guarantees is never cut short**, not a ceiling ``max_tokens`` enforces on it -- a caller that
    needs to know whether the working half alone already exceeded budget must call
    :func:`bound_by_tokens` directly (or measure ``working.as_context()`` itself) rather than
    relying on this function's return value to reveal it.
    """
    require_token_budget(max_tokens)
    working_text = working.as_context()
    bounded = bound_by_tokens(persistent, max_tokens=max_tokens, model_id=model_id, preamble=working_text)
    return working.as_context(bounded)
