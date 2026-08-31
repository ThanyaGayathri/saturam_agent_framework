"""PII containment at the memory seam's two boundaries (SM-X5, #141).

Three layers, matching the module split:

* ``config.pii`` validation (``MemoryProviderConfig.__post_init__`` -> ``_validate_pii``) --
  exercised through the public seam, the same way ``test_rerank.py``'s sibling coverage in
  ``test_provider_co_located.py`` exercises ``_validate_rerank``.
* ``egpt.memory.security.pii``'s own pure functions, independent of any provider.
* The two wiring points: ``MemoryProvider.assemble_context`` (read/inject) and
  ``MemoryProvider.write``/``awrite`` (write-back -- SM-X4's ``memory_span`` tracing lives at the
  same per-adapter seam, for the same reason: every write-back caller, run-loop or otherwise,
  funnels through this one method). ``test_writeback.py`` and ``test_working_memory.py`` already
  cover everything else those call sites do; this file only pins the redaction step landing.
"""

from unittest.mock import MagicMock

import pytest

from egpt.guardrails.pii import PIIDetectionGuardrail
from egpt.memory.adapters.co_located import CoLocatedMemoryProvider
from egpt.memory.adapters.seed import SeedMemoryProvider
from egpt.memory.config import MemoryProviderConfig, MemoryProviderConfigError
from egpt.memory.security.pii import (
    PII_REDACTED_KEY,
    pii_guardrail_for_config,
    redact_context_for_read,
    redact_events_for_writeback,
    redact_records_for_writeback,
    redact_text_for_writeback,
)
from egpt.memory.types import MemoryEvent, MemoryQuery, MemoryRecord, MemoryTier, Scope
from egpt.memory.working import WorkingMemory
from egpt.models.base import Model
from egpt.session.summary import SessionSummaryManager, SessionSummaryResponse
from tests.unit.memory._fakes import FakeVector
from tests.unit.memory.test_provider_seed_bridge import FakeManager

ACME = Scope(tenant_id="acme", user_id="ada")


def _fake_model(summary: str) -> Model:
    """Narrow enough for `SessionSummaryManager` -- see `test_working_memory.py`'s twin."""
    from unittest.mock import Mock

    model = Mock(spec=Model)
    model.supports_native_structured_outputs = True
    model.supports_json_schema_outputs = False
    parsed = SessionSummaryResponse(summary=summary, topics=None)
    model.response = Mock(return_value=Mock(content=None, parsed=parsed))
    return model


# ---------------------------------------------------------------------------
# config.pii validation
# ---------------------------------------------------------------------------


