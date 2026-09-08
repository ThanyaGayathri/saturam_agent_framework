"""R2 without a cluster: the offline identity helper and the auth settings.

The cluster scenarios are the cookbook (R02_cluster_evidence.sh). These pin what must hold before
it runs: a minted token verifies against the JWKS the middleware will load, and a JWKS path turns
authorization on with the audience the chart sets.
"""

from __future__ import annotations

import json

import jwt
import pytest
from jwt import PyJWK

from egpt_retail.app.devauth import generate_rsa_jwks, mint_token, write_dev_jwks
from egpt_retail.app.factory import build_agent_os
from egpt_retail.app.settings import RetailSettings


def test_minted_token_verifies_against_the_jwks():
    pem, jwks = generate_rsa_jwks(kid="t")
    token = mint_token(
        pem, ["config:read"], subject="alice", audience="api://egpt-retail", kid="t"
    )
    key = PyJWK.from_dict(jwks["keys"][0]).key
    claims = jwt.decode(token, key, algorithms=["RS256"], audience="api://egpt-retail")
    assert claims["sub"] == "alice"
    assert claims["scopes"] == ["config:read"], "the middleware reads `scopes`, a list"
    assert jwt.get_unverified_header(token)["kid"] == "t"


def test_wrong_audience_is_refused():
    pem, jwks = generate_rsa_jwks()
    token = mint_token(pem, ["config:read"], audience="api://other")
    key = PyJWK.from_dict(jwks["keys"][0]).key
    with pytest.raises(jwt.InvalidAudienceError):
        jwt.decode(token, key, algorithms=["RS256"], audience="api://egpt-retail")


def test_write_dev_jwks_produces_loadable_files(tmp_path):
    private_path, jwks_path = write_dev_jwks(tmp_path / "auth")
    assert private_path.read_text().startswith("-----BEGIN PRIVATE KEY-----")
    loaded = json.loads(jwks_path.read_text())
    assert loaded["keys"][0]["kty"] == "RSA" and loaded["keys"][0]["alg"] == "RS256"
    assert oct(private_path.stat().st_mode & 0o777) == "0o600"


def test_auth_is_on_exactly_when_a_jwks_file_is_configured(monkeypatch):
    monkeypatch.delenv("EGPT_RETAIL_AUTH_JWKS_FILE", raising=False)
    assert RetailSettings().authorization_enabled is False
    monkeypatch.setenv("EGPT_RETAIL_AUTH_JWKS_FILE", "/etc/egpt/jwks/jwks.json")
    monkeypatch.setenv("EGPT_RETAIL_AUTH_AUDIENCE", "api://egpt-retail")
    s = RetailSettings()
    assert (
        s.authorization_enabled is True
        and s.retail_auth_audience == "api://egpt-retail"
    )


def test_a_jwks_file_without_an_audience_refuses_to_build(monkeypatch):
    """verify_audience must never be silently False while a JWKS file makes auth mandatory --
    review finding: an unset EGPT_RETAIL_AUTH_AUDIENCE would otherwise accept any `aud` claim
    from any token the JWKS can verify."""
    monkeypatch.setenv("EGPT_RETAIL_AUTH_JWKS_FILE", "/etc/egpt/jwks/jwks.json")
    monkeypatch.delenv("EGPT_RETAIL_AUTH_AUDIENCE", raising=False)
    monkeypatch.delenv("EGPT_RETAIL_AUTH_SKIP_AUDIENCE_CHECK", raising=False)
    with pytest.raises(RuntimeError, match="EGPT_RETAIL_AUTH_AUDIENCE"):
        build_agent_os(RetailSettings())


def test_skip_audience_check_escape_hatch_allows_no_audience(monkeypatch):
    monkeypatch.setenv("EGPT_RETAIL_AUTH_JWKS_FILE", "/etc/egpt/jwks/jwks.json")
    monkeypatch.delenv("EGPT_RETAIL_AUTH_AUDIENCE", raising=False)
    monkeypatch.setenv("EGPT_RETAIL_AUTH_SKIP_AUDIENCE_CHECK", "true")
    # Does not raise the audience RuntimeError; PostgresDb is pure construction (no connection),
    # so this exercises build_agent_os end to end without a database.
    os = build_agent_os(RetailSettings())
    assert os is not None


def test_direct_db_url_defaults_to_the_pooled_one(monkeypatch):
    monkeypatch.delenv("EGPT_RETAIL_DB_DIRECT_URL", raising=False)
    monkeypatch.setenv(
        "EGPT_RETAIL_DB_URL", "postgresql+psycopg://ai:ai@pgbouncer:5432/ai"
    )
    assert (
        RetailSettings().db_direct_url == "postgresql+psycopg://ai:ai@pgbouncer:5432/ai"
    )
    monkeypatch.setenv(
        "EGPT_RETAIL_DB_DIRECT_URL", "postgresql+psycopg://ai:ai@postgres:5432/ai"
    )
    assert (
        RetailSettings().db_direct_url == "postgresql+psycopg://ai:ai@postgres:5432/ai"
    )
