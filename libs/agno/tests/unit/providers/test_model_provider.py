"""Contract tests for the frozen EGPT-3.1 model-provider seam.

EGPT-3.2 (#64), 3.3 (#65), 3.4 (#66) and 3.5 (#67) are written in parallel against
``egpt/providers/model_provider.py``, so the properties they rely on have to be asserted
rather than assumed. These tests pin the three that a well-meaning edit could quietly break:

* ``ModelProvider`` is structural. An out-of-tree provider satisfies it without inheriting
  anything, which is the whole reason it is a ``Protocol`` and not an ABC.
* ``ModelResolveRequest()`` constructs with every field defaulted, and stays frozen all the
  way down -- including ``metadata``, which a plain ``@dataclass(frozen=True)`` would leave
  mutable.
* The module imports no integration at module scope, so importing the seam costs no SDK.
"""

import ast

import pytest

from egpt.models.base import Model
from egpt.providers import (
    BaseModelProvider,
    ModelCapabilities,
    ModelProvider,
    ModelProviderConfigError,
    ModelResolveRequest,
    ModelResolveResult,
    ProviderMode,
)
from tests.unit._layer_guards import imported_modules, package_root


class _StubModel(Model):
    """A concrete Model that talks to nothing.

    ``Model`` is abstract over the six provider-call methods. None of them are reached here --
    these tests only ever pass a model around -- so they raise rather than pretend to work.
    """

    def invoke(self, *args, **kwargs):
        raise NotImplementedError

    async def ainvoke(self, *args, **kwargs):
        raise NotImplementedError

    def invoke_stream(self, *args, **kwargs):
        raise NotImplementedError

    async def ainvoke_stream(self, *args, **kwargs):
        raise NotImplementedError

    def _parse_provider_response(self, *args, **kwargs):
        raise NotImplementedError

    def _parse_provider_response_delta(self, *args, **kwargs):
        raise NotImplementedError


class _StubProvider:
    """A provider that inherits nothing.

    Deliberately not a ``BaseModelProvider`` subclass: the point of the Protocol is that a
    third party can satisfy it from their own class hierarchy, and a stub that inherited from
    the convenience base would assert nothing about that.
    """

    name = "stub"

    def __init__(self, model: Model) -> None:
        self._model = model
        self.requests: list = []

    def resolve(self, request: ModelResolveRequest) -> Model:
        self.requests.append(request)
        return self._model

    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(modes=(ProviderMode.LOCAL,), provider_keys=("stub",))


def _model() -> Model:
    return _StubModel(id="stub-model", provider="stub")


# --- the Protocol is structural ---------------------------------------------------------


def test_stub_satisfies_the_protocol_without_inheriting_it():
    stub = _StubProvider(_model())

    assert isinstance(stub, ModelProvider)
    assert not isinstance(stub, BaseModelProvider)


def test_a_class_missing_a_method_does_not_satisfy_the_protocol():
    """Non-vacuity: if isinstance said yes to everything the check above would prove nothing."""

    class _NotAProvider:
        name = "nope"

    assert not isinstance(_NotAProvider(), ModelProvider)


def test_base_model_provider_satisfies_the_protocol():
    """The in-tree convenience base must itself pass the structural check."""

    class _Concrete(BaseModelProvider):
        name = "concrete"

        def resolve_detailed(self, request: ModelResolveRequest) -> ModelResolveResult:
            return ModelResolveResult(model=_model(), mode=ProviderMode.LOCAL, provider_key="stub")

    provider = _Concrete()
    assert isinstance(provider, ModelProvider)
    # resolve() delegating to resolve_detailed() is the only behavior the base actually adds.
    assert provider.resolve(ModelResolveRequest()).id == "stub-model"


def test_base_model_provider_requires_an_implementation():
    """The default resolve_detailed must fail loudly, not return something empty."""
    with pytest.raises(NotImplementedError, match="resolve_detailed"):
        BaseModelProvider().resolve(ModelResolveRequest())


# --- the request is fully defaulted and genuinely frozen ---------------------------------


def test_request_constructs_with_no_arguments():
    """The "nothing configured" request. Every provider must handle it."""
    request = ModelResolveRequest()

    assert request.requested is None
    assert request.mode is None
    assert request.role == "model"
    assert request.escalation_level == 0
    assert dict(request.metadata) == {}


