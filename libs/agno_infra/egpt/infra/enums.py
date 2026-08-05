from enum import Enum


class InfraStarterTemplate(str, Enum):
    # Member names keep their upstream spelling (back-compat, see EGPT-0.12); the
    # values are what users type and see, so they name the Saturam-Inc templates.
    agentos_docker = "egpt-os-docker"
    agentos_aws = "egpt-os-aws"
    agentos_railway = "egpt-os-railway"


# Values accepted for `-t` before the EGPT-0.12 rebrand, mapped to their new names.
LEGACY_TEMPLATE_ALIASES = {
    "agentos-docker": InfraStarterTemplate.agentos_docker,
    "agentos-aws": InfraStarterTemplate.agentos_aws,
    "agentos-railway": InfraStarterTemplate.agentos_railway,
}
