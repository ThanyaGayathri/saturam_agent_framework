"""The verified-tenant RunContext builder (EGPT-3.6 unit 1b).

A tenant id selects a ``ModelPolicy`` -- the backend, the adapter, and a ``config`` blob that may
carry a base_url or credentials -- and, once enforcement lands, a spend budget. So the only thing
these tests really check is that it cannot be named by the client.

PR #46 closed exactly this spoofability inside ``resolve_tenant_id``; the builder exists so that
fix cannot be re-opened one layer up by a route accepting ``tenant_id`` from a form field.
"""

from __future__ import annotations

from egpt.os.utils import build_run_context_for_request


class _State:
    def __init__(self, claims=None, user_id=None):
        self.claims = claims or {}
        self.user_id = user_id


class _Request:
    def __init__(self, claims=None, user_id=None):
        self.state = _State(claims, user_id)


class TestTheTenantCannotBeNamedByTheClient:
    def test_an_unverified_user_id_yields_no_tenant(self):
        """The spoof attempt: `user_id` arrives from a `Form(None)` field, so a caller can put
        anything there. Without a matching middleware-stamped identity it must not become a tenant."""
        ctx = build_run_context_for_request(
            _Request(user_id=None),  # nothing verified this request
            run_id="r",
            session_id="s",
            user_id="attacker-chosen-tenant",
        )

        assert ctx.tenant_id is None

    def test_a_user_id_matching_the_verified_identity_is_accepted(self):
        """The fallback that actually runs today: `user_id` is the de-facto tenant axis for a
        verified caller."""
        ctx = build_run_context_for_request(
            _Request(user_id="u1"),
            run_id="r",
            session_id="s",
            user_id="u1",
        )

        assert ctx.tenant_id == "u1"

    def test_a_verified_claim_outranks_the_user_id_fallback(self):
        ctx = build_run_context_for_request(
            _Request(claims={"tenant_id": "acme"}, user_id="u1"),
            run_id="r",
            session_id="s",
            user_id="u1",
        )

        assert ctx.tenant_id == "acme"

    def test_a_mismatched_user_id_yields_no_tenant(self):
        """A form value that disagrees with the verified identity is a spoof attempt, not a
        typo -- it must not fall back to either value."""
        ctx = build_run_context_for_request(
            _Request(user_id="u1"),
            run_id="r",
            session_id="s",
            user_id="someone-else",
        )

        assert ctx.tenant_id is None

    def test_the_builder_takes_no_tenant_argument_at_all(self):
        """Structural, not behavioural: the absence of the parameter is the guarantee. A future
        edit adding one would re-open the spoofability this function exists to prevent, so the
        signature is pinned."""
        from inspect import signature

        assert "tenant_id" not in signature(build_run_context_for_request).parameters


class TestNoRequestMeansNoTenant:
    def test_a_programmatic_caller_gets_no_tenant(self):
        """`request=None` is a programmatic caller or a route with no request in scope: the default
        policy and no budget, which is the pre-EGPT-3.6 behaviour."""
        ctx = build_run_context_for_request(None, run_id="r", session_id="s", user_id="u1")

        assert ctx.tenant_id is None
        assert ctx.user_id == "u1", "identity still flows; only the tenant is withheld"

    def test_identity_and_metadata_are_carried(self):
        ctx = build_run_context_for_request(
            _Request(user_id="u1"), run_id="r1", session_id="s1", user_id="u1", metadata={"escalate": True}
        )

        assert (ctx.run_id, ctx.session_id, ctx.user_id) == ("r1", "s1", "u1")
        assert ctx.metadata == {"escalate": True}
