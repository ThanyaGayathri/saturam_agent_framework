"""The two playground defects a review found, pinned so they cannot come back.

Both were silent: one returned a confident answer built on another user's memories, the other
sent customer data to a third party. Neither raised, neither logged, and neither had a test.
"""

from egpt.factory import RequestContext
from egpt.factory.utils import TrustedContext

from egpt_retail.app.components import _verified_user_id
from egpt_retail.app.settings import RetailSettings
from egpt_retail.playground.modes import PlaygroundMode, resolve_mode_scope


# The REAL types, deliberately. The first version of this file defined its own `_Trusted`
# class carrying a `user_id` attribute -- a shape `TrustedContext` does not have -- so the
# test passed against a fiction while the production path returned None on every request.
# A stub that is kinder than reality is worse than no test: it reports a working feature and
# a closed hole, and neither was true.


def _ctx(*, form_user_id=None, sub=None, authorization_enabled=True):
    """A `RequestContext` shaped exactly as the run route builds one."""

    class _State:
        pass

    state = _State()
    state.authorization_enabled = authorization_enabled

    class _Request:
        pass

    request = _Request()
    request.state = state
    return RequestContext(
        user_id=form_user_id,
        trusted=TrustedContext(claims={"sub": sub} if sub else {}),
        request=request,
    )


def test_a_form_supplied_user_id_never_scopes_a_memory_read():
    """`RequestContext.user_id` is "From form field or request.state" -- not verified.

    With authorization off, or a token carrying no `sub`, the raw multipart `user_id` field
    survives to the factory. Trusting it lets `curl -F 'user_id=victim'` read that person's
    memories: cross-user, which is what MEMORY_SCOPE.md's read table exists to prevent.
    """
    assert _verified_user_id(_ctx(form_user_id="victim@corp")) is None


def test_a_verified_subject_claim_is_used():
    """The identity comes from the JWT's `sub`, which AuthMiddleware places in
    `TrustedContext.claims` only after the token verified."""
    assert (
        _verified_user_id(_ctx(form_user_id="ignored", sub="real@corp")) == "real@corp"
    )


def test_trusted_context_really_has_no_user_id_attribute():
    """Pins why this is read from `claims`. If `TrustedContext` ever grows a `user_id`, this
    fails and whoever adds it can decide deliberately which source wins."""
    assert not hasattr(TrustedContext(), "user_id")


def test_authorization_off_resolves_to_the_single_local_caller():
    """A legitimate deployment posture, not an attack: with authorization off there is one
    caller, and `_owner` already uses "local" for it. Chosen from configuration, never from
    the request."""
    assert (
        _verified_user_id(_ctx(form_user_id="anything", authorization_enabled=False))
        == "local"
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
