"""EGPT-3.2: invalid configuration is rejected with a clear error.

"Clear" is asserted mechanically as ``ModelProviderConfigError.field_name`` -- an operator
reading a stack trace needs to be told *which field* is wrong, and a message-only assertion
would let that regress silently.

Validation is eager (in ``__post_init__``), so a broken deployment fails at process start
rather than on whichever request happens to route to the broken policy.
"""

from typing import Any, Dict, Optional

import pytest

from egpt.providers import ModelPolicy, ModelProviderConfigError, ProviderConfig, ProviderMode

# --------------------------------------------------------------------------- #
# ModelPolicy
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kwargs", "expected_field"),
    [
        # A policy with no mode cannot say which family of backends serves it.
        pytest.param({"mode": None, "model_id": "m"}, "mode", id="missing-mode"),
        pytest.param({"mode": "cloudy", "model_id": "m"}, "mode", id="unknown-mode-string"),
        pytest.param({"mode": 7, "model_id": "m"}, "mode", id="non-string-mode"),
        # An adapter names which LoRA weights answer; a frontier host cannot honour it, so
        # accepting the config would serve a different model than the operator asked for.
        pytest.param({"mode": "frontier", "model_id": "m", "adapter": "lora-v1"}, "adapter", id="adapter-on-frontier"),
        pytest.param({"mode": "local", "model_id": "  "}, "model_id", id="blank-model-id"),
        pytest.param({"mode": "local", "model_id": "m", "provider_key": ""}, "provider_key", id="blank-provider-key"),
    ],
)
def test_invalid_policy_names_the_offending_field(kwargs: Dict[str, Any], expected_field: str):
    with pytest.raises(ModelProviderConfigError) as exc:
        ModelPolicy(**kwargs)
    assert exc.value.field_name == expected_field


@pytest.mark.parametrize(
    ("data", "expected_field"),
    [
        pytest.param({"model_id": "m"}, "mode", id="from-dict-missing-mode"),
        pytest.param({"mode": "nope", "model_id": "m"}, "mode", id="from-dict-unknown-mode"),
        pytest.param({"modle": "local", "model_id": "m"}, "modle", id="from-dict-typo'd-key"),
        pytest.param({"mode": "frontier", "model_id": "m", "adapter": "l"}, "adapter", id="from-dict-adapter"),
    ],
)
def test_invalid_policy_document_names_the_offending_field(data: Dict[str, Any], expected_field: str):
    with pytest.raises(ModelProviderConfigError) as exc:
        ModelPolicy.from_dict(data)
    assert exc.value.field_name == expected_field


def test_a_mode_with_no_model_is_caught_at_resolve_time():
    """``ModelPolicy(mode=...)`` alone is legal (a config may declare intent before the model
    id lands), but resolving through it must fail naming ``model_id`` rather than construct
    something arbitrary."""
    from egpt.providers import ConfigDrivenModelProvider, ModelResolveRequest

    provider = ConfigDrivenModelProvider(ProviderConfig(default=ModelPolicy(mode=ProviderMode.LOCAL)))
    with pytest.raises(ModelProviderConfigError) as exc:
        provider.resolve(ModelResolveRequest(tenant_id="acme"))
    assert exc.value.field_name == "model_id"


@pytest.mark.parametrize("value", ["local", "LOCAL", " Local ", ProviderMode.LOCAL])
def test_mode_accepts_the_forms_a_handwritten_document_produces(value: Any):
    assert ModelPolicy(mode=value, model_id="m").mode is ProviderMode.LOCAL


def test_provider_key_defaults_per_mode():
    assert ModelPolicy(mode="local", model_id="m").effective_provider_key == "vllm"
    assert ModelPolicy(mode="frontier", model_id="m").effective_provider_key == "openai-responses"


def test_policy_config_is_frozen_against_the_holder():
    policy = ModelPolicy(mode="local", model_id="m", config={"base_url": "http://x"})
    with pytest.raises(TypeError):
        policy.config["base_url"] = "http://evil"  # type: ignore[index]
    # merged_config hands back a plain dict, so mutating the copy is safe and does not leak.
    merged = policy.merged_config()
    merged["base_url"] = "http://y"
    assert policy.config["base_url"] == "http://x"


# --------------------------------------------------------------------------- #
# ProviderConfig
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kwargs", "expected_field"),
    [
        pytest.param({"default": "frontier"}, "default", id="default-not-a-policy"),
        pytest.param({"policies": {("a",): "local"}}, "policies", id="value-not-a-policy"),
        pytest.param({"policies": {12: ModelPolicy(mode="local", model_id="m")}}, "policies", id="key-wrong-type"),
        pytest.param(
            {"policies": {("a", "b", "c", "d"): ModelPolicy(mode="local", model_id="m")}},
            "policies",
            id="key-too-long",
        ),
        pytest.param(
            {"policies": {("a", 3): ModelPolicy(mode="local", model_id="m")}},
            "policies",
            id="key-part-not-a-string",
        ),
    ],
)
def test_invalid_config_names_the_offending_field(kwargs: Dict[str, Any], expected_field: str):
    with pytest.raises(ModelProviderConfigError) as exc:
        ProviderConfig(**kwargs)
    assert exc.value.field_name == expected_field


