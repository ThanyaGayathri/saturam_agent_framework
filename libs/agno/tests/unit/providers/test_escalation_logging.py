"""Escalation events are observable and tenant-attributed (EGPT-3.5).

Epic 4 consumes these records, so what is asserted here is the *payload*, not just that
something was logged: an operator debugging a cost spike needs to know which tenant escalated,
away from what, to what, and why. A test that only checked "a line appeared" would let the
fields Epic 4 depends on be renamed or dropped silently.
"""

from __future__ import annotations

import logging

import pytest

from egpt.providers.escalation import ESCALATE_METADATA_KEY, EscalatingModelProvider
from egpt.providers.events import ESCALATION_LOG_FIELD, EscalationEvent, log_escalation_event
from egpt.providers.model_provider import ModelResolveRequest
from tests.unit.providers.test_escalation import FRONTIER, SMALL, FakeDetailedProvider

LOGGER_NAME = "egpt"


@pytest.fixture
def primary() -> FakeDetailedProvider:
    return FakeDetailedProvider("mode-a", SMALL)


@pytest.fixture
def frontier() -> FakeDetailedProvider:
    return FakeDetailedProvider("mode-b", FRONTIER)


@pytest.fixture
def provider(primary: FakeDetailedProvider, frontier: FakeDetailedProvider) -> EscalatingModelProvider:
    return EscalatingModelProvider(primary=primary, escalation_target=frontier)


def _escalation_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Only the records carrying a structured escalation payload."""
    return [r for r in caplog.records if hasattr(r, ESCALATION_LOG_FIELD)]


class TestEscalationEvent:
    def test_to_dict_is_json_safe(self):
        event = EscalationEvent(
            reason="vision_unsupported",
            escalated_from="mode-a",
            target="mode-b",
            escalation_level=1,
            tenant_id="acme",
        )
        payload = event.to_dict()
        assert all(v is None or isinstance(v, (str, int)) for v in payload.values())

    def test_payload_names_the_event(self):
        """Epic 4 routes on this discriminator, so it must be present and stable."""
        event = EscalationEvent(reason="r", escalated_from="a", target="b", escalation_level=1)
        assert event.to_dict()["event"] == "model_escalation"

    def test_event_is_immutable(self):
        event = EscalationEvent(reason="r", escalated_from="a", target="b", escalation_level=1)
        with pytest.raises(Exception):
            event.reason = "changed"  # type: ignore[misc]

    def test_log_escalation_event_emits_exactly_one_record(self, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        log_escalation_event(EscalationEvent(reason="r", escalated_from="a", target="b", escalation_level=1))
        assert len(_escalation_records(caplog)) == 1


class TestEscalationLogging:
    def test_escalation_emits_one_record(self, provider, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider.resolve(ModelResolveRequest(requires_vision=True))
        assert len(_escalation_records(caplog)) == 1

    def test_record_names_both_providers_and_the_reason(self, provider, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider.resolve(ModelResolveRequest(requires_vision=True))
        record = _escalation_records(caplog)[0]
        payload = getattr(record, ESCALATION_LOG_FIELD)
        assert payload["escalated_from"] == "mode-a"
        assert payload["target"] == "mode-b"
        assert payload["reason"] == "vision_unsupported"

    def test_message_is_readable_without_a_structured_handler(self, provider, caplog):
        """The default handler still has to produce a sentence an operator can read."""
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider.resolve(ModelResolveRequest(requires_vision=True))
        message = _escalation_records(caplog)[0].getMessage()
        assert "mode-a" in message
        assert "mode-b" in message
        assert "vision_unsupported" in message

    def test_record_is_tenant_attributed(self, provider, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        request = ModelResolveRequest(
            requires_vision=True,
            tenant_id="acme",
            user_id="u-1",
            session_id="s-1",
            agent_id="a-1",
        )
        provider.resolve(request)
        payload = getattr(_escalation_records(caplog)[0], ESCALATION_LOG_FIELD)
        assert payload["tenant_id"] == "acme"
        assert payload["user_id"] == "u-1"
        assert payload["session_id"] == "s-1"
        assert payload["agent_id"] == "a-1"

    def test_record_carries_the_resolved_model_id(self, provider, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider.resolve(ModelResolveRequest(requires_vision=True))
        payload = getattr(_escalation_records(caplog)[0], ESCALATION_LOG_FIELD)
        assert payload["model_id"] == "mode-b-model"

    def test_record_carries_the_escalation_level(self, provider, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider.resolve(ModelResolveRequest(requires_vision=True))
        payload = getattr(_escalation_records(caplog)[0], ESCALATION_LOG_FIELD)
        assert payload["escalation_level"] == 1

    def test_no_record_when_the_request_stays_on_the_primary(self, provider, caplog):
        """The quiet path must stay quiet: most traffic does not escalate."""
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider.resolve(ModelResolveRequest())
        assert _escalation_records(caplog) == []

    def test_no_record_when_at_the_escalation_ceiling(self, provider, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider.resolve(ModelResolveRequest(requires_vision=True, escalation_level=1, max_escalations=1))
        assert _escalation_records(caplog) == []

    def test_reason_reflects_the_rule_that_fired(self, provider, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider.resolve(ModelResolveRequest(estimated_input_tokens=20_000))
        payload = getattr(_escalation_records(caplog)[0], ESCALATION_LOG_FIELD)
        assert payload["reason"] == "input_exceeds_context_window"

    def test_explicit_flag_is_logged_as_such(self, provider, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider.resolve(ModelResolveRequest(metadata={ESCALATE_METADATA_KEY: True}))
        payload = getattr(_escalation_records(caplog)[0], ESCALATION_LOG_FIELD)
        assert payload["reason"] == "explicit_policy_flag"

    def test_each_escalation_logs_once(self, provider, caplog):
        """Two escalating requests produce two records, not one and not four."""
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        provider.resolve(ModelResolveRequest(requires_vision=True))
        provider.resolve(ModelResolveRequest(estimated_input_tokens=20_000))
        assert len(_escalation_records(caplog)) == 2

    def test_logging_does_not_swallow_the_result(self, provider, caplog):
        caplog.set_level(logging.INFO, logger=LOGGER_NAME)
        result = provider.resolve_detailed(ModelResolveRequest(requires_vision=True))
        assert result.model.id == "mode-b-model"
        assert result.reason == "vision_unsupported"
