"""Unit tests for ManagedPrompt dataclass (EGPT-4.4)."""

from __future__ import annotations

from egpt.prompts.managed import ManagedPrompt


class TestManagedPromptDefaults:
    """Verify default field values."""

    def test_minimal_construction(self):
        ref = ManagedPrompt(name="greeting")
        assert ref.name == "greeting"
        assert ref.label is None
        assert ref.variables == {}
        assert ref.fallback is None

    def test_full_construction(self):
        ref = ManagedPrompt(
            name="support-v1",
            label="production",
            variables={"company": "Acme"},
            fallback="You are a helpful agent for {company}.",
        )
        assert ref.name == "support-v1"
        assert ref.label == "production"
        assert ref.variables == {"company": "Acme"}
        assert ref.fallback == "You are a helpful agent for {company}."

    def test_frozen(self):
        ref = ManagedPrompt(name="test")
        try:
            ref.name = "other"  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass

    def test_equality(self):
        a = ManagedPrompt(name="x", label="prod")
        b = ManagedPrompt(name="x", label="prod")
        assert a == b