@pytest.mark.parametrize(
    ("data", "expected_field"),
    [
        pytest.param({"tenants": []}, "tenants", id="unknown-top-level-key"),
        pytest.param({"policies": {"acme": {}}}, "policies", id="policies-as-a-mapping"),
        pytest.param({"policies": "acme"}, "policies", id="policies-not-a-list"),
        pytest.param({"policies": ["acme"]}, "policies", id="record-not-a-mapping"),
        pytest.param({"default": {"model_id": "m"}}, "mode", id="default-missing-mode"),
        pytest.param({"policies": [{"tenant_id": "a", "model_id": "m"}]}, "mode", id="record-missing-mode"),
    ],
)
def test_invalid_config_document_names_the_offending_field(data: Any, expected_field: str):
    with pytest.raises(ModelProviderConfigError) as exc:
        ProviderConfig.from_dict(data)
    assert exc.value.field_name == expected_field


def test_duplicate_identity_records_are_rejected_not_last_wins():
    """A hand-edited document with a copy-pasted tenant_id would otherwise route that tenant to
    the wrong mode with no error -- the silent misconfiguration this ticket exists to surface."""
    with pytest.raises(ModelProviderConfigError) as exc:
        ProviderConfig.from_dict(
            {
                "policies": [
                    {"tenant_id": "acme", "mode": "local", "model_id": "qwen3-8b"},
                    {"tenant_id": "acme", "mode": "frontier", "model_id": "gpt-5.5"},
                ]
            }
        )
    assert exc.value.field_name == "policies"


def test_config_document_round_trips_into_a_lookup():
    config = ProviderConfig.from_dict(
        {
            "default": {"mode": "frontier", "model_id": "gpt-5.5"},
            "policies": [
                {"tenant_id": "acme", "mode": "local", "model_id": "qwen3-8b"},
                {
                    "tenant_id": "acme",
                    "agent_id": "support",
                    "role": "parser",
                    "mode": "local",
                    "model_id": "qwen3-1.7b",
                    "adapter": "support-v3",
                },
            ],
        }
    )

    assert config.lookup("acme").model_id == "qwen3-8b"
    assert config.lookup("acme", "support", "parser").adapter == "support-v3"
    assert config.lookup("globex").model_id == "gpt-5.5"
    assert config.modes() == (ProviderMode.FRONTIER, ProviderMode.LOCAL)


def test_an_empty_config_resolves_nothing_rather_than_guessing():
    """The EGPT-3.1 no-op path: nothing configured means no policy, not a default model."""
    config = ProviderConfig()
    assert config.lookup("acme") is None
    assert config.modes() == ()
    assert config.provider_keys() == ()


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("acme", ("acme", None, None)),
        (("acme",), ("acme", None, None)),
        (("acme", "support"), ("acme", "support", None)),
        (("acme", "support", "parser"), ("acme", "support", "parser")),
        ((None, None, "parser"), (None, None, "parser")),
    ],
)
def test_short_keys_are_padded_to_the_full_identity(key: Any, expected: Any):
    config = ProviderConfig(policies={key: ModelPolicy(mode="local", model_id="m")})
    assert expected in config.policies


def test_with_policy_returns_a_copy_and_leaves_the_original_alone():
    original = ProviderConfig(default=ModelPolicy(mode="frontier", model_id="gpt-5.5"))
    updated = original.with_policy("acme", ModelPolicy(mode="local", model_id="qwen3-8b"))

    assert original.lookup("acme").model_id == "gpt-5.5"
    assert updated.lookup("acme").model_id == "qwen3-8b"


def test_policies_mapping_is_frozen_against_the_holder():
    config = ProviderConfig(policies={("acme",): ModelPolicy(mode="local", model_id="m")})
    with pytest.raises(TypeError):
        config.policies[("globex",)] = ModelPolicy(mode="local", model_id="m")  # type: ignore[index]


# --------------------------------------------------------------------------- #
# Settings block: EGPT-3.2 fields exist and stay branded.
# --------------------------------------------------------------------------- #


def test_provider_settings_expose_the_egpt_3_2_fields(monkeypatch):
    from egpt.settings.providers import get_provider_settings

    assert get_provider_settings().policy_file is None
    assert get_provider_settings().tenant_claim == "tenant_id"

    monkeypatch.setenv("EGPT_POLICY_FILE", "/etc/egpt/policy.json")
    monkeypatch.setenv("EGPT_TENANT_CLAIM", "org_id")
    settings = get_provider_settings()
    assert settings.policy_file == "/etc/egpt/policy.json"
    assert settings.tenant_claim == "org_id"


