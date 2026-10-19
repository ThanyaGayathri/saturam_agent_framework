"""The default system prompt must be byte-stable between runs.

Provider prompt caching matches on a prefix, so a system prompt that varies between two
identical requests turns every call into a cache miss. These tests pin the default behaviour
and document which opt-in flags deliberately break it.
"""

import time

from egpt.agent import Agent
from egpt.agent._messages import get_system_message
from egpt.integrations.models.openai.chat import OpenAIChat
from egpt.run.base import RunContext
from egpt.session.agent import AgentSession


def _system_prompt(**agent_kwargs) -> str:
    agent = Agent(
        id="cache-stability",
        description="A test agent.",
        instructions=["Be brief.", "Be kind."],
        model=OpenAIChat(id="gpt-5.4-mini", api_key="test"),
        **agent_kwargs,
    )
    run_context = RunContext(run_id="run", session_id="session", session_state={})
    message = get_system_message(agent, AgentSession(session_id="session"), run_context)
    assert message is not None and isinstance(message.content, str)
    return message.content


def test_default_system_prompt_is_identical_across_builds() -> None:
    assert _system_prompt() == _system_prompt()


def test_adding_the_datetime_makes_the_prompt_vary() -> None:
    first = _system_prompt(add_datetime_to_context=True)
    time.sleep(0.01)
    second = _system_prompt(add_datetime_to_context=True)
    assert first != second
