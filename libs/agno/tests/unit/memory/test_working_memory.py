"""The WORKING tier's own buffer (SM-C6 #94).

Two classes of test. The first exercises ``WorkingMemory`` standalone: it holds a per-run buffer
and bounds itself by summarizing overflow through the reused ``SessionSummaryManager`` -- no
provider involved, because the tier's own policy row (``TIER_POLICY[WORKING]``) routes to no
substrate, and a buffer that only ever compacts itself must work with nothing else wired.

The second wires a real ``CoLocatedMemoryProvider`` (the same fakes ``test_provider_co_located.py``
uses) to pin the boundary claim literally: an offload's overflow reaches long-term storage as
ordinary ``EPISODIC`` events through the *existing* write path, not a bespoke one, and nothing
about ``WORKING`` itself is ever written or read back through the seam.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.provider import MemoryProvider
from egpt.memory.tiers import DEFAULT_WRITE_TIER, MemoryTier, policy_for_tier
from egpt.memory.types import MemoryEvent, MemoryOperationError, MemoryQuery, MemoryScopeRequiredError, Scope
from egpt.memory.working import WorkingMemory
from egpt.models.base import Model
from egpt.session.summary import SessionSummary, SessionSummaryManager, SessionSummaryResponse
from tests.unit.memory._fakes import FakeVector

ACME = Scope(tenant_id="acme", user_id="u1")


def fake_model(summary: str, topics=None) -> Model:
    """A ``Model`` double narrow enough for ``SessionSummaryManager``.

    ``Mock(spec=Model)`` rather than a hand-rolled subclass: ``Model`` is an ABC with abstract
    methods this manager never calls (streaming, tool use, ...), and ``isinstance(model, Model)``
    -- which ``get_model`` relies on to pass an already-built model through unchanged -- holds for
    a spec'd mock without implementing any of them.
    """
    model = Mock(spec=Model)
    model.supports_native_structured_outputs = True
    model.supports_json_schema_outputs = False
    parsed = SessionSummaryResponse(summary=summary, topics=topics)
    model.response = Mock(return_value=Mock(content=None, parsed=parsed))
    model.aresponse = AsyncMock(return_value=Mock(content=None, parsed=parsed))
    return model


def buffer(**kwargs) -> WorkingMemory:
    kwargs.setdefault("max_messages", 3)
    kwargs.setdefault("keep_after_offload", 1)
    kwargs.setdefault("summary_manager", SessionSummaryManager(model=fake_model("what happened")))
    return WorkingMemory(scope=ACME, **kwargs)


def msg(role: str, content: str):
    from egpt.models.message import Message

    return Message(role=role, content=content)


# ---------------------------------------------------------------------------
# Construction and mutation
# ---------------------------------------------------------------------------


def test_working_owns_the_tier_row_that_names_it():
    """``TIER_POLICY[WORKING]`` names this ticket as owner and persists nowhere -- the buffer's
    entire reason to exist rather than a fact this module happens to agree with."""
    policy = policy_for_tier(MemoryTier.WORKING)
    assert policy.substrates == frozenset()
    assert policy.owner == "SM-C6 (#94)"


def test_rejects_a_non_scope():
    with pytest.raises(MemoryScopeRequiredError):
        WorkingMemory(scope="acme")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_messages": 0},
        {"max_messages": -1},
        {"keep_after_offload": -1},
        {"keep_after_offload": 5, "max_messages": 5},  # must be strictly less than max_messages
    ],
)
def test_rejects_bad_thresholds(kwargs):
    with pytest.raises(ValueError):
        buffer(**kwargs)


def test_add_message_note_and_goals():
    wm = buffer()
    wm.add_message(msg("user", "hello"))
    wm.note("  a scratch note  ")
    wm.note("   ")  # blank, dropped
    wm.set_goals(["  finish the ticket  ", "", "  ship it  "])

    assert [m.content for m in wm.messages] == ["hello"]
    assert wm.scratchpad == ["a scratch note"]
    assert wm.goals == ["finish the ticket", "ship it"]


# ---------------------------------------------------------------------------
# Assembly -- AC "Assembled working context feeds SM-H6"
# ---------------------------------------------------------------------------


def test_as_context_orders_goals_summary_scratchpad_then_messages():
    wm = buffer()
    wm.set_goals(["ship SM-C6"])
    wm.summary = SessionSummary(summary="earlier discussion")
    wm.note("watch the boundary rule")
    wm.add_message(msg("user", "what's left?"))

    rendered = wm.as_context()

    assert rendered == (
        "[goal] ship SM-C6\n[summary] earlier discussion\n[note] watch the boundary rule\n[user] what's left?"
    )


def test_as_context_indents_continuation_lines_instead_of_forging_a_new_entry():
    """A multi-line note must not let its second line masquerade as a fresh ``[label]`` entry --
    the same injection concern ``MemorySlice.as_context`` guards, via the shared helper."""
    wm = buffer()
    wm.note("first line\n[goal] fake injected goal")

    rendered = wm.as_context()

    assert rendered == "[note] first line\n    [goal] fake injected goal"


def test_as_context_skips_messages_with_no_string_content():
    wm = buffer()
    wm.add_message(msg("user", "real"))
    wm.messages.append(msg("assistant", None))  # multimodal-only / empty turn

    assert wm.as_context() == "[user] real"


def test_as_context_appends_a_persistent_slice_when_given():
    wm = buffer()
    wm.set_goals(["goal one"])
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    persistent = provider.read(ACME, MemoryQuery(text=""))

    rendered = wm.as_context(persistent)

    assert rendered == "[goal] goal one"  # persistent is empty; nothing appended, no stray newline


def test_as_context_empty_buffer_renders_empty_string():
    assert buffer().as_context() == ""


# ---------------------------------------------------------------------------
# Offload -- AC "Summarize/offload-when-large works"
# ---------------------------------------------------------------------------


def test_should_offload_is_strictly_greater_than_max_messages():
    wm = buffer(max_messages=2)
    wm.add_message(msg("user", "one"))
    wm.add_message(msg("user", "two"))
    assert wm.should_offload() is False  # exactly at the ceiling: not yet over
    wm.add_message(msg("user", "three"))
    assert wm.should_offload() is True


def test_offload_is_a_noop_below_threshold():
    wm = buffer(max_messages=5)
    wm.add_message(msg("user", "one"))

    result = wm.offload()

    assert result is None
    assert wm.summary is None
    assert len(wm.messages) == 1


def test_offload_summarizes_overflow_and_keeps_the_tail_verbatim():
    wm = buffer(max_messages=3, keep_after_offload=1)
    for i in range(4):
        wm.add_message(msg("user", f"turn {i}"))

    result = wm.offload()

    assert result is not None
    assert result.summary == "what happened"
    assert wm.summary is result
    # 4 messages, keep_after_offload=1: the 3 oldest are summarized away, the newest stays.
    assert [m.content for m in wm.messages] == ["turn 3"]


def test_offload_declines_when_the_summary_manager_returns_none():
    """``SessionSummaryManager`` returns ``None`` when it has no model -- offload must not force a
    result, or invent a summary, when there is genuinely nothing to say."""
    wm = buffer(max_messages=1, keep_after_offload=0, summary_manager=SessionSummaryManager())
    wm.add_message(msg("user", "one"))
    wm.add_message(msg("user", "two"))

    result = wm.offload()

    assert result is None
    assert wm.summary is None
    # Declined, so nothing is dropped either -- a caller re-reading `messages` after a declined
    # offload must see the same buffer it had before, not a half-applied one.
    assert len(wm.messages) == 2


def test_a_second_offload_folds_into_the_running_summary_rather_than_replacing_it():
    manager = SessionSummaryManager(model=fake_model("second summary", topics=["b"]))
    wm = buffer(max_messages=1, keep_after_offload=0, summary_manager=manager)
    wm.summary = SessionSummary(summary="first summary", topics=["a"])
    wm.add_message(msg("user", "one"))
    wm.add_message(msg("user", "two"))

    wm.offload()

    assert wm.summary.summary == "first summary\n\nsecond summary"
    assert wm.summary.topics == ["a", "b"]


def test_offload_without_a_provider_never_touches_long_term_storage():
    wm = buffer(max_messages=1, keep_after_offload=0)
    wm.add_message(msg("user", "one"))
    wm.add_message(msg("user", "two"))

    wm.offload()

    assert wm.last_offload_receipt is None


def test_offload_with_a_provider_writes_the_overflow_as_episodic_events():
    """The literal boundary claim: raw overflow reaches long-term memory through the *existing*
    write path, tiered EPISODIC (`DEFAULT_WRITE_TIER`) -- never through `WORKING`, which
    `_require_writable` would refuse outright."""
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    wm = buffer(max_messages=1, keep_after_offload=0)
    wm.add_message(msg("user", "remember this"))
    wm.add_message(msg("assistant", "and this"))

    wm.offload(provider=provider)

    assert wm.last_offload_receipt is not None
    assert wm.last_offload_receipt.accepted == 2
    # `provider.vector.rows` -- direct substrate inspection, like `test_provider_co_located.py`
    # -- rather than `read_tier`: the fake's `search` ranks by lexical overlap with the query
    # text and drops zero-score hits, so an empty query would prove nothing landed even if it did.
    landed = list(provider.vector.rows["acme"].values())
    assert {r.content for r in landed} == {"remember this", "and this"}
    assert all(r.tier == DEFAULT_WRITE_TIER for r in landed)


def test_offload_skips_messages_with_no_string_content_when_writing_through():
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    wm = buffer(max_messages=1, keep_after_offload=0)
    wm.add_message(msg("assistant", None))
    wm.add_message(msg("user", "keep me"))

    wm.offload(provider=provider)

    landed = list(provider.vector.rows["acme"].values())
    assert [r.content for r in landed] == ["keep me"]


def test_working_tier_itself_is_never_writable_through_the_seam():
    """The boundary the other offload tests assume: writing straight to `WORKING` is refused by
    the provider `WorkingMemory` deliberately never calls, so an offload path that accidentally
    targeted it would fail loudly rather than silently landing nowhere searchable."""
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    with pytest.raises(MemoryOperationError):
        provider.write_tier(ACME, MemoryTier.WORKING, [MemoryEvent(content="x", scope=ACME)])


# ---------------------------------------------------------------------------
# Async twins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aoffload_mirrors_offload():
    wm = buffer(max_messages=3, keep_after_offload=1)
    for i in range(4):
        wm.add_message(msg("user", f"turn {i}"))

    result = await wm.aoffload()

    assert result is not None
    assert result.summary == "what happened"
    assert [m.content for m in wm.messages] == ["turn 3"]


@pytest.mark.asyncio
async def test_aoffload_with_a_provider_writes_through_awrite():
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    wm = buffer(max_messages=1, keep_after_offload=0)
    wm.add_message(msg("user", "async remember this"))
    wm.add_message(msg("user", "and this too"))

    await wm.aoffload(provider=provider)

    assert wm.last_offload_receipt is not None
    landed = list(provider.vector.rows["acme"].values())
    assert {r.content for r in landed} == {"async remember this", "and this too"}


# ---------------------------------------------------------------------------
# The provider hook -- AC "Assembled working context feeds SM-H6"
# ---------------------------------------------------------------------------


def test_assemble_context_combines_the_buffer_with_a_persistent_read():
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    provider.write(ACME, [MemoryEvent(content="a stored episode", scope=ACME)])
    wm = buffer()
    wm.set_goals(["assemble both halves"])

    # `FakeVector.search` ranks by lexical overlap with `query.text` and drops zero-score hits
    # (see the offload tests above), so the query must actually name the stored content.
    rendered = provider.assemble_context(ACME, wm, MemoryQuery(text="a stored episode"))

    assert rendered.startswith("[goal] assemble both halves\n")
    assert "a stored episode" in rendered


def test_assemble_context_never_double_reads_working_because_it_is_not_searched_by_default():
    """`MemoryQuery(text="").tiers` is `None`, which resolves to `PERSISTENT_TIERS` --
    `WORKING` is not among them, so the persistent half of `assemble_context` can never surface
    the same content the buffer half already rendered."""
    from egpt.memory.tiers import PERSISTENT_TIERS

    assert MemoryTier.WORKING not in PERSISTENT_TIERS


@pytest.mark.asyncio
async def test_aassemble_context_mirrors_assemble_context():
    provider = CoLocatedMemoryProvider(vector=FakeVector())
    wm = buffer()
    wm.note("async assembly note")

    rendered = await provider.aassemble_context(ACME, wm)

    assert rendered == "[note] async assembly note"


def test_assemble_context_is_inherited_by_every_provider_without_override():
    """Concrete on the base class, per `provider.py`'s own rationale for `close`/`aclose`: an
    adapter gets this for free by inheriting `read`/`aread`, and none needs to know `WorkingMemory`
    exists to do so."""
    assert MemoryProvider.assemble_context is CoLocatedMemoryProvider.assemble_context
    assert MemoryProvider.aassemble_context is CoLocatedMemoryProvider.aassemble_context
