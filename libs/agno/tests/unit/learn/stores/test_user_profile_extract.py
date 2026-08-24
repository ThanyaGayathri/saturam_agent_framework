"""``UserProfileStore.extract_profile``/``aextract_profile``: storage-free extraction, the
piece SM-S3 (#120) adds so a caller with its own tenant-scoped persistence
(``egpt.memory.semantic.profile.SemanticProfileStore``) can reuse this store's schema and model
call without going through ``self.db`` -- which, unlike everything else on this class, these two
methods never touch.
"""

from egpt.learn.config import UserProfileConfig
from egpt.learn.schemas import UserProfile
from egpt.learn.stores.user_profile import UserProfileStore
from egpt.models.message import Message
from egpt.models.response import ModelResponse, ToolExecution


class _FakeModel:
    """Simulates the model calling the single exposed tool with ``calls_with`` -- or calling
    nothing when ``calls_with`` is falsy, matching a conversation with no profile-worthy content.
    """

    def __init__(self, calls_with=None):
        self.calls_with = calls_with or {}

    def _invoke(self, tools):
        if not tools or not self.calls_with:
            return ModelResponse(content="No updates needed", tool_executions=[])
        tool = tools[0]
        result = tool.entrypoint(**self.calls_with)
        return ModelResponse(
            content=result,
            tool_executions=[ToolExecution(tool_call_id="c1", tool_name=tool.name, result=result)],
        )

    def response(self, messages, tools=None):
        return self._invoke(tools or [])

    async def aresponse(self, messages, tools=None):
        return self._invoke(tools or [])


def profile_store(calls_with=None):
    return UserProfileStore(config=UserProfileConfig(model=_FakeModel(calls_with)))


def test_no_model_returns_none():
    store = UserProfileStore(config=UserProfileConfig(model=None))
    assert store.extract_profile([Message(role="user", content="I'm Ada")], user_id="ada") is None


def test_blank_conversation_returns_none():
    store = profile_store(calls_with={"name": "Ada"})
    assert store.extract_profile([Message(role="user", content="   ")], user_id="ada") is None


def test_empty_messages_returns_none():
    store = profile_store(calls_with={"name": "Ada"})
    assert store.extract_profile([], user_id="ada") is None


def test_extracted_field_lands_on_a_fresh_profile():
    store = profile_store(calls_with={"name": "Ada Lovelace"})
    result = store.extract_profile([Message(role="user", content="I'm Ada Lovelace")], user_id="ada")
    assert result is not None
    assert result.user_id == "ada"
    assert result.name == "Ada Lovelace"


def test_no_tool_call_returns_none_not_an_unchanged_copy():
    """A model that extracted nothing must not be conflated with one that wrote back the same
    values -- the caller (the consolidation hook) uses `None` to skip the persistence write."""
    store = profile_store(calls_with=None)
    result = store.extract_profile([Message(role="user", content="what's the weather?")], user_id="ada")
    assert result is None


def test_extraction_never_touches_db():
    store = profile_store(calls_with={"name": "Ada"})
    assert store.db is None
    result = store.extract_profile([Message(role="user", content="I'm Ada")], user_id="ada")
    assert result is not None
    assert store.db is None


def test_existing_profile_is_deep_copied_not_mutated_in_place():
    store = profile_store(calls_with={"preferred_name": "Ada"})
    existing = UserProfile(user_id="ada", name="Ada Lovelace")

    result = store.extract_profile(
        [Message(role="user", content="call me Ada")], user_id="ada", existing_profile=existing
    )

    assert result is not existing
    assert existing.preferred_name is None  # the caller's object is untouched
    assert result.preferred_name == "Ada"
    assert result.name == "Ada Lovelace"  # unrelated fields carry over


async def test_async_extraction_makes_the_same_decisions_as_sync():
    store = profile_store(calls_with={"name": "Ada"})

    result = await store.aextract_profile([Message(role="user", content="I'm Ada")], user_id="ada")

    assert result is not None
    assert result.name == "Ada"
    assert store.db is None


async def test_async_no_tool_call_returns_none():
    store = profile_store(calls_with=None)
    result = await store.aextract_profile([Message(role="user", content="hi")], user_id="ada")
    assert result is None