def test_resolve_provider_setting_reads_one_field_live(monkeypatch):
    """The per-request path reads a single field rather than constructing the whole model."""
    from egpt.settings.providers import resolve_provider_setting

    monkeypatch.delenv("EGPT_TENANT_CLAIM", raising=False)
    assert resolve_provider_setting("tenant_claim") is None

    monkeypatch.setenv("EGPT_TENANT_CLAIM", "org_id")
    assert resolve_provider_setting("tenant_claim") == "org_id"

    # A typo must fail loudly rather than resolve to None.
    with pytest.raises(KeyError):
        resolve_provider_setting("tenant_clam")


def test_from_settings_with_nothing_configured_is_the_no_op_path(monkeypatch):
    from egpt.providers import ConfigDrivenModelProvider

    monkeypatch.delenv("EGPT_POLICY_FILE", raising=False)
    monkeypatch.delenv("EGPT_DEFAULT_MODE", raising=False)
    provider = ConfigDrivenModelProvider.from_settings()
    assert provider.config.modes() == ()


def test_from_settings_reads_a_policy_document(tmp_path, monkeypatch):
    import json

    from egpt.providers import ConfigDrivenModelProvider

    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "default": {"mode": "frontier", "model_id": "gpt-5.5"},
                "policies": [{"tenant_id": "acme", "mode": "local", "model_id": "qwen3-8b"}],
            }
        )
    )
    monkeypatch.setenv("EGPT_POLICY_FILE", str(path))

    provider = ConfigDrivenModelProvider.from_settings()
    assert provider.policy_for(_request(tenant_id="acme")).mode is ProviderMode.LOCAL
    assert provider.policy_for(_request(tenant_id="globex")).mode is ProviderMode.FRONTIER


@pytest.mark.parametrize("content", ["{ not json", '{"policies": "nope"}'])
def test_from_settings_rejects_a_broken_policy_document(tmp_path, monkeypatch, content: str):
    from egpt.providers import ConfigDrivenModelProvider

    path = tmp_path / "policy.json"
    path.write_text(content)
    monkeypatch.setenv("EGPT_POLICY_FILE", str(path))

    with pytest.raises(ModelProviderConfigError):
        ConfigDrivenModelProvider.from_settings()


def test_from_settings_reports_a_missing_policy_file(tmp_path, monkeypatch):
    from egpt.providers import ConfigDrivenModelProvider

    monkeypatch.setenv("EGPT_POLICY_FILE", str(tmp_path / "absent.json"))
    with pytest.raises(ModelProviderConfigError) as exc:
        ConfigDrivenModelProvider.from_settings()
    assert exc.value.field_name == "policy_file"


def _request(tenant_id: Optional[str] = None):
    from egpt.providers import ModelResolveRequest

    return ModelResolveRequest(tenant_id=tenant_id)


# --------------------------------------------------------------------------- #
# Review regressions (PR #46)
# --------------------------------------------------------------------------- #


def test_a_non_mapping_config_is_a_named_config_error():
    """Finding 3744507148: the operator's exact document -- config as a string -- must name the
    field, not raise 'dictionary update sequence element #0'."""
    with pytest.raises(ModelProviderConfigError) as exc:
        ProviderConfig.from_dict({"default": {"mode": "local", "model_id": "m", "config": "base_url=http://x"}})
    assert exc.value.field_name == "config"
    assert "mapping" in str(exc.value)


def test_a_list_config_fails_the_same_way():
    with pytest.raises(ModelProviderConfigError) as exc:
        ModelPolicy(mode="local", model_id="m", config=["base_url"])
    assert exc.value.field_name == "config"


def test_an_identity_less_policy_record_is_rejected():
    """Finding 3744507150: a record with no identity is a second 'default' in disguise that
    silently shadows the real one."""
    with pytest.raises(ModelProviderConfigError) as exc:
        ProviderConfig.from_dict(
            {
                "default": {"mode": "frontier", "model_id": "gpt-5.5"},
                "policies": [{"mode": "local", "model_id": "qwen"}],
            }
        )
    assert exc.value.field_name == "policies"
    assert "default" in str(exc.value)


def test_the_programmatic_identity_less_shadow_is_rejected_too():
    """Same shadow built in code: default present + a (None, None, None) key."""
    with pytest.raises(ModelProviderConfigError) as exc:
        ProviderConfig(
            default=ModelPolicy(mode="frontier", model_id="gpt-5.5"),
            policies={(None, None, None): ModelPolicy(mode="local", model_id="qwen")},
        )
    assert exc.value.field_name == "policies"
