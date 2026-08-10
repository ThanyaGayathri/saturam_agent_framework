"""Per-tenant capability policy for DB-authored component definitions (EGPT-2.3).

EGPT-2.2 proved a stored config is *well-formed and hydratable*. It says nothing about whether
the tenant who wrote it may use the capabilities it names. Without that second gate a tenant
can wire ``ShellTools`` into a stored definition and get arbitrary code execution inside the
platform, which is the abuse surface this module closes.

This is capability policy, not RBAC. ``egpt.os.scopes`` answers *who may call the API*; this
module answers *what a definition may compose*. Conflating them was considered and rejected:
a scope grammar keyed on resource ids has nowhere to put "this tenant may reference the sql
toolkit but not the shell toolkit".

Two enforcement points, one walker
----------------------------------
:func:`check_capabilities` is called from ``egpt.registry.schema._check_policy`` on the write
path (violations become field-level ``ConfigError``s, 422) and from
:func:`enforce_policy_or_raise` at hydration (violations become a raised
:class:`PolicyViolationError`, mapped to 403 by the api layer). One walker, two renderings --
a second implementation would drift, and the whole point of checking twice is that the two
checks agree.

Why the toolkit table is a table of strings
-------------------------------------------
``egpt.registry`` is core, and core must not import integrations (ADR 0001). Import-linter
builds its graph from the AST, so a function-local import would not dodge that either. It
does not need to: classification only ever compares ``type(toolkit).__module__`` against these
prefixes, and a string comparison imports nothing. ``tests/unit/registry/test_schema_layering.py``
pins the property.

The hard problem: a serialized config has no toolkits in it
-----------------------------------------------------------
``parse_tools`` flattens every ``Toolkit`` into its individual ``Function``s before
serialization, and ``Function.to_dict`` emits a seven-key allowlist with no toolkit, module or
qualname. So a stored config says ``read_file`` -- and ``read_file`` is a function of
``FileTools``, ``PythonTools``, ``E2BTools`` *and* ``DaytonaTools``. Discovery cannot help
either: ``registry/_declarations/tools.py`` has one row, documented as a contract rather than
a gap.

:func:`classify_tools` recovers what it can, in a deliberate order: live-``Registry`` evidence
first (walking the MRO, so a subclass of ``ShellTools`` is still shell), and the static
:data:`PRIVILEGED_FUNCTION_NAMES` backstop only where the registry could not answer at all.
That ordering is the whole design. Registry-first means a tenant's own ``def read_file`` is
not blocked by a name collision with ``FileTools``; backstop-last means ``run_shell_command``
is still refused on the one path where nothing else can see it.

Ambiguity fails closed, and that is load-bearing rather than cautious: ``Registry``'s
entrypoint lookup is last-wins keyed by function name and merely *warns* on a collision, so
with ``ShellTools`` and ``DaytonaTools`` both registered, which ``run_shell_command`` actually
executes depends on registration order. A classifier that picked one would be guessing which
sandbox the agent escapes into.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    AbstractSet,
    Any,
    Dict,
    FrozenSet,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from egpt.models.utils import resolve_provider_key
from egpt.settings.policy import get_policy_settings
from egpt.utils.log import log_warning

if TYPE_CHECKING:
    from egpt.registry.registry import Registry


# The version of the policy *document* format. Bumped only by a breaking change to the
# document's shape, independently of the config schema version.
POLICY_VERSION: int = 1

# Error codes, defined here and imported into `egpt.registry.schema`'s namespace so the closed
# vocabulary stays in one place. `tests/unit/registry/test_config_schema.py` builds the
# vocabulary from `vars(schema)`, so the import there is load-bearing, not stylistic.
CODE_CAPABILITY_NOT_PERMITTED = "capability_not_permitted"
CODE_PRIVILEGED_TOOL_BLOCKED = "privileged_tool_blocked"

CapabilityKind = Literal["model", "tool", "mcp"]
PolicyMode = Literal["enforce", "warn"]

# The wildcard accepted in `models`, `tools` and `mcp_servers`. Deliberately NOT accepted in
# `granted_privileged`: an operator who wants every privileged toolkit lists all of them, so
# the grant shows up in the diff.
WILDCARD = "*"

# How many candidate toolkits to name in an ambiguity message before truncating.
_MAX_NAMED_CANDIDATES = 6


# ---------------------------------------------------------------------------------------------
# The privileged inventory
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PrivilegedToolkit:
    """One toolkit that is denied to tenant authors unless explicitly granted.

    ``module`` is a package prefix, matched against ``type(toolkit).__module__`` and never
    imported. ``toolkit_name`` is the ``name=`` the class passes to ``Toolkit.__init__``; it is
    recorded for diagnostics only, because those slugs are inconsistent across the tree
    (``shell_tools``, ``aws-lambda``, ``aws_ses_tool``, ``local_file_system``) and keying policy
    on them would be keying on an accident.
    """

    slug: str
    module: str
    class_name: str
    toolkit_name: str
    functions: FrozenSet[str]
    reason: str


def _privileged(
    slug: str, module: str, class_name: str, toolkit_name: str, reason: str, *functions: str
) -> PrivilegedToolkit:
    return PrivilegedToolkit(
        slug=slug,
        module=module,
        class_name=class_name,
        toolkit_name=toolkit_name,
        functions=frozenset(functions),
        reason=reason,
    )


# Keyed by the grant slug an operator writes in `granted_privileged`. Documented in
# docs/capability-policy.md, which is pinned against this table by a doc guard.
PRIVILEGED_TOOLKITS: Mapping[str, PrivilegedToolkit] = {
    tk.slug: tk
    for tk in (
        _privileged(
            "shell",
            "egpt.integrations.tools.shell",
            "ShellTools",
            "shell_tools",
            "arbitrary command execution on the host",
            "run_shell_command",
        ),
        _privileged(
            "python",
            "egpt.integrations.tools.python",
            "PythonTools",
            "python_tools",
            "arbitrary code execution and package installation",
            "save_to_file_and_run",
            "run_python_code",
            "pip_install_package",
            "uv_pip_install_package",
            "run_python_file_return_variable",
            "read_file",
            "list_files",
        ),
        _privileged(
            "docker",
            "egpt.integrations.tools.docker",
            "DockerTools",
            "docker_tools",
            "container and image control on the host daemon",
            "list_containers",
            "start_container",
            "stop_container",
            "remove_container",
            "get_container_logs",
            "inspect_container",
            "run_container",
            "exec_in_container",
            "list_images",
            "pull_image",
            "remove_image",
            "build_image",
            "tag_image",
            "inspect_image",
            "list_volumes",
            "create_volume",
            "remove_volume",
            "inspect_volume",
            "list_networks",
            "create_network",
            "remove_network",
            "inspect_network",
            "connect_container_to_network",
            "disconnect_container_from_network",
        ),
        _privileged(
            "e2b",
            "egpt.integrations.tools.e2b",
            "E2BTools",
            "e2b_tools",
            "remote sandbox execution and filesystem access",
            "run_python_code",
            "upload_file",
            "download_png_result",
            "download_chart_data",
            "download_file_from_sandbox",
            "list_files",
            "read_file_content",
            "write_file_content",
            "watch_directory",
            "get_public_url",
            "run_server",
            "set_sandbox_timeout",
            "get_sandbox_status",
            "shutdown_sandbox",
            "list_running_sandboxes",
            "run_command",
            "stream_command",
            "run_background_command",
            "kill_background_command",
        ),
        _privileged(
            "daytona",
            "egpt.integrations.tools.daytona",
            "DaytonaTools",
            "daytona_tools",
            "remote sandbox execution and filesystem access",
            "run_code",
            "run_shell_command",
            "create_file",
            "read_file",
            "list_files",
            "delete_file",
            "change_directory",
        ),
        _privileged(
            "sql",
            "egpt.integrations.tools.sql",
            "SQLTools",
            "sql_tools",
            "arbitrary SQL against a configured database",
            "list_tables",
            "describe_table",
            "run_sql_query",
        ),
        _privileged(
            "aws_lambda",
            "egpt.integrations.tools.aws_lambda",
            "AWSLambdaTools",
            "aws-lambda",
            "invocation of arbitrary cloud functions",
            "list_functions",
            "invoke_function",
        ),
        _privileged(
            "aws_ses",
            "egpt.integrations.tools.aws_ses",
            "AWSSESTool",
            "aws_ses_tool",
            "outbound email from the platform's identity",
            "send_email",
        ),
        _privileged(
            "file",
            "egpt.integrations.tools.file",
            "FileTools",
            "file_tools",
            "local filesystem read and write",
            "save_file",
            "read_file",
            "list_files",
            "search_files",
            "read_file_chunk",
            "replace_file_chunk",
            "search_content",
            "delete_file",
        ),
        _privileged(
            "local_file_system",
            "egpt.integrations.tools.local_file_system",
            "LocalFileSystemTools",
            "local_file_system",
            "local filesystem read and write",
            "write_file",
            "read_file",
        ),
        _privileged(
            "file_generation",
            "egpt.integrations.tools.file_generation",
            "FileGenerationTools",
            "file_generation",
            "writing generated files to the local filesystem",
            "generate_json_file",
            "generate_csv_file",
            "generate_pdf_file",
            "generate_docx_file",
            "generate_text_file",
            "generate_html_file",
        ),
    )
}


def _build_function_backstop() -> Mapping[str, Tuple[str, ...]]:
    """Function name -> every privileged slug that exposes it.

    A tuple rather than a single slug because the names genuinely collide: ``read_file`` belongs
    to four of these toolkits and ``run_shell_command`` to two. The collision is the reason the
    backstop fails closed.
    """
    index: Dict[str, List[str]] = {}
    for toolkit in PRIVILEGED_TOOLKITS.values():
        for function_name in toolkit.functions:
            index.setdefault(function_name, []).append(toolkit.slug)
    return {name: tuple(sorted(slugs)) for name, slugs in index.items()}


PRIVILEGED_FUNCTION_NAMES: Mapping[str, Tuple[str, ...]] = _build_function_backstop()


# ---------------------------------------------------------------------------------------------
# The policy document
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantPolicy:
    """What one tenant may reference.

    ``None`` and the absence of a key mean *unrestricted* for that capability class; an empty
    tuple means *nothing permitted*. The distinction is load-bearing and is the difference
    between "we have not started allow-listing models yet" and "this tenant gets no models".
    """

    models: Optional[Tuple[str, ...]] = None
    tools: Optional[Tuple[str, ...]] = None
    mcp_servers: Optional[Tuple[str, ...]] = None
    granted_privileged: Tuple[str, ...] = ()

    def allows_model(self, provider_key: str, model_id: str) -> bool:
        """True when this tenant may use ``provider_key``/``model_id``.

        Accepts a bare provider slug (``openai``), a provider-qualified id
        (``openai:gpt-5.5``), and a glob on the id (``openai:gpt-5*``). The provider half is
        compared on the normalized key, so ``OpenAI``, ``openai`` and ``azure`` all land on the
        same declaration-table entry.
        """
        if self.models is None:
            return True
        for entry in self.models:
            if entry == WILDCARD:
                return True
            entry_provider, _, entry_model = entry.partition(":")
            if resolve_provider_key(entry_provider.strip()) != provider_key:
                continue
            if not entry_model or entry_model.strip() == WILDCARD:
                return True
            if fnmatchcase(model_id.lower(), entry_model.strip().lower()):
                return True
        return False

    def allows_tool(self, capability_id: str) -> bool:
        """True when this tenant may use a (non-privileged) tool capability."""
        return _matches_any(capability_id, self.tools)

    def allows_mcp(self, server_id: str) -> bool:
        """True when this tenant may use an MCP server, identified by its toolkit name."""
        return _matches_any(server_id, self.mcp_servers)

    def grants_privileged(self, slug: str) -> bool:
        """True when this tenant holds the explicit grant for a privileged toolkit.

        Note there is no wildcard here, by construction: ``granted_privileged`` rejects ``*`` at
        load time. A tenant that should hold every privileged toolkit lists them.
        """
        return slug in self.granted_privileged


def _matches_any(value: str, patterns: Optional[Tuple[str, ...]]) -> bool:
    if patterns is None:
        return True
    return any(pattern == WILDCARD or fnmatchcase(value, pattern) for pattern in patterns)


@dataclass(frozen=True)
class CapabilityPolicy:
    """A whole policy document: the default tenant policy plus per-tenant overrides.

    ``enabled`` is what makes this feature opt-in. With no document configured and nothing
    injected, callers get :meth:`disabled`, every check returns on the first line, and the
    framework behaves exactly as it did before EGPT-2.3. Turning the denylist on unconditionally
    would reject definitions that legitimately wire ``PythonTools`` today; default-deny applies
    *within* the feature, once an operator opts in.
    """

    version: int = POLICY_VERSION
    mode: PolicyMode = "enforce"
    unknown_tenant: Literal["defaults", "deny"] = "defaults"
    defaults: TenantPolicy = field(default_factory=TenantPolicy)
    tenants: Mapping[str, TenantPolicy] = field(default_factory=dict)
    enabled: bool = True

    @classmethod
    def disabled(cls) -> "CapabilityPolicy":
        """The no-op policy. Every check short-circuits on ``enabled``."""
        return cls(enabled=False)

    def for_tenant(self, tenant_id: Optional[str]) -> TenantPolicy:
        """The policy that applies to ``tenant_id``.

        ``None`` -- an unscoped caller, or a deployment with no ``tenant_id_claim`` configured --
        gets ``defaults``. Not deny-all, which would break every deployment the moment it wrote
        its first policy file, and not allow-all, which would make the denylist bypassable by
        simply dropping the claim. An operator who wants unscoped traffic refused writes an
        empty ``defaults``, or sets ``unknown_tenant: "deny"`` and enumerates their tenants.
        """
        if tenant_id is None:
            return self.defaults
        known = self.tenants.get(tenant_id)
        if known is not None:
            return known
        return _DENY_ALL if self.unknown_tenant == "deny" else self.defaults

    # -- loading ------------------------------------------------------------------------------

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> "CapabilityPolicy":
        """Build a policy from a parsed document, rejecting anything it does not understand.

        Strict on purpose. A typo'd ``"priviledged_tools"`` that parsed as "no grants" would
        read as a working policy and silently deny; a document from the future would read as a
        policy this build understands. Both fail loudly instead.
        """
        if not isinstance(document, Mapping):
            raise ValueError("Capability policy document must be a JSON object.")

        _reject_unknown_keys(document, {"version", "mode", "unknown_tenant", "defaults", "tenants"}, "<document>")

        version = document.get("version", POLICY_VERSION)
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("Capability policy 'version' must be an integer.")
        if version > POLICY_VERSION:
            raise ValueError(
                f"Capability policy declares version {version}, but this framework understands up to "
                f"{POLICY_VERSION}. Upgrade before loading this document."
            )

        mode = document.get("mode", "enforce")
        if mode not in ("enforce", "warn"):
            raise ValueError(f"Capability policy 'mode' must be 'enforce' or 'warn', got {mode!r}.")

        unknown_tenant = document.get("unknown_tenant", "defaults")
        if unknown_tenant not in ("defaults", "deny"):
            raise ValueError(
                f"Capability policy 'unknown_tenant' must be 'defaults' or 'deny', got {unknown_tenant!r}."
            )

        defaults = _tenant_policy_from_dict(document.get("defaults") or {}, "defaults")

        raw_tenants = document.get("tenants") or {}
        if not isinstance(raw_tenants, Mapping):
            raise ValueError("Capability policy 'tenants' must be an object keyed by tenant id.")
        tenants = {
            str(tenant_id): _tenant_policy_from_dict(block or {}, f"tenants.{tenant_id}", defaults=defaults)
            for tenant_id, block in raw_tenants.items()
        }

        return cls(
            version=version,
            mode=mode,  # type: ignore[arg-type]
            unknown_tenant=unknown_tenant,  # type: ignore[arg-type]
            defaults=defaults,
            tenants=tenants,
        )

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> "CapabilityPolicy":
        """Load a JSON policy document from disk.

        A missing file raises. It deliberately does *not* degrade to :meth:`disabled`: a typo'd
        path silently switching the security control off is the exact failure this module exists
        to prevent.
        """
        resolved = Path(path).expanduser()
        try:
            raw = resolved.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Capability policy file not found: {resolved}. Fix the path or unset "
                "EGPT_CAPABILITY_POLICY_FILE to run without a capability policy."
            ) from exc
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Capability policy file {resolved} is not valid JSON: {exc}") from exc
        return cls.from_dict(document)


_DENY_ALL = TenantPolicy(models=(), tools=(), mcp_servers=(), granted_privileged=())

_TENANT_KEYS = frozenset({"models", "tools", "mcp_servers", "granted_privileged"})


def _reject_unknown_keys(block: Mapping[str, Any], allowed: AbstractSet[str], where: str) -> None:
    unknown = sorted(str(key) for key in block if key not in allowed)
    if unknown:
        raise ValueError(
            f"Capability policy {where} has unknown key(s): {', '.join(unknown)}. "
            f"Known keys: {', '.join(sorted(allowed))}."
        )


def _string_tuple(value: Any, where: str) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
        raise ValueError(f"Capability policy {where} must be a list of strings.")
    entries = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Capability policy {where} must contain only non-empty strings.")
        entries.append(item.strip())
    return tuple(entries)


def _tenant_policy_from_dict(
    block: Mapping[str, Any], where: str, defaults: Optional[TenantPolicy] = None
) -> TenantPolicy:
    """One tenant block. A key that is present replaces the default outright; absent inherits.

    No merge syntax on purpose -- a union is written out. An operator reading a tenant block
    should be able to see the tenant's whole permitted surface without also holding ``defaults``
    in their head.
    """
    if not isinstance(block, Mapping):
        raise ValueError(f"Capability policy {where} must be an object.")
    _reject_unknown_keys(block, _TENANT_KEYS, where)

    base = defaults if defaults is not None else TenantPolicy()
    resolved: Dict[str, Any] = {}

    for key in ("models", "tools", "mcp_servers"):
        if key not in block:
            resolved[key] = getattr(base, key)
        elif block[key] is None:
            resolved[key] = None  # explicit null == unrestricted
        else:
            resolved[key] = _string_tuple(block[key], f"{where}.{key}")

    if "granted_privileged" not in block:
        resolved["granted_privileged"] = base.granted_privileged
    else:
        grants = _string_tuple(block["granted_privileged"] or (), f"{where}.granted_privileged")
        if WILDCARD in grants:
            raise ValueError(
                f"Capability policy {where}.granted_privileged does not accept '*'. List the "
                f"privileged toolkits explicitly. Known: {', '.join(sorted(PRIVILEGED_TOOLKITS))}."
            )
        unknown = sorted(slug for slug in grants if slug not in PRIVILEGED_TOOLKITS)
        if unknown:
            raise ValueError(
                f"Capability policy {where}.granted_privileged names unknown privileged "
                f"toolkit(s): {', '.join(unknown)}. Known: {', '.join(sorted(PRIVILEGED_TOOLKITS))}."
            )
        resolved["granted_privileged"] = grants

    return replace(base, **resolved)


# ---------------------------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------------------------


def get_default_policy() -> Optional[CapabilityPolicy]:
    """The policy the environment configures, or ``None`` when nothing is configured.

    Read fresh each call rather than memoized: the settings model itself is deliberately
    uncached, and a security control that served a stale document after an operator edited it
    would be worse than the file read it saves.
    """
    path = get_policy_settings().capability_policy_file
    if not path:
        return None
    return CapabilityPolicy.from_file(path)


def resolve_policy(
    explicit: Optional[CapabilityPolicy] = None,
    registry: Optional["Registry"] = None,
) -> CapabilityPolicy:
    """Pick the policy in force: explicit argument, then the registry, then the environment.

    Always returns a policy, never ``None``, so callers do not each re-implement the
    "unconfigured means allow everything" branch -- they check ``policy.enabled`` once.
    """
    if explicit is not None:
        return explicit
    from_registry = getattr(registry, "capability_policy", None) if registry is not None else None
    if from_registry is not None:
        return from_registry
    return get_default_policy() or CapabilityPolicy.disabled()


# ---------------------------------------------------------------------------------------------
# Violations
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyViolation:
    """One capability a definition names that its tenant may not use.

    ``path`` is config-relative (``"model"``, ``"tools[3].name"``), matching the convention
    ``egpt.registry.schema.ConfigError`` uses, so the api layer prefixes both the same way.
    """

    path: str
    message: str
    code: str
    kind: CapabilityKind
    capability: str
    tenant_id: Optional[str] = None


class PolicyViolationError(ValueError):
    """Raised at hydration when a stored config names a capability its tenant may not use.

    Distinct from ``ConfigValidationError`` on purpose. That one subclasses ``ValueError`` so a
    write handler that only knows the old contract still fails closed; this one has the opposite
    requirement -- it must *survive* the ``except Exception`` arms the hydration functions are
    built around, so a policy refusal surfaces as a 403 instead of being flattened into
    "component not found".
    """

    def __init__(
        self,
        violations: Sequence[PolicyViolation],
        component_type: str,
        component_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> None:
        self.violations: List[PolicyViolation] = list(violations)
        self.component_type = component_type
        self.component_id = component_id
        self.tenant_id = tenant_id
        named = component_id or f"<{component_type}>"
        super().__init__(
            f"Capability policy blocked {component_type} '{named}'"
            + (f" for tenant '{tenant_id}'" if tenant_id else "")
            + ": "
            + "; ".join(violation.message for violation in self.violations)
        )


# ---------------------------------------------------------------------------------------------
# Tool classification
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolOrigin:
    """What a serialized tool name could be, once the registry has been consulted.

    ``ids`` holds more than one entry only when the name is ambiguous, which the checks treat
    as "must satisfy all of them" rather than picking a winner.
    """

    name: str
    kind: Literal["tool", "mcp"]
    ids: Tuple[str, ...]
    privileged_slugs: Tuple[str, ...]
    source: Literal["registry", "backstop", "unknown"]

    @property
    def ambiguous(self) -> bool:
        return len(self.ids) > 1


_MCP_TOOLKIT_CLASS_NAMES = frozenset({"MCPTools", "MultiMCPTools"})


def _is_mcp_toolkit(toolkit: Any) -> bool:
    """True for an MCP client toolkit, by MRO class-name sniff.

    The same detection ``egpt.os.app`` uses. An ``isinstance`` check would require importing
    ``egpt.integrations.tools.mcp`` from core, which is the edge this module must not have.
    """
    return bool({cls.__name__ for cls in type(toolkit).__mro__} & _MCP_TOOLKIT_CLASS_NAMES)


def _privileged_slug_for(toolkit: Any) -> Optional[str]:
    """The privileged slug a toolkit belongs to, or None.

    Walks the whole MRO rather than testing ``type(toolkit).__module__`` alone, so a user's
    ``class MyShell(ShellTools)`` defined in their own package is still shell.
    """
    for cls in type(toolkit).__mro__:
        module = getattr(cls, "__module__", "") or ""
        for toolkit_record in PRIVILEGED_TOOLKITS.values():
            if module == toolkit_record.module or module.startswith(toolkit_record.module + "."):
                return toolkit_record.slug
    return None


def _toolkit_owners(name: str, registry: Optional["Registry"]) -> List[Any]:
    """Every live ``Toolkit`` in the registry that exposes a function called ``name``.

    Deliberately not ``Registry._entrypoint_lookup``: that index is keyed by function name and
    is last-wins, so it answers "which one would run" rather than "which ones could", and the
    second question is the one policy has to ask.
    """
    if registry is None:
        return []
    owners: List[Any] = []
    for tool in getattr(registry, "tools", ()) or ():
        functions = getattr(tool, "functions", None)
        if isinstance(functions, dict) and name in functions:
            owners.append(tool)
    return owners


def classify_tools(names: Sequence[str], registry: Optional["Registry"]) -> Dict[str, ToolOrigin]:
    """Map each serialized tool name to what it could actually be.

    Resolution order, and the reason for it:

    1. **Live toolkits win.** Every ``Toolkit`` owning the name is collected; MCP toolkits
       classify as ``mcp`` (identified by their toolkit name, the ``mcp_<slug>`` the client
       derives), others as ``tool`` under either their privileged slug or their toolkit name.
    2. **A registry that resolves the name but finds no toolkit** means a bare ``Function`` or
       callable -- a user's own tool. It is *not* run through the backstop: a tenant's
       ``def read_file`` is theirs, and blocking it on a name collision with ``FileTools`` would
       make the policy unusable.
    3. **Only when the registry cannot answer at all** does the static backstop apply. That is
       the case where nothing else can see a ``run_shell_command`` for what it is.
    """
    classified: Dict[str, ToolOrigin] = {}
    for name in names:
        if name in classified:
            continue
        classified[name] = _classify_one(name, registry)
    return classified


def _classify_one(name: str, registry: Optional["Registry"]) -> ToolOrigin:
    owners = _toolkit_owners(name, registry)

    # A toolkit can gain functions after the registry was first populated -- MCP toolkits only
    # register theirs once connected -- so a miss is retried against the live list rather than a
    # cached index. `_toolkit_owners` already reads `registry.tools` directly, so no
    # invalidation is needed; this branch exists to document why no cache is used here.
    if owners:
        mcp_ids: List[str] = []
        tool_ids: List[str] = []
        privileged: List[str] = []
        for toolkit in owners:
            if _is_mcp_toolkit(toolkit):
                mcp_ids.append(str(getattr(toolkit, "name", "") or "mcp"))
                continue
            slug = _privileged_slug_for(toolkit)
            if slug is not None:
                privileged.append(slug)
                tool_ids.append(slug)
            else:
                tool_ids.append(str(getattr(toolkit, "name", "") or name))

        # An MCP server is one capability regardless of how many remote tools it exposes, and it
        # is matched against `mcp_servers`, never against `tools`.
        if mcp_ids and not tool_ids:
            return ToolOrigin(name, "mcp", tuple(sorted(set(mcp_ids))), (), "registry")
        return ToolOrigin(
            name,
            "tool",
            tuple(sorted(set(tool_ids + mcp_ids))),
            tuple(sorted(set(privileged))),
            "registry",
        )

    if registry is not None and registry.has_entrypoint(name):
        # A bare Function or callable: the registry positively answered, so the backstop's
        # name-collision guesswork would only produce a false positive here.
        return ToolOrigin(name, "tool", (name,), (), "registry")

    backstop = PRIVILEGED_FUNCTION_NAMES.get(name)
    if backstop:
        return ToolOrigin(name, "tool", backstop, backstop, "backstop")

    return ToolOrigin(name, "tool", (name,), (), "unknown")


# ---------------------------------------------------------------------------------------------
# The walker
# ---------------------------------------------------------------------------------------------


def check_capabilities(
    config: Mapping[str, Any],
    component_type: str,
    *,
    policy: CapabilityPolicy,
    tenant_id: Optional[str],
    registry: Optional["Registry"] = None,
) -> List[PolicyViolation]:
    """Every capability in ``config`` that ``tenant_id`` may not use.

    Accumulates rather than short-circuiting, matching the validator it feeds: an author fixing
    a definition should see every refused capability in one response instead of peeling them off
    one request at a time.

    Applies to all three component types. A team config carries a ``model``, and ``tools`` reaches
    every type through the schema's ``extra="allow"``, so restricting this to agents would leave
    a team definition as the bypass.
    """
    if not policy.enabled:
        return []
    if not isinstance(config, Mapping):
        return []

    tenant = policy.for_tenant(tenant_id)
    violations: List[PolicyViolation] = []

    _check_model(config, tenant, tenant_id, violations)
    _check_tools(config, tenant, tenant_id, registry, violations)
    return violations


def _check_model(
    config: Mapping[str, Any],
    tenant: TenantPolicy,
    tenant_id: Optional[str],
    violations: List[PolicyViolation],
) -> None:
    model_data = config.get("model")
    if model_data is None:
        return

    if isinstance(model_data, str):
        provider, _, model_id = model_data.partition(":")
        provider_key = resolve_provider_key(provider.strip())
        model_id = model_id.strip()
    elif isinstance(model_data, Mapping):
        model_id = str(model_data.get("id") or "")
        provider_key = resolve_provider_key(model_data.get("provider"), model_data.get("name"))
    else:
        return  # a wrong type is the shape check's problem, not policy's

    if not provider_key and not model_id:
        return

    if not tenant.allows_model(provider_key, model_id):
        named = f"{provider_key}:{model_id}" if model_id else provider_key
        violations.append(
            PolicyViolation(
                path="model",
                message=(
                    f"Model '{named}' is not permitted for this tenant. "
                    f"Permitted models: {_render_allowed(tenant.models)}."
                ),
                code=CODE_CAPABILITY_NOT_PERMITTED,
                kind="model",
                capability=named,
                tenant_id=tenant_id,
            )
        )


def _check_tools(
    config: Mapping[str, Any],
    tenant: TenantPolicy,
    tenant_id: Optional[str],
    registry: Optional["Registry"],
    violations: List[PolicyViolation],
) -> None:
    tools = config.get("tools")
    if not isinstance(tools, list) or not tools:
        return

    names: List[str] = []
    positions: Dict[str, int] = {}
    for index, tool in enumerate(tools):
        if not isinstance(tool, Mapping):
            continue
        name = tool.get("name")
        if isinstance(name, str) and name:
            names.append(name)
            positions.setdefault(name, index)

    classified = classify_tools(names, registry)

    for name in names:
        origin = classified[name]
        path = f"tools[{positions[name]}].name"

        # Privileged first: a tenant with `tools: ["*"]` still may not reach the denylist, which
        # is the whole point of keeping the grant on its own key.
        blocked = tuple(slug for slug in origin.privileged_slugs if not tenant.grants_privileged(slug))
        if blocked:
            violations.append(
                PolicyViolation(
                    path=path,
                    message=_privileged_message(name, origin, blocked),
                    code=CODE_PRIVILEGED_TOOL_BLOCKED,
                    kind="tool",
                    capability=blocked[0],
                    tenant_id=tenant_id,
                )
            )
            continue

        if origin.kind == "mcp":
            refused = tuple(server for server in origin.ids if not tenant.allows_mcp(server))
            if refused:
                violations.append(
                    PolicyViolation(
                        path=path,
                        message=(
                            f"Tool '{name}' comes from MCP server '{refused[0]}', which is not "
                            f"permitted for this tenant. Permitted MCP servers: "
                            f"{_render_allowed(tenant.mcp_servers)}."
                        ),
                        code=CODE_CAPABILITY_NOT_PERMITTED,
                        kind="mcp",
                        capability=refused[0],
                        tenant_id=tenant_id,
                    )
                )
            continue

        refused_tools = tuple(capability for capability in origin.ids if not tenant.allows_tool(capability))
        if refused_tools:
            violations.append(
                PolicyViolation(
                    path=path,
                    message=(
                        f"Tool '{name}' resolves to capability '{refused_tools[0]}', which is not "
                        f"permitted for this tenant. Permitted tools: {_render_allowed(tenant.tools)}."
                    ),
                    code=CODE_CAPABILITY_NOT_PERMITTED,
                    kind="tool",
                    capability=refused_tools[0],
                    tenant_id=tenant_id,
                )
            )


def _privileged_message(name: str, origin: ToolOrigin, blocked: Tuple[str, ...]) -> str:
    shown = ", ".join(blocked[:_MAX_NAMED_CANDIDATES])
    if len(blocked) > _MAX_NAMED_CANDIDATES:
        shown += f", ... ({len(blocked) - _MAX_NAMED_CANDIDATES} more)"

    if origin.ambiguous:
        return (
            f"Tool '{name}' is exposed by more than one privileged toolkit ({shown}) and none of "
            "them is granted to this tenant. The stored definition does not record which toolkit "
            "a tool came from, so every candidate must be granted. Register only the specific "
            "toolkit on the Registry to disambiguate, or add the candidates to "
            "'granted_privileged'."
        )
    reason = PRIVILEGED_TOOLKITS[blocked[0]].reason
    source = " (matched by name, with no toolkit registered to narrow it)" if origin.source == "backstop" else ""
    return (
        f"Tool '{name}' belongs to the privileged toolkit '{blocked[0]}' ({reason}){source}, which "
        "is blocked for tenant-authored definitions. Add it to this tenant's 'granted_privileged' "
        "to allow it."
    )


def _render_allowed(patterns: Optional[Tuple[str, ...]]) -> str:
    if patterns is None:
        return "any"
    return ", ".join(patterns) if patterns else "<none>"


# ---------------------------------------------------------------------------------------------
# Hydration-side entry point
# ---------------------------------------------------------------------------------------------


def enforce_policy_or_raise(
    config: Mapping[str, Any],
    component_type: str,
    *,
    tenant_id: Optional[str],
    registry: Optional["Registry"] = None,
    policy: Optional[CapabilityPolicy] = None,
    component_id: Optional[str] = None,
) -> None:
    """Re-check a stored config against the policy in force, raising if it no longer passes.

    The read-side twin of the write-path check. It exists because the write check alone is
    bypassable in two ordinary ways: a policy can be tightened after a definition was published,
    and a config row can be written by something other than the components API.

    Only policy is re-checked -- not shape, not references, not a dry run. Re-running the whole
    validator at read time would reject every stored config that predates a schema tweak, and
    would cost a full hydration per request.

    Raises:
        PolicyViolationError: the config names a capability this tenant may not use.
    """
    resolved = resolve_policy(policy, registry)
    if not resolved.enabled:
        return

    violations = check_capabilities(config, component_type, policy=resolved, tenant_id=tenant_id, registry=registry)
    if not violations:
        return

    if resolved.mode == "warn":
        for violation in violations:
            log_warning(f"[capability-policy:warn] {component_type} '{component_id or '?'}': {violation.message}")
        return

    raise PolicyViolationError(violations, component_type, component_id=component_id, tenant_id=tenant_id)


__all__ = [
    "CODE_CAPABILITY_NOT_PERMITTED",
    "CODE_PRIVILEGED_TOOL_BLOCKED",
    "POLICY_VERSION",
    "PRIVILEGED_FUNCTION_NAMES",
    "PRIVILEGED_TOOLKITS",
    "CapabilityPolicy",
    "PolicyViolation",
    "PolicyViolationError",
    "PrivilegedToolkit",
    "TenantPolicy",
    "ToolOrigin",
    "check_capabilities",
    "classify_tools",
    "enforce_policy_or_raise",
    "get_default_policy",
    "resolve_policy",
]
