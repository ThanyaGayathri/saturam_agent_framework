"""Per-domain settings for the capability-policy seam (EGPT-2.3).

Follows the per-domain-file convention: the central ``EgptSettings`` in
``egpt/settings/__init__.py`` owns the framework's own serving/auth surface, and each lane adds
its own module here rather than appending to that shared class body.

One field, and it is deliberately the only one. The policy *document* carries the allow-lists,
the grants and the rollout mode; the environment carries nothing but the path to it. Splitting
policy across env vars and a document would give an operator two places to look when an agent
is unexpectedly refused, and only one of them would be in version control.

No bare env name. ``CAPABILITY_POLICY_FILE`` in the shared OS namespace is exactly the
accidental binding ``bare_env``'s docstring warns about, and a security control is the last
setting that should be readable from an unprefixed name someone else may already export.
"""

from __future__ import annotations

from typing import Any, Optional

from egpt.settings.sources import BrandedSettings


class PolicySettings(BrandedSettings):
    """Environment configuration for the tenant capability policy.

    Read via ``EGPT_CAPABILITY_POLICY_FILE``, falling back to ``AGNO_CAPABILITY_POLICY_FILE`` --
    the same two-step chain every other EGPT settings model uses, minus the opt-in bare name.
    """

    # Path to the JSON capability-policy document. ``None`` means "no policy configured", which
    # is the seam's no-op path: definitions are validated exactly as they were before EGPT-2.3.
    capability_policy_file: Optional[str] = None


def get_policy_settings(**overrides: Any) -> PolicySettings:
    """Build a fresh snapshot of the policy settings from the current environment.

    No module-level singleton and no cache, matching ``egpt.settings.get_settings``: a test
    that points the policy file somewhere else after an earlier test warmed a cache must not
    read a stale path.
    """
    return PolicySettings(**overrides)


__all__ = ["PolicySettings", "get_policy_settings"]