def test_request_fields_are_immutable():
    request = ModelResolveRequest()

    with pytest.raises(Exception):  # FrozenInstanceError, a subclass of AttributeError
        request.role = "reasoning_model"  # type: ignore[misc]


def test_request_metadata_is_read_only():
    """``frozen=True`` freezes the binding, not the mapping. __post_init__ freezes the rest."""
    request = ModelResolveRequest(metadata={"tier": "gold"})

    assert request.metadata["tier"] == "gold"
    with pytest.raises(TypeError):
        request.metadata["tier"] = "silver"  # type: ignore[index]


def test_request_metadata_is_copied_from_the_caller():
    """Mutating the caller's dict afterwards must not change the request."""
    source = {"tier": "gold"}
    request = ModelResolveRequest(metadata=source)

    source["tier"] = "silver"
    assert request.metadata["tier"] == "gold"


def test_capabilities_notes_are_read_only():
    capabilities = ModelCapabilities(notes={"why": "test"})

    with pytest.raises(TypeError):
        capabilities.notes["why"] = "other"  # type: ignore[index]


# --- escalation -------------------------------------------------------------------------


def test_escalated_increments_the_level_and_clears_the_pin():
    """The pinned model is what proved insufficient, so it must not survive the escalation."""
    request = ModelResolveRequest(requested=_model(), max_escalations=2)

    escalated = request.escalated(reason="tool_count")

    assert escalated.escalation_level == 1
    assert escalated.escalation_reason == "tool_count"
    assert escalated.requested is None
    # replace() copies; the original is untouched, which is what makes concurrent use safe.
    assert request.escalation_level == 0
    assert request.requested is not None


def test_can_escalate_is_false_at_the_ceiling():
    request = ModelResolveRequest(max_escalations=1)

    assert request.can_escalate is True
    assert request.escalated(reason="first").can_escalate is False


def test_max_escalations_zero_never_escalates():
    assert ModelResolveRequest(max_escalations=0).can_escalate is False


def test_negative_escalation_level_is_rejected():
    with pytest.raises(ValueError, match="escalation_level must be >= 0"):
        ModelResolveRequest(escalation_level=-1)


def test_negative_max_escalations_is_rejected():
    with pytest.raises(ValueError, match="max_escalations must be >= 0"):
        ModelResolveRequest(max_escalations=-1)


# --- error type -------------------------------------------------------------------------


def test_config_error_is_a_value_error_and_carries_the_field():
    """ValueError so existing pytest.raises(ValueError) around model resolution keeps holding."""
    error = ModelProviderConfigError("base_url is required for mode A", field_name="base_url")

    assert isinstance(error, ValueError)
    assert error.field_name == "base_url"


def test_config_error_is_not_the_fallback_provider_error():
    """egpt/models/fallback.py catches ModelProviderError. A config mistake must not be swallowed."""
    from egpt.exceptions import ModelProviderError

    assert not issubclass(ModelProviderConfigError, ModelProviderError)


# --- laziness ---------------------------------------------------------------------------


def test_seam_imports_only_the_model_base_at_module_scope():
    """Mirrors registry/test_discovery_laziness.py::test_seam_itself_imports_no_integration.

    ``providers/`` sits in the same cell of ADR 0001's matrix as ``registry/``: it may name an
    integration but never import one. AST-based so it holds on a base install where none of
    the SDKs it would otherwise drag in are importable at all.
    """
    path = package_root() / "egpt" / "providers" / "model_provider.py"
    assert path.is_file(), f"the frozen contract moved: {path}"

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_scope_imports = {
        alias_module
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias_module in (
            [node.module] if isinstance(node, ast.ImportFrom) and node.module else [a.name for a in node.names]
        )
    }
    egpt_imports = sorted(name for name in module_scope_imports if name.startswith("egpt."))

    assert egpt_imports == ["egpt.models.base"], (
        f"egpt/providers/model_provider.py imports {egpt_imports} at module scope; expected only "
        "egpt.models.base. Everything else -- registry.discovery included -- must be imported "
        "inside the function that uses it."
    )


def test_the_seam_names_no_integration_anywhere():
    """Not even inside a function: construction goes through the discovery seam by string key."""
    path = package_root() / "egpt" / "providers" / "model_provider.py"
    offenders = sorted(name for name in imported_modules(path) if name.startswith("egpt.integrations"))

    assert not offenders, (
        f"The frozen contract imports integrations: {offenders}. Build models with "
        "discovery.construct(kind, provider_key, ...) instead."
    )
