"""The two playground defects a review found, pinned so they cannot come back.

Both were silent: one returned a confident answer built on another user's memories, the other
sent customer data to a third party. Neither raised, neither logged, and neither had a test.
"""

from egpt_retail.app.components import _verified_user_id
from egpt_retail.app.settings import RetailSettings
from egpt_retail.playground.modes import PlaygroundMode, resolve_mode_scope


class _Trusted:
    def __init__(self, user_id=None):
        self.user_id = user_id


class _Ctx:
    """Stands in for `RequestContext`: a caller-supplied `user_id` and a verified `trusted`."""

    def __init__(self, user_id=None, trusted_user_id=None):
        self.user_id = user_id
        self.trusted = _Trusted(trusted_user_id)


def test_a_form_supplied_user_id_never_scopes_a_memory_read():
    """`RequestContext.user_id` is "From form field or request.state" -- not verified.

    With authorization off, or a token carrying no `sub`, the raw multipart `user_id` field
    survives to the factory. Trusting it lets `curl -F 'user_id=victim'` read that person's
    memories: cross-user, which is what MEMORY_SCOPE.md's read table exists to prevent.
    """
    assert _verified_user_id(_Ctx(user_id="victim@corp")) is None


def test_a_verified_identity_is_used():
    """`ctx.trusted` is documented as populated by verified middleware only."""
    assert (
        _verified_user_id(_Ctx(user_id="ignored", trusted_user_id="real@corp"))
        == "real@corp"
    )


def test_an_unidentified_caller_drops_the_memory_leg_rather_than_widening_it():
    """No memory is the honest answer for an unidentified caller. "Everyone's" is the one
    answer that must never come back, so the absent case must not fall through to a
    tenant-wide read."""
    scope = resolve_mode_scope(
        PlaygroundMode.KNOWLEDGE, None, settings=RetailSettings(), user_id=None
    )
    assert scope.refused is None, "the other three legs still answer without memory"
    assert [t.__name__ for t in scope.tools] == ["search_knowledge"]


def test_the_sql_sub_agent_is_given_an_explicit_model():
    """An agent built with no model and no provider falls through to
    `get_model("openai-responses:gpt-5.4")`, which `agent/_init.py` says "would ship tenant
    prompts to api.openai.com". This tool handles the grounding block -- personal-data field
    names and compliance tags included -- and up to `row_cap` rows of real customer data.

    Asserted through the resolver rather than by reading the closure: what matters is that the
    model reaches `_data_scope`, which is the only way it can reach the provider.
    """
    sentinel = object()
    scope = resolve_mode_scope(
        PlaygroundMode.DATA, None, settings=RetailSettings(), model=sentinel
    )
    assert [t.__name__ for t in scope.tools] == ["read_kpi_data", "query_warehouse"]
    # The closure captures it; `__closure__` is how we see it without running a query.
    captured = [
        cell.cell_contents
        for tool in scope.tools
        for cell in (tool.__closure__ or ())
        if cell.cell_contents is sentinel
    ]
    assert captured, (
        "query_warehouse must capture the resolved model, not build an agent without one"
    )
