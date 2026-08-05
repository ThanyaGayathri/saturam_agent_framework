"""Starter-template names and the EGPT-0.12 legacy `-t` aliases.

`InfraStarterTemplate` deliberately keeps its upstream *member* names while its
*values* carry the Saturam names, so the two are easy to mix up. These tests pin the
half a user actually sees: what you type after `-t`, and where it clones from.
"""

from egpt.infra.enums import LEGACY_TEMPLATE_ALIASES, InfraStarterTemplate
from egpt.infra.operator import TEMPLATE_TO_NAME_MAP, TEMPLATE_TO_REPO_MAP


def test_template_values_carry_no_upstream_brand():
    """Regression guard for EGPT-0.12: what a user types is the branded name."""
    for template in InfraStarterTemplate:
        assert "agno" not in template.value.lower()
        assert "agentos" not in template.value.lower()
        assert template.value.startswith("egpt-os-")


def test_template_repos_point_at_our_org():
    for template, url in TEMPLATE_TO_REPO_MAP.items():
        assert url.startswith("https://github.com/Saturam-Inc/"), template
        assert "agno-agi" not in url


def test_every_template_has_a_name_and_repo():
    """A member missing from either map degrades to a silent default at `-t` time."""
    for template in InfraStarterTemplate:
        assert template in TEMPLATE_TO_NAME_MAP
        assert template in TEMPLATE_TO_REPO_MAP
        assert TEMPLATE_TO_NAME_MAP[template] == template.value


def test_legacy_aliases_resolve_to_the_renamed_templates():
    """The pre-rebrand `-t` values keep working and land on the new template."""
    assert LEGACY_TEMPLATE_ALIASES == {
        "agentos-docker": InfraStarterTemplate.agentos_docker,
        "agentos-aws": InfraStarterTemplate.agentos_aws,
        "agentos-railway": InfraStarterTemplate.agentos_railway,
    }
    for old_value, template in LEGACY_TEMPLATE_ALIASES.items():
        assert isinstance(template, InfraStarterTemplate)
        assert template.value == old_value.replace("agentos-", "egpt-os-")


def test_every_template_has_a_legacy_alias():
    """No template may quietly lose its old spelling and start rejecting `-t`."""
    assert set(LEGACY_TEMPLATE_ALIASES.values()) == set(InfraStarterTemplate)


def test_legacy_aliases_are_lowercase_lookup_keys():
    """`create_infra_from_template` looks these up as `template.lower()`, so an
    uppercase key here would be unreachable."""
    for old_value in LEGACY_TEMPLATE_ALIASES:
        assert old_value == old_value.lower()


def test_legacy_aliases_do_not_collide_with_current_values():
    """An alias that shadowed a current value would never reach the alias branch."""
    current = {t.value for t in InfraStarterTemplate}
    assert current.isdisjoint(set(LEGACY_TEMPLATE_ALIASES))