class TestPiiConfigValidation:
    def test_default_is_disabled(self):
        config = MemoryProviderConfig(provider="co_located")
        assert config.pii == {"enabled": False}

    def test_enabled_with_no_guardrail_builds_the_default_one(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        assert config.pii["enabled"] is True
        assert isinstance(config.pii["guardrail"], PIIDetectionGuardrail)

    def test_enabled_with_a_custom_guardrail_passes_it_through_unchanged(self):
        custom = PIIDetectionGuardrail(custom_patterns={"employee_id": r"EMP-\d{4}"})
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True, "guardrail": custom})
        assert config.pii["guardrail"] is custom

    def test_unknown_key_is_rejected(self):
        with pytest.raises(MemoryProviderConfigError, match="unknown key"):
            MemoryProviderConfig(provider="co_located", pii={"enabled": True, "bogus": 1})

    def test_non_bool_enabled_is_rejected(self):
        with pytest.raises(MemoryProviderConfigError, match="must be a bool"):
            MemoryProviderConfig(provider="co_located", pii={"enabled": "yes"})

    def test_guardrail_without_enabled_is_rejected(self):
        """The same "no knob that silently does nothing" rule `_validate_fusion` applies to `k`."""
        with pytest.raises(MemoryProviderConfigError, match="only meaningful when enabled=True"):
            MemoryProviderConfig(provider="co_located", pii={"guardrail": PIIDetectionGuardrail()})

    def test_wrong_typed_guardrail_is_rejected(self):
        """Structural, not nominal: rejected for lacking detect()/mask(), not for failing an
        isinstance(..., PIIDetectionGuardrail) check -- see test_a_duck_typed_guardrail_is_accepted
        for the other side of this same contract."""
        with pytest.raises(MemoryProviderConfigError, match="must expose detect.*and mask"):
            MemoryProviderConfig(provider="co_located", pii={"enabled": True, "guardrail": "not-a-guardrail"})

    def test_a_duck_typed_guardrail_is_accepted(self):
        """`security/pii.py`'s own module docstring promises "any object exposing the same
        detect/mask shape works" (a future Presidio (EGPT-8) swap-in, or a tenant's own
        detector) -- pinning that promise against an object that is not a PIIDetectionGuardrail
        subclass at all (SM-X5 #141 round-1 review, finding #3)."""

        class CustomGuardrail:
            def detect(self, content: str):
                return ["Custom"] if "secret" in content else []

            def mask(self, content: str, pii_types=None) -> str:
                return content.replace("secret", "[REDACTED]")

        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True, "guardrail": CustomGuardrail()})
        assert isinstance(config.pii["guardrail"], CustomGuardrail)

    def test_non_dict_pii_is_rejected(self):
        with pytest.raises(MemoryProviderConfigError, match="must be a dict"):
            MemoryProviderConfig(provider="co_located", pii=["enabled"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# pii_guardrail_for_config
# ---------------------------------------------------------------------------


class TestPiiGuardrailForConfig:
    def test_none_for_the_default_disabled_config(self):
        assert pii_guardrail_for_config(MemoryProviderConfig(provider="co_located")) is None

    def test_none_for_none(self):
        assert pii_guardrail_for_config(None) is None

    def test_none_for_a_mock_config(self):
        """A `MagicMock` standing in for a provider's `.config` in a test answers any attribute
        access with another truthy mock -- this must not read as "PII handling on". See the
        function's own docstring for why a duck-typed lookup would be unsafe here."""
        assert pii_guardrail_for_config(MagicMock()) is None

    def test_returns_the_configured_guardrail_when_enabled(self):
        custom = PIIDetectionGuardrail()
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True, "guardrail": custom})
        assert pii_guardrail_for_config(config) is custom

    def test_raises_a_clear_error_when_pii_dict_was_mutated_in_place_past_validation(self):
        """`config.pii` is a plain dict on a non-frozen dataclass -- nothing stops
        `config.pii = {"enabled": True}` after construction, which skips `_validate_pii`'s
        default-guardrail fill-in. This must fail closed with a clear error, not a bare
        `KeyError` deep in the write/read hot path (round-2 review nit)."""
        from egpt.memory.errors import MemoryOperationError

        config = MemoryProviderConfig(provider="co_located")
        config.pii = {"enabled": True}

        with pytest.raises(MemoryOperationError, match="guardrail.*is missing"):
            pii_guardrail_for_config(config)


# ---------------------------------------------------------------------------
# redact_events_for_writeback
# ---------------------------------------------------------------------------


