"""PII containment at the two memory boundaries (SM-X5, #141).

Memory is a sensitive data sink: something written once can be re-injected into a model's
context many times over (``docs/MEMORY-RESEARCH-episodic-and-neurosymbolic.md``). This module
redacts PII **on write-back**, before an event's content reaches storage, and **again on
read/inject**, before an assembled feed-forward context re-enters a run's input --
:func:`redact_events_for_writeback` and :func:`redact_context_for_read` respectively.

**Both boundaries, not one** (issue #141, Decision 1: "Handle PII on BOTH boundaries" over
"Redact-once", rejected because it "leaks on the other path"). Redacting only on write leaves a
record written by a provider or a deployment that predates this seam to leak on read; redacting
only on read leaves raw PII sitting in the store between the two. Handling both is what the
acceptance criteria's "no raw PII stored or injected unexpectedly" actually requires.

**Reuses the seed's guardrail**, per the issue's own seed fact: detection and masking are
``egpt.guardrails.pii.PIIDetectionGuardrail.detect``/``.mask`` (SSN/CC/email/phone, plus any
``custom_patterns`` a tenant's guardrail carries) -- not a second implementation of the same
regexes. Presidio (EGPT-8) is a possible future ``guardrail`` swap-in, not built here: any object
exposing the same ``detect``/``mask`` shape works, since neither function here imports
``PIIDetectionGuardrail`` by name past the config boundary.

**Driven by tenant policy** via ``MemoryProviderConfig.pii`` (:mod:`egpt.memory.config`), not a
global switch: SM-C5's per-tenant resolution seam means two tenants sharing one deployment can
resolve to different configs, so PII handling is on for one and off (or uses a different pattern
set) for another. ``config.pii`` reaches this module already validated by
``MemoryProviderConfig.__post_init__`` (:func:`~egpt.memory.config._validate_pii`) -- the
functions below are a lookup and an application, not a second validation pass.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, List, Optional, Protocol, Sequence, runtime_checkable

from egpt.memory.errors import MemoryOperationError
from egpt.memory.types import MemoryEvent, MemoryRecord
from egpt.utils.log import log_debug

if TYPE_CHECKING:
    from egpt.guardrails.pii import PIIDetectionGuardrail
    from egpt.memory.config import MemoryProviderConfig

__all__ = [
    "PII_REDACTED_KEY",
    "PIIGuardrailLike",
    "pii_guardrail_for_config",
    "redact_context_for_read",
    "redact_events_for_writeback",
    "redact_records_for_writeback",
    "redact_text_for_writeback",
]


@runtime_checkable
class PIIGuardrailLike(Protocol):
    """The two methods this module calls on a guardrail -- a structural type, not the concrete
    ``PIIDetectionGuardrail`` class, so a future Presidio (EGPT-8) swap-in or a tenant's own
    detector satisfies it without subclassing anything here.

    Mirrors ``egpt.memory.retrieval.rerank.RerankerLike``'s exact shape and reasoning: this
    module's own docstring already promises "any object exposing the same ``detect``/``mask``
    shape works," and ``config.py``'s validation must check *that* promise, not
    ``isinstance(guardrail, PIIDetectionGuardrail)`` -- which silently narrowed the accepted type
    back down to the one concrete class, contradicting the promise this module's own docstring
    makes (SM-X5 #141 round-1 review, finding #3).
    """

    def detect(self, content: str) -> List[str]: ...

    def mask(self, content: str, pii_types: Optional[Sequence[str]] = None) -> str: ...


#: Set on a redacted event's metadata to the sorted list of PII types masked out of its content.
#: Absent -- not ``[]`` -- on an event nothing was found in: the same "presence, not a falsy
#: value, signals it happened" convention ``TOOL_CALL_ERROR_KEY`` uses
#: (``egpt.memory.writeback``), so a reader checks ``"pii_redacted" in event.metadata`` rather
#: than special-casing an empty list.
PII_REDACTED_KEY = "pii_redacted"


def pii_guardrail_for_config(config: Optional["MemoryProviderConfig"]) -> Optional["PIIDetectionGuardrail"]:
    """The guardrail this tenant's policy names, or ``None`` when PII handling is off.

    ``None`` for a bare ``config.pii`` (unset, defaulting to ``{}``), for ``{"enabled": False}``,
    and for anything that is not an actual :class:`~egpt.memory.config.MemoryProviderConfig` --
    checked with ``isinstance`` rather than ``getattr(config, "pii", None)``, deliberately: a
    ``unittest.mock.MagicMock`` standing in for ``agent.memory_provider`` in a test (there are
    many) answers *any* attribute access, including ``.pii.get("enabled", False)``, with another
    truthy ``MagicMock`` -- so a duck-typed lookup would read a mock as "PII handling on" and feed
    ``guardrail.detect``/``.mask`` a mock ``PIIDetectionGuardrail`` that returns more mocks,
    silently overwriting real event content with a ``MagicMock`` object. Requiring the real
    dataclass keeps that whole class of double safely off, the same way an unconfigured
    ``config.pii`` is off.

    ``.get("guardrail")`` rather than ``["guardrail"]``: ``_validate_pii`` fills that key in
    during ``__post_init__``, but nothing stops a caller from mutating ``config.pii`` in place
    after construction (``config.pii = {"enabled": True}``), which skips that validation and
    would otherwise raise a bare ``KeyError`` on this hot path instead of a clear, fail-closed
    error (round-2 review nit).
    """
    from egpt.memory.config import MemoryProviderConfig

    if not isinstance(config, MemoryProviderConfig):
        return None
    if not config.pii.get("enabled", False):
        return None
    guardrail = config.pii.get("guardrail")
    if guardrail is None:
        raise MemoryOperationError(
            "config.pii['enabled'] is True but config.pii['guardrail'] is missing. This usually "
            "means config.pii was mutated in place after construction, bypassing "
            "MemoryProviderConfig's validation -- reconstruct the config instead of mutating "
            "its pii dict."
        )
    return guardrail


def _detect(guardrail: "PIIGuardrailLike", content: str, *, caller: str) -> List[str]:
    """``guardrail.detect(content)``, fail-closed (SM-X5 #141 round-1 review, finding #5).

    A guardrail exception on the write hot path -- non-string ``content`` (no current call site
    passes one, but nothing prevents it), or a bug in a tenant-supplied custom guardrail -- used
    to propagate unwrapped, outside the module's ``PartialWriteError`` contract every other write
    failure goes through. Fail-**closed**, not fail-open: a guardrail crash means this content's
    PII status could not be verified, so refusing the write is the safe default for a security
    boundary -- the same choice ``resolve_tenant_scope``/``_require_memory_tenant_scope`` make for
    an unscoped call, applied here to an unverifiable one. Raises
    :class:`~egpt.memory.errors.MemoryOperationError`, which every caller already turns into a
    properly-shaped ``PartialWriteError``/bare refusal via its own existing exception handling
    (``write``/``write_tier``'s ``except Exception`` fan-out, ``consolidate``'s ``if facts:``
    branch) -- this function does not need to duplicate that shaping.
    """
    try:
        return guardrail.detect(content)
    except Exception as exc:
        raise MemoryOperationError(
            f"{caller}: PII guardrail {type(guardrail).__name__}.detect() raised {exc!r} on "
            "content that could not be verified safe to store. Refusing the write rather than "
            "storing unverified content."
        ) from exc


def _mask(guardrail: "PIIGuardrailLike", content: str, detected: List[str], *, caller: str) -> str:
    """``guardrail.mask(content, detected)``, fail-closed -- the write-side twin of
    :func:`_detect`. See its docstring for the fail-closed rationale."""
    try:
        return guardrail.mask(content, detected)
    except Exception as exc:
        raise MemoryOperationError(
            f"{caller}: PII guardrail {type(guardrail).__name__}.mask() raised {exc!r} while "
            "masking detected PII. Refusing the write rather than storing unmasked content."
        ) from exc


def redact_events_for_writeback(
    events: Sequence[MemoryEvent], config: Optional["MemoryProviderConfig"]
) -> List[MemoryEvent]:
    """Mask PII out of every event's ``content`` before it reaches ``MemoryProvider.write``.

    Called from inside ``CoLocatedMemoryProvider.write``/``awrite`` (and ``SeedMemoryProvider``'s),
    the same per-adapter placement SM-X4's ``memory_span`` tracing uses -- so every caller that
    reaches storage through ``write`` gets this for free: the agent/team run loop's write-back
    hooks, ``WorkingMemory.offload``/``aoffload``, and a direct ``provider.write`` call alike.

    A plain copy of ``events`` when this tenant's policy has PII handling off -- ``config.pii``
    unset or ``enabled=False`` -- matching ``fusion``/``rerank``'s own "the zero-config shape is a
    safe no-op" convention.

    Only ``content`` is touched. ``metadata``, ``topics``, ``entities`` and the rest of the event
    envelope pass through unchanged: they are structured fields a caller supplied deliberately,
    not free text a user typed, and ``PIIDetectionGuardrail``'s patterns are built for scanning
    prose, not for judging whether a caller-supplied tag is PII. An event this redacts gets
    :data:`PII_REDACTED_KEY` added to its (copied) metadata, naming which pattern(s) matched.

    Events are otherwise passed through by reference (an event with no PII detected is not
    copied) -- callers must not rely on every returned event being a fresh object.

    A guardrail exception is fail-closed, not swallowed -- see :func:`_detect`'s docstring.
    """
    guardrail = pii_guardrail_for_config(config)
    if guardrail is None:
        return list(events)

    redacted: List[MemoryEvent] = []
    for event in events:
        detected = _detect(guardrail, event.content, caller="redact_events_for_writeback")
        if not detected:
            redacted.append(event)
            continue
        masked_content = _mask(guardrail, event.content, detected, caller="redact_events_for_writeback")
        metadata = dict(event.metadata)
        metadata[PII_REDACTED_KEY] = sorted(detected)
        redacted.append(replace(event, content=masked_content, metadata=metadata))
        log_debug(f"Redacted PII ({', '.join(sorted(detected))}) from a write-back event before storage.")
    return redacted


def redact_records_for_writeback(
    records: Sequence[MemoryRecord], config: Optional["MemoryProviderConfig"]
) -> List[MemoryRecord]:
    """Mask PII out of every record's ``content`` before it reaches a vector/graph write.

    The :class:`~egpt.memory.types.MemoryRecord` counterpart of
    :func:`redact_events_for_writeback`, for the consolidation lane (SM-X5 #141 round-1 review,
    finding #2): ``CoLocatedMemoryProvider.consolidate``/``aconsolidate`` reads raw episodes via
    ``vector.scan`` -- bypassing ``write``/``awrite`` entirely, since these rows already exist in
    storage -- and derives facts, entity/relation extraction, and profile updates from that
    content. None of those derived writes went through ``write``, so none of them were reached by
    write-back redaction until this function's call sites were added.

    Same contract as :func:`redact_events_for_writeback`: a plain copy when PII handling is off,
    only ``content`` is touched, :data:`PII_REDACTED_KEY` is added to a redacted record's (copied)
    metadata, and an untouched record is passed through by reference.

    Redacting the episodes consolidation reads is necessary but not sufficient on its own: the
    LLM-driven distillation/reflection/summarization/entity-extraction passes downstream can still
    restate or paraphrase PII in their own generated text even when their input was clean. Callers
    that build new :class:`FactWrite`/:class:`EntityWrite` records from LLM output must also
    redact those derived records before they reach a vector/graph write -- this function is not a
    single choke point for the whole consolidation lane, only for record-shaped writes.
    """
    guardrail = pii_guardrail_for_config(config)
    if guardrail is None:
        return list(records)

    redacted: List[MemoryRecord] = []
    for record in records:
        detected = _detect(guardrail, record.content, caller="redact_records_for_writeback")
        if not detected:
            redacted.append(record)
            continue
        masked_content = _mask(guardrail, record.content, detected, caller="redact_records_for_writeback")
        metadata = dict(record.metadata)
        metadata[PII_REDACTED_KEY] = sorted(detected)
        redacted.append(replace(record, content=masked_content, metadata=metadata))
        log_debug(f"Redacted PII ({', '.join(sorted(detected))}) from a consolidation record before storage.")
    return redacted


def redact_text_for_writeback(text: Optional[str], config: Optional["MemoryProviderConfig"]) -> Optional[str]:
    """Mask PII out of one free-text field that is not itself a
    :class:`~egpt.memory.types.MemoryEvent`/:class:`~egpt.memory.types.MemoryRecord` -- a
    ``FactWrite``'s ``subject``/``predicate``/``object``, or a profile field, for example.

    For the fields :func:`redact_records_for_writeback` cannot reach: subject/predicate/object
    strings live directly on ``FactWrite``, not inside its composed ``record.content``, and a
    ``UserProfile``'s fields are serialized into ``metadata["profile"]`` -- which
    :func:`redact_events_for_writeback`/:func:`redact_records_for_writeback` deliberately never
    touch, since ``metadata`` is caller-structured data everywhere else in this module.

    Returns ``text`` unchanged (including ``None``) when PII handling is off, when ``text`` is
    falsy, or when nothing in it matches this tenant's guardrail. A guardrail exception is
    fail-closed, not swallowed -- see :func:`_detect`'s docstring.
    """
    guardrail = pii_guardrail_for_config(config)
    if guardrail is None or not text:
        return text
    detected = _detect(guardrail, text, caller="redact_text_for_writeback")
    if not detected:
        return text
    return _mask(guardrail, text, detected, caller="redact_text_for_writeback")


def redact_context_for_read(context: str, config: Optional["MemoryProviderConfig"]) -> str:
    """Mask PII out of an assembled feed-forward context before it re-enters a run's input.

    Applied by ``MemoryProvider.assemble_context``/``aassemble_context`` after the working and
    persistent halves are already combined into one string, so this is a policy check on the
    string a caller is about to inject -- not a per-record pass over ``MemorySlice`` separate from
    the working buffer.

    A second, independent boundary from write-back, not a backstop for it (issue #141, Decision
    1): a record a provider that predates this seam wrote un-redacted, or one written in a session
    before a tenant's policy turned PII handling on, is still caught here before it reaches a
    prompt.

    Returns ``context`` unchanged when PII handling is off, when ``context`` is empty, or when
    nothing in ``context`` matches this tenant's guardrail.
    """
    guardrail = pii_guardrail_for_config(config)
    if guardrail is None or not context:
        return context
    detected = guardrail.detect(context)
    if not detected:
        return context
    log_debug(f"Redacted PII ({', '.join(sorted(detected))}) from assembled memory context before injection.")
    return guardrail.mask(context, detected)
