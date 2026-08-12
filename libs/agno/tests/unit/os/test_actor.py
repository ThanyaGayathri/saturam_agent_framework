"""Reading the acting principal off a request (EGPT-2.6).

``egpt.os.actor`` is the sibling of ``egpt.os.tenancy``, and the interesting assertions are the
places the two deliberately differ. Tenancy has a ``require_tenant_id`` gate because there
``None`` means *unscoped*, which is the widest possible access; an actor of ``None`` grants
nothing, so there is no gate and ``None`` has to flow through rather than be refused.
"""

from types import SimpleNamespace

import pytest

from egpt.os.actor import current_actor, get_actor


def _request(**state):
    """A stand-in carrying only what current_actor reads."""
    return SimpleNamespace(state=SimpleNamespace(**state))


class TestCurrentActor:
    def test_it_returns_the_authenticated_subject(self):
        assert current_actor(_request(user_id="alice")) == "alice"

    def test_a_service_account_principal_passes_through(self):
        """``sa:`` is a reserved namespace the middleware refuses to let a JWT claim, so it can
        be trusted as an attribution."""
        assert current_actor(_request(user_id="sa:ci-bot")) == "sa:ci-bot"

    def test_the_scheduler_passes_through(self):
        assert current_actor(_request(user_id="__scheduler__")) == "__scheduler__"

    def test_an_unauthenticated_request_has_no_actor(self):
        """The attribute genuinely does not exist on an unauthenticated request, which is why
        the read is a getattr with a default rather than an attribute access."""
        assert current_actor(_request()) is None

    def test_an_explicit_none_is_none(self):
        assert current_actor(_request(user_id=None)) is None

    @pytest.mark.parametrize("value", ["", 42, [], {"sub": "alice"}])
    def test_empty_and_non_string_values_resolve_to_none(self, value):
        """Belt-and-braces for state populated by something other than the middleware --
        mirroring current_tenant_id, so a junk principal is never recorded as the actor."""
        assert current_actor(_request(user_id=value)) is None

    def test_no_request_at_all_is_none(self):
        assert current_actor(None) is None


class TestGetActorDependency:
    async def test_it_delegates_to_current_actor(self):
        assert await get_actor(_request(user_id="alice")) == "alice"

    async def test_it_returns_none_rather_than_raising(self):
        """The difference from require_tenant_id: no principal is a legitimate steady state
        (an open instance, a programmatic writer), so this never refuses a request."""
        assert await get_actor(_request()) is None