class TestRedactEventsForWriteback:
    def test_disabled_policy_is_a_no_op_and_events_pass_through_by_reference(self):
        config = MemoryProviderConfig(provider="co_located")
        events = [MemoryEvent(content="call me at 555-123-4567", scope=ACME)]

        out = redact_events_for_writeback(events, config)

        assert out[0] is events[0]
        assert out[0].content == "call me at 555-123-4567"

    def test_mock_config_is_a_no_op(self):
        events = [MemoryEvent(content="ssn 123-45-6789", scope=ACME)]
        out = redact_events_for_writeback(events, MagicMock())
        assert out[0] is events[0]

    def test_enabled_policy_masks_content_and_tags_metadata(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        events = [MemoryEvent(content="email me at foo@example.com", scope=ACME)]

        out = redact_events_for_writeback(events, config)

        assert "@" not in out[0].content
        assert "foo" not in out[0].content
        assert out[0].metadata[PII_REDACTED_KEY] == ["Email"]
        # The source event is untouched -- `replace()` builds a new one.
        assert events[0].content == "email me at foo@example.com"

    def test_multiple_pii_types_in_one_event_are_all_masked_and_named(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        events = [MemoryEvent(content="email foo@example.com or call 555-123-4567", scope=ACME)]

        out = redact_events_for_writeback(events, config)

        assert "@" not in out[0].content
        assert "555-123-4567" not in out[0].content
        assert out[0].metadata[PII_REDACTED_KEY] == ["Email", "Phone"]

    def test_an_event_with_no_pii_is_passed_through_by_reference_even_when_enabled(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        events = [MemoryEvent(content="nothing sensitive here", scope=ACME)]

        out = redact_events_for_writeback(events, config)

        assert out[0] is events[0]
        assert PII_REDACTED_KEY not in out[0].metadata

    def test_existing_metadata_is_preserved_alongside_the_redaction_flag(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        events = [MemoryEvent(content="foo@example.com", scope=ACME, metadata={"role": "user"})]

        out = redact_events_for_writeback(events, config)

        assert out[0].metadata["role"] == "user"
        assert out[0].metadata[PII_REDACTED_KEY] == ["Email"]

    def test_empty_batch_returns_empty(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        assert redact_events_for_writeback([], config) == []


# ---------------------------------------------------------------------------
# redact_context_for_read
# ---------------------------------------------------------------------------


class TestRedactContextForRead:
    def test_disabled_policy_is_a_no_op(self):
        config = MemoryProviderConfig(provider="co_located")
        assert redact_context_for_read("call 555-123-4567", config) == "call 555-123-4567"

    def test_empty_context_is_a_no_op_even_when_enabled(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        assert redact_context_for_read("", config) == ""

    def test_enabled_policy_masks_pii_in_the_assembled_context(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        redacted = redact_context_for_read("reach me at foo@example.com please", config)
        assert "@" not in redacted
        assert "please" in redacted

    def test_context_with_no_pii_is_returned_unchanged(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        assert redact_context_for_read("nothing sensitive here", config) == "nothing sensitive here"


# ---------------------------------------------------------------------------
# Wiring: MemoryProvider.assemble_context / aassemble_context (read/inject boundary)
# ---------------------------------------------------------------------------


class TestAssembleContextRedactsBeforeReturning:
    def test_pii_in_the_working_buffer_is_redacted_from_the_returned_context(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        provider = CoLocatedMemoryProvider(vector=FakeVector(), config=config)
        wm = WorkingMemory(scope=ACME)
        wm.note("contact the customer at foo@example.com")

        rendered = provider.assemble_context(ACME, wm)

        assert "@" not in rendered
        assert "contact the customer at" in rendered

    @pytest.mark.asyncio
    async def test_async_variant_mirrors_the_sync_one(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        provider = CoLocatedMemoryProvider(vector=FakeVector(), config=config)
        wm = WorkingMemory(scope=ACME)
        wm.note("ssn on file: 123-45-6789")

        rendered = await provider.aassemble_context(ACME, wm)

        assert "123-45-6789" not in rendered

    def test_pii_handling_off_by_default_leaves_context_untouched(self):
        provider = CoLocatedMemoryProvider(vector=FakeVector())
        wm = WorkingMemory(scope=ACME)
        wm.note("contact the customer at foo@example.com")

        rendered = provider.assemble_context(ACME, wm)

        assert "foo@example.com" in rendered

    def test_warns_when_masking_pushes_a_context_back_over_budget(self):
        """`PIIDetectionGuardrail.mask` replaces each match with `len`-preserving `*`s (same
        character count, not a longer placeholder like `[REDACTED_EMAIL]`) -- but same character
        count is not the same *token* count under BPE tokenization, so this can still tip an
        exactly-at-budget context over (round-1 review, finding #4). Exercises the warning helper
        directly with a real over-budget case rather than the full `assemble_context` pipeline,
        since which real strings actually cross a token boundary is tokenizer/environment
        dependent: locally neither `tiktoken` nor `tokenizers` is installed, so `count_text_tokens`
        falls back to `len(text) // 4`, but CI's `unit-drivers-absent` job pulls real `tiktoken`
        in transitively -- and repeated identical characters BPE-merge efficiently, so `"*" * 40`
        is 10 fallback tokens but only *1* real tiktoken token, which does not clear a
        `max_tokens=1` budget. `max_tokens=0` instead: any non-empty text is at least 1 token
        under either tokenizer, so `used > max_tokens` holds regardless of which one is active.

        Asserts on `egpt.memory.provider.log_warning` directly rather than `caplog` -- the shared
        `egpt.utils.log.logger` this ultimately reaches has a documented history of leaking
        mutated state (name and/or level) across test boundaries in a full-suite, xdist-parallel
        run (`tests/test_logger_isolation.py`), and `caplog` without a `logger=` filter only
        restores the *root* logger's level, not whatever name/level the leak left on the actual
        logger instance. Patching the call site sidesteps that global entirely."""
        from unittest.mock import patch

        from egpt.memory.provider import _warn_if_redaction_pushed_over_budget

        with patch("egpt.memory.provider.log_warning") as mock_warn:
            _warn_if_redaction_pushed_over_budget("*" * 40, max_tokens=0, model_id="gpt-4o")

        mock_warn.assert_called_once()
        assert "PII redaction pushed" in mock_warn.call_args[0][0]

    def test_no_warning_when_redaction_stays_within_budget(self):
        from unittest.mock import patch

        from egpt.memory.provider import _warn_if_redaction_pushed_over_budget

        with patch("egpt.memory.provider.log_warning") as mock_warn:
            _warn_if_redaction_pushed_over_budget("short", max_tokens=1000, model_id="gpt-4o")

        mock_warn.assert_not_called()

    def test_assemble_context_calls_the_warning_helper_only_when_redaction_actually_changed_the_string(self):
        """The identity check (`redacted is not context`) must gate the extra tokenization cost
        this warning requires -- pinning that a context with nothing to redact never reaches
        `_warn_if_redaction_pushed_over_budget` at all, not just that it never warns."""
        from unittest.mock import patch

        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        provider = CoLocatedMemoryProvider(vector=FakeVector(), config=config)
        wm = WorkingMemory(scope=ACME)
        wm.note("nothing to redact here")

        with patch("egpt.memory.provider._warn_if_redaction_pushed_over_budget") as mock_warn:
            provider.assemble_context(ACME, wm, max_tokens=1000)

        mock_warn.assert_not_called()

    def test_no_warning_when_pii_handling_is_off(self):
        """With PII handling off, `redact_context_for_read` is a no-op (`redacted is context`),
        so the warning helper must never even be reached -- see the class docstring above for why
        this asserts on the call site rather than `caplog`."""
        from unittest.mock import patch

        provider = CoLocatedMemoryProvider(vector=FakeVector())
        wm = WorkingMemory(scope=ACME)
        wm.note("foo@example.com")

        with patch("egpt.memory.provider._warn_if_redaction_pushed_over_budget") as mock_warn:
            provider.assemble_context(ACME, wm, max_tokens=5)

        mock_warn.assert_not_called()

    def test_a_persistent_record_written_before_pii_handling_was_turned_on_is_still_caught_on_read(self):
        """Decision 1 (issue #141): read/inject is an independent boundary, not a backstop for
        write-back -- a record written while PII handling was off for this tenant (or by code
        that predates SM-X5 entirely) still gets masked here before it reaches a prompt."""
        vector = FakeVector()
        off = CoLocatedMemoryProvider(vector=vector)
        off.write(ACME, [MemoryEvent(content="unredacted foo@example.com on file", scope=ACME)])

        on = CoLocatedMemoryProvider(
            vector=vector, config=MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        )
        wm = WorkingMemory(scope=ACME)

        rendered = on.assemble_context(ACME, wm, MemoryQuery(text="unredacted foo@example.com on file"))

        assert "foo@example.com" not in rendered
        assert "unredacted" in rendered


# ---------------------------------------------------------------------------
# Wiring: MemoryProvider.write/awrite (write-back boundary) -- both adapters
# ---------------------------------------------------------------------------


class TestWriteRedactsBeforeStorage:
    """Redaction runs inside ``write``/``awrite`` themselves (mirroring exactly where SM-X4's
    ``memory_span`` tracing was wired -- per-adapter, not on the abstract ``MemoryProvider``
    base), so it applies to *every* caller that reaches storage through this method: the agent
    and team run loops' write-back hooks (`agent/_managers.py`, `team/_managers.py`),
    `WorkingMemory.offload`/`aoffload`, and a caller hitting the seam directly, as this test
    does. `test_memory_provider_hooks.py`/`test_writeback.py` already cover write-back's own
    dispatch/gating with a bare `MagicMock()` provider (a safe no-op here too, per
    `TestPiiGuardrailForConfig.test_none_for_a_mock_config`) -- this class only pins the
    redaction landing at the one method both paths funnel through.
    """

    def test_co_located_write_redacts_before_it_reaches_the_vector_substrate(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        provider = CoLocatedMemoryProvider(vector=FakeVector(), config=config)

        provider.write(ACME, [MemoryEvent(content="reach me at foo@example.com", scope=ACME)])

        stored = list(provider.vector.rows["acme"].values())
        assert len(stored) == 1
        assert "@" not in stored[0].content
        assert stored[0].metadata[PII_REDACTED_KEY] == ["Email"]

    @pytest.mark.asyncio
    async def test_co_located_awrite_mirrors_the_sync_path(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        provider = CoLocatedMemoryProvider(vector=FakeVector(), config=config)

        await provider.awrite(ACME, [MemoryEvent(content="ssn on file: 123-45-6789", scope=ACME)])

        stored = list(provider.vector.rows["acme"].values())
        assert "123-45-6789" not in stored[0].content
        assert stored[0].metadata[PII_REDACTED_KEY] == ["SSN"]

    def test_no_pii_policy_leaves_stored_content_untouched(self):
        """Default (unconfigured) config -- pinning that this change is a no-op for every
        deployment that has not opted in."""
        provider = CoLocatedMemoryProvider(vector=FakeVector())

        provider.write(ACME, [MemoryEvent(content="reach me at foo@example.com", scope=ACME)])

        stored = list(provider.vector.rows["acme"].values())
        assert stored[0].content == "reach me at foo@example.com"
        assert PII_REDACTED_KEY not in stored[0].metadata

    def test_working_memory_offload_is_covered_by_the_same_seam(self):
        """`WorkingMemory.offload` reaches storage through `provider.write` too -- not a second
        call site to wire, just a caller of the one this class already pins. Needs a real (fake)
        model on `summary_manager`: with the default model-less one, `offload` always declines
        and writes nothing (see its own docstring), so there would be nothing here to redact."""
        from egpt.models.message import Message

        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        provider = CoLocatedMemoryProvider(vector=FakeVector(), config=config)
        wm = WorkingMemory(
            scope=ACME,
            max_messages=1,
            keep_after_offload=0,
            summary_manager=SessionSummaryManager(model=_fake_model("what happened")),
        )
        wm.add_message(Message(role="user", content="call me at 555-123-4567"))
        wm.add_message(Message(role="user", content="and again"))

        wm.offload(provider=provider)

        stored = list(provider.vector.rows["acme"].values())
        assert stored, "offload should have written the summarized overflow through the provider"
        assert not any("555-123-4567" in r.content for r in stored)

    def test_agent_run_loop_writeback_lands_redacted(self):
        """End-to-end through the real dispatch hook, not a mocked provider -- proves the run
        loop's write-back path (which calls `provider.write` on a background thread/task) gets
        the same redaction as a direct call, with nothing agent-specific to wire."""
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import MagicMock

        from egpt.agent._managers import start_memory_writeback_future
        from egpt.models.message import Message
        from egpt.run.base import RunContext

        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        provider = CoLocatedMemoryProvider(vector=FakeVector(), config=config)

        agent = MagicMock()
        agent.id = "agent-1"
        agent.memory_provider = provider
        agent.background_executor = ThreadPoolExecutor(max_workers=1)

        run_context = RunContext(run_id="r1", session_id="s1", user_id="u1", tenant_id="acme")
        run_response = MagicMock()
        run_response.tools = None
        run_response.messages = [Message(role="assistant", content="reach me at foo@example.com")]

        future = start_memory_writeback_future(agent, run_context, run_response, user_id="user-1")
        assert future is not None
        future.result(timeout=5)

        stored = list(provider.vector.rows["acme"].values())
        assert len(stored) == 1
        assert "@" not in stored[0].content
        assert stored[0].metadata[PII_REDACTED_KEY] == ["Email"]


# ---------------------------------------------------------------------------
# write_tier/awrite_tier redact too, not just write/awrite (round-1 review, finding #1)
# ---------------------------------------------------------------------------


class TestWriteTierRedactsBeforeStorage:
    """`write_tier`/`awrite_tier` are independently public, abstract methods on `MemoryProvider`
    (this repo's own `test_provider_seed_bridge.py` and several cookbooks call them directly) --
    redacting only inside `write`/`awrite` left every direct caller of `write_tier`/`awrite_tier`
    with zero PII protection even when a tenant's policy opted in. Pinned here against
    `SeedMemoryProvider`, since `CoLocatedMemoryProvider.write_tier` already delegated to
    `self.write` and was never the gap."""

    def _config(self) -> MemoryProviderConfig:
        return MemoryProviderConfig(
            provider="seed",
            options={"single_tenant": True, "tenant_id": ACME.tenant_id},
            pii={"enabled": True},
        )

    def test_write_tier_called_directly_still_redacts(self):
        provider = SeedMemoryProvider(manager=FakeManager(), config=self._config())

        provider.write_tier(
            ACME,
            MemoryTier.SEMANTIC,
            [MemoryEvent(content="reach me at foo@example.com", tier=MemoryTier.SEMANTIC, scope=ACME)],
        )

        assert len(provider.manager.added) == 1
        assert "@" not in provider.manager.added[0].memory

    @pytest.mark.asyncio
    async def test_awrite_tier_called_directly_still_redacts(self):
        provider = SeedMemoryProvider(manager=FakeManager(), config=self._config())

        await provider.awrite_tier(
            ACME,
            MemoryTier.SEMANTIC,
            [MemoryEvent(content="ssn on file: 123-45-6789", tier=MemoryTier.SEMANTIC, scope=ACME)],
        )

        assert len(provider.manager.added) == 1
        assert "123-45-6789" not in provider.manager.added[0].memory

    def test_write_still_redacts_when_routed_through_write_tier(self):
        """`write()` no longer redacts directly -- it fans out through `write_tier` per group, so
        this pins that the end-to-end behavior every existing caller of `write()` relies on is
        unchanged even though the redaction call moved."""
        provider = SeedMemoryProvider(manager=FakeManager(), config=self._config())

        provider.write(ACME, [MemoryEvent(content="reach me at foo@example.com", tier=MemoryTier.SEMANTIC, scope=ACME)])

        assert len(provider.manager.added) == 1
        assert "@" not in provider.manager.added[0].memory

    def test_no_pii_policy_leaves_write_tier_untouched(self):
        config = MemoryProviderConfig(provider="seed", options={"single_tenant": True, "tenant_id": ACME.tenant_id})
        provider = SeedMemoryProvider(manager=FakeManager(), config=config)

        provider.write_tier(
            ACME,
            MemoryTier.SEMANTIC,
            [MemoryEvent(content="reach me at foo@example.com", tier=MemoryTier.SEMANTIC, scope=ACME)],
        )

        assert provider.manager.added[0].memory == "reach me at foo@example.com"


# ---------------------------------------------------------------------------
# consolidate/aconsolidate redact too, not just write/awrite (round-1 review, finding #2)
# ---------------------------------------------------------------------------


def _co_located_provider(**config_kwargs):
    from tests.unit.memory._fakes import FakeGraph

    vector, graph = FakeVector(), FakeGraph()
    config = MemoryProviderConfig(provider="co_located", **config_kwargs)
    return CoLocatedMemoryProvider(vector=vector, graph=graph, config=config), vector, graph


class TestConsolidateRedactsDerivedWrites:
    """`consolidate`/`aconsolidate` read raw episodes via `vector.scan` -- bypassing
    `write`/`awrite`'s redaction entirely, since these rows already exist in storage -- and derive
    facts written straight to the vector/graph substrates. Neither path was reachable by
    write-back redaction until now."""

    def test_a_fact_distilled_from_a_raw_episode_is_redacted_before_the_semantic_write(self):
        """`vector.scan` inside `consolidate` reads whatever is already in storage -- including a
        row written before this tenant's policy turned PII handling on, the same read-boundary
        justification `security/pii.py`'s module docstring gives (`assemble_context` catches that
        case for reads; this class pins that `consolidate`'s own derived writes do too). Simulated
        here by writing raw (policy off), then consolidating with a provider that has it on --
        `write` and `consolidate` share `self.config`, so a real deployment sees exactly this
        sequence the moment an operator flips the flag."""
        p, vector, _ = _co_located_provider()
        p.write(ACME, [MemoryEvent(content="reach me at foo@example.com", scope=ACME)])
        strict, _, _ = _co_located_provider(pii={"enabled": True})
        strict.vector = vector

        strict.consolidate(ACME)

        semantic_rows = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
        assert semantic_rows, "consolidate should have distilled at least one semantic fact"
        assert not any("@" in r.content for r in semantic_rows)

    @pytest.mark.asyncio
    async def test_aconsolidate_mirrors_the_sync_path(self):
        p, vector, _ = _co_located_provider()
        await p.awrite(ACME, [MemoryEvent(content="ssn on file: 123-45-6789", scope=ACME)])
        strict, _, _ = _co_located_provider(pii={"enabled": True})
        strict.vector = vector

        await strict.aconsolidate(ACME)

        semantic_rows = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
        assert semantic_rows
        assert not any("123-45-6789" in r.content for r in semantic_rows)

    def test_no_pii_policy_leaves_consolidated_facts_untouched(self):
        p, vector, _ = _co_located_provider()
        p.write(ACME, [MemoryEvent(content="reach me at foo@example.com", scope=ACME)])

        p.consolidate(ACME)

        semantic_rows = [r for r in vector.rows["acme"].values() if r.tier is MemoryTier.SEMANTIC]
        assert semantic_rows
        assert any("@" in r.content for r in semantic_rows)

    def test_redact_records_for_writeback_matches_the_event_helpers_contract(self):
        """Pure-function pin for the MemoryRecord-flavored sibling of
        `redact_events_for_writeback` -- same no-op/mask/tag/pass-through-by-reference contract,
        proven independent of any provider."""
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})
        clean = MemoryRecord(id="r1", tier=MemoryTier.SEMANTIC, content="no pii here", scope=ACME)
        dirty = MemoryRecord(id="r2", tier=MemoryTier.SEMANTIC, content="call 555-123-4567", scope=ACME)

        redacted = redact_records_for_writeback([clean, dirty], config)

        assert redacted[0] is clean
        assert "555-123-4567" not in redacted[1].content
        assert redacted[1].metadata[PII_REDACTED_KEY] == ["Phone"]

    def test_redact_records_for_writeback_is_a_no_op_when_pii_handling_is_off(self):
        assert (
            redact_records_for_writeback(
                [MemoryRecord(id="r1", tier=MemoryTier.SEMANTIC, content="call 555-123-4567", scope=ACME)], None
            )[0].content
            == "call 555-123-4567"
        )

    def test_redact_text_for_writeback_masks_a_bare_string(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})

        assert redact_text_for_writeback("call 555-123-4567", config) != "call 555-123-4567"
        assert "555-123-4567" not in redact_text_for_writeback("call 555-123-4567", config)

    def test_redact_text_for_writeback_passes_through_none_and_empty(self):
        config = MemoryProviderConfig(provider="co_located", pii={"enabled": True})

        assert redact_text_for_writeback(None, config) is None
        assert redact_text_for_writeback("", config) == ""

    def test_redact_text_for_writeback_is_a_no_op_when_pii_handling_is_off(self):
        assert redact_text_for_writeback("call 555-123-4567", None) == "call 555-123-4567"


# ---------------------------------------------------------------------------
# A guardrail exception is fail-closed, not swallowed or left unwrapped
# (round-1 review, finding #5)
# ---------------------------------------------------------------------------


class _BrokenGuardrail:
    """A guardrail whose detect()/mask() always raises -- stands in for a non-string
    MemoryEvent.content reaching PIIDetectionGuardrail's real regex path, or a bug in a
    tenant-supplied custom guardrail."""

    def detect(self, content):
        raise ValueError("guardrail is broken")

    def mask(self, content, pii_types=None):
        raise ValueError("guardrail is broken")


def _broken_guardrail_config(**kwargs) -> MemoryProviderConfig:
    return MemoryProviderConfig(provider="co_located", pii={"enabled": True, "guardrail": _BrokenGuardrail()}, **kwargs)


class TestGuardrailExceptionIsFailClosed:
    def test_redact_events_for_writeback_raises_memory_operation_error(self):
        from egpt.memory.errors import MemoryOperationError

        with pytest.raises(MemoryOperationError, match="could not be verified safe to store"):
            redact_events_for_writeback([MemoryEvent(content="hello", scope=ACME)], _broken_guardrail_config())

    def test_redact_records_for_writeback_raises_memory_operation_error(self):
        from egpt.memory.errors import MemoryOperationError

        with pytest.raises(MemoryOperationError):
            redact_records_for_writeback(
                [MemoryRecord(id="r1", tier=MemoryTier.SEMANTIC, content="hello", scope=ACME)],
                _broken_guardrail_config(),
            )

    def test_redact_text_for_writeback_raises_memory_operation_error(self):
        from egpt.memory.errors import MemoryOperationError

        with pytest.raises(MemoryOperationError):
            redact_text_for_writeback("hello", _broken_guardrail_config())

    def test_write_refuses_bare_rather_than_storing_unverified_content(self):
        """A guardrail crash means this content's PII status could not be verified -- the write
        must refuse, not silently store it or crash with an unrelated exception type. Bare, not
        wrapped in PartialWriteError: this refusal happens before any store is touched (the same
        pre-fan-out rule every other write refusal in this module follows), so there is nothing
        to reconcile."""
        from egpt.memory.errors import MemoryOperationError

        provider = CoLocatedMemoryProvider(vector=FakeVector(), config=_broken_guardrail_config())

        with pytest.raises(MemoryOperationError):
            provider.write(ACME, [MemoryEvent(content="hello", scope=ACME)])

        assert not list(provider.vector.rows.get("acme", {}).values())

    @pytest.mark.asyncio
    async def test_awrite_refuses_bare_too(self):
        from egpt.memory.errors import MemoryOperationError

        provider = CoLocatedMemoryProvider(vector=FakeVector(), config=_broken_guardrail_config())

        with pytest.raises(MemoryOperationError):
            await provider.awrite(ACME, [MemoryEvent(content="hello", scope=ACME)])

    def test_write_tier_called_directly_also_refuses(self):
        """The same fail-closed behavior applies to SeedMemoryProvider's write_tier, the direct
        entry point finding #1 fixed -- a broken guardrail must refuse there too, not just when
        reached through write()."""
        from egpt.memory.errors import MemoryOperationError

        config = MemoryProviderConfig(
            provider="seed",
            options={"single_tenant": True, "tenant_id": ACME.tenant_id},
            pii={"enabled": True, "guardrail": _BrokenGuardrail()},
        )
        provider = SeedMemoryProvider(manager=FakeManager(), config=config)

        with pytest.raises(MemoryOperationError):
            provider.write_tier(
                ACME, MemoryTier.SEMANTIC, [MemoryEvent(content="hello", tier=MemoryTier.SEMANTIC, scope=ACME)]
            )

        assert provider.manager.added == []
