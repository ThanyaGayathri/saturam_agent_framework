"""Building request params must not write into the model's own attributes (aicoe-tracker#190).

`deep_copy` shares `model` by reference on purpose -- it is on `deep_copy_field`'s heavy-resource
list -- and aicoe-tracker#172's pin carry-over is a shallow copy, which isolates *rebound
attributes* but not the contents of a container the model still shares. So a provider that writes
into one of its own dicts while assembling request params leaks that write to every copy of the
agent and to the original.

Three providers did exactly that. `OpenAIResponses._set_reasoning_request_param` aliased
`self.reasoning`; `Claude._setup_skills_configuration` appended to `self.betas`, which is often
the caller's own list; and `Gemini.get_request_params` aliased `self.generation_config` into a
local and then wrote into it a dozen times. The fields they happened to mutate carry no
credential, which is why this was filed rather than treated as a leak -- but the shape is the
counter-example to "a shallow copy is enough", and the same pattern applied to
`default_headers["Authorization"]` or `client_params` would be a genuine cross-tenant credential
leak. The last class here is the guard against that next provider.

This test lives at `tests/unit/models/` rather than under one provider's folder because the guard
scans the whole `integrations/models/` tree, matching where the other cross-provider structural
checks (`test_same_model.py`, `test_pricing.py`) live.
"""

import ast
import importlib.util
import pathlib
from copy import copy

import pytest


def _sdk_missing(module: str) -> bool:
    """Whether an optional provider SDK is absent.

    Each of the three provider modules exercised below re-raises `ImportError` at module scope
    when its SDK is missing, so those classes have to be gated. The scan class must *not* be --
    it reads source with `ast` and needs nothing installed -- which is why this is a per-class
    mark rather than a module-scope `pytest.importorskip`.
    """
    try:
        return importlib.util.find_spec(module) is None
    except (ImportError, ValueError):
        return True


_needs_openai = pytest.mark.skipif(
    _sdk_missing("openai"), reason="OpenAIResponses imports the OpenAI SDK at module scope"
)
_needs_anthropic = pytest.mark.skipif(
    _sdk_missing("anthropic"), reason="Claude imports the Anthropic SDK at module scope"
)
_needs_google = pytest.mark.skipif(_sdk_missing("google.genai"), reason="Gemini imports google-genai at module scope")


@_needs_openai
class TestBuildingParamsLeavesTheModelAlone:
    @staticmethod
    def _model(**kwargs):
        from egpt.integrations.models.openai.responses import OpenAIResponses

        return OpenAIResponses(**kwargs)

    def test_the_reasoning_dict_is_not_mutated(self):
        """The reported symptom: building params rewrote the model's own `reasoning`."""
        model = self._model(id="gpt-5.5", reasoning={"summary": "auto"}, reasoning_effort="high")

        params = model._set_reasoning_request_param({})

        assert model.reasoning == {"summary": "auto"}, "building request params mutated the model"
        assert params["reasoning"] == {"summary": "auto", "effort": "high"}, "the params must still be right"

    def test_a_copys_param_build_is_invisible_to_its_siblings(self):
        """Why it matters: `deep_copy` shares the model, so the dict is shared by every copy."""
        model = self._model(id="gpt-5.5", reasoning={"summary": "auto"})
        first, second = copy(model), copy(model)
        first.reasoning_effort = "high"

        first._set_reasoning_request_param({})

        assert second.reasoning == {"summary": "auto"}, "a sibling copy saw another's request params"
        assert model.reasoning == {"summary": "auto"}, "the original saw a copy's request params"

    def test_the_empty_case_still_works(self):
        """`self.reasoning or {}` already built a fresh dict when falsy -- which is why a first
        attempt to reproduce the bug fails. Pinned so the fix is not mistaken for the whole story."""
        model = self._model(id="gpt-5.5", reasoning_effort="low")

        params = model._set_reasoning_request_param({})

        assert params["reasoning"] == {"effort": "low"}
        assert model.reasoning in (None, {}), "an absent reasoning dict must stay absent"


@_needs_anthropic
class TestClaudeSkillsDoNotMutateACallersList:
    """The second instance, found by the scan below rather than by the ticket (aicoe-tracker#190).

    `_setup_skills_configuration` appended the required betas to `self.betas`. Same class as the
    reasoning dict and slightly worse in one respect: the list is often the caller's own, so the
    write escaped the model entirely.
    """

    def test_a_callers_list_is_not_appended_to(self):
        from egpt.integrations.models.anthropic.claude import Claude

        caller_list = ["my-own-beta"]
        model = Claude(id="claude-x", betas=caller_list)

        model._setup_skills_configuration()

        assert caller_list == ["my-own-beta"], "the caller's own list was mutated"
        assert "skills-2025-10-02" in (model.betas or []), "the model must still get its betas"

    def test_a_copys_configuration_is_invisible_to_its_siblings(self):
        from egpt.integrations.models.anthropic.claude import Claude

        model = Claude(id="claude-x", betas=["my-own-beta"])
        first, second = copy(model), copy(model)

        first._setup_skills_configuration()

        assert second.betas == ["my-own-beta"], "a sibling copy saw another's beta configuration"

    def test_the_unset_case_still_configures(self):
        from egpt.integrations.models.anthropic.claude import Claude

        model = Claude(id="claude-x")

        model._setup_skills_configuration()

        assert model.betas is not None and "skills-2025-10-02" in model.betas


@_needs_google
class TestGeminiGenerationConfigIsNotMutated:
    """The third instance -- the one the first version of the guard below could not see.

    `get_request_params` bound `config = self.generation_config` and then wrote into `config`
    roughly a dozen times (`config.update(...)`, `config["tools"] = ...`, ...). Because the writes
    went through a local name rather than through `self`, the original guard's direct-receiver
    check walked straight past it. The alias tracking added below is what closes that.
    """

    @staticmethod
    def _model(**kwargs):
        from egpt.integrations.models.google.gemini import Gemini

        return Gemini(id="gemini-2.5-flash", **kwargs)

    def test_the_generation_config_dict_is_not_mutated(self):
        """The plain-dict path is the one that aliased; `model_dump()` already returned a copy."""
        model = self._model(generation_config={"candidate_count": 1})

        model.get_request_params()

        assert model.generation_config == {"candidate_count": 1}, "building request params mutated the model"

    def test_a_copys_param_build_is_invisible_to_its_siblings(self):
        """The production path: `Agent.deep_copy()` shares the model, so the dict is shared."""
        model = self._model(generation_config={"candidate_count": 1})
        first, second = copy(model), copy(model)
        first.temperature = 0.9

        first.get_request_params()

        assert second.generation_config == {"candidate_count": 1}, "a sibling copy saw another's request params"
        assert model.generation_config == {"candidate_count": 1}, "the original saw a copy's request params"

    def test_a_callers_dict_is_not_written_into(self):
        """The dict is usually the caller's own object, so the write escaped the model entirely."""
        caller_config = {"candidate_count": 1}
        model = self._model(generation_config=caller_config)

        model.get_request_params(system_message="you are a helpful assistant")

        assert caller_config == {"candidate_count": 1}, "the caller's own dict was mutated"

    def test_the_params_are_still_built_correctly(self):
        """The fix must not cost the caller their own generation config."""
        model = self._model(generation_config={"candidate_count": 1}, temperature=0.5)

        params = model.get_request_params()

        config = params["config"]
        assert config.candidate_count == 1, "the caller's generation config must still reach the request"
        assert config.temperature == 0.5, "the model's own settings must still reach the request"


class TestNoProviderWritesIntoItsOwnContainers:
    """The guard for the next provider, not for the ones that were fixed.

    A shallow copy isolates a rebound attribute (`self.api_key = ...`) but not a mutated container
    (`self.default_headers[...] = ...`). This scans the provider tree for the second shape so the
    class of bug cannot come back somewhere that does carry a credential.

    Coverage is deliberately stated as shapes rather than as "anywhere". The scan flags item
    assignment, augmented assignment, `del`, and the mutating-method calls listed in `_MUTATORS`,
    reached either directly through `self.<attr>` (including nested paths like `self.a.b[...]`)
    or through a local name bound to `self.<attr>` somewhere in the same function. What it does
    not model is aliasing that crosses a function boundary, is laundered through a container or a
    call, or reaches the attribute via `getattr`. Those remain reviewer's work.
    """

    #: Methods that mutate the receiver in place. `pop`/`clear`/`popitem`/`discard` are here for
    #: symmetry -- removing an entry from a shared container is the same hazard as adding one.
    _MUTATORS = frozenset(
        {
            "update",
            "setdefault",
            "pop",
            "popitem",
            "clear",
            "append",
            "extend",
            "insert",
            "remove",
            "add",
            "discard",
            "sort",
            "reverse",
        }
    )

    @staticmethod
    def _provider_sources():
        root = pathlib.Path(__file__).resolve().parents[3] / "egpt" / "integrations" / "models"
        assert root.is_dir(), f"provider tree not found at {root}"
        return sorted(root.rglob("*.py"))

    @staticmethod
    def _self_attr_path(node):
        """The `self.<path>` an expression names, or None.

        `self.betas` -> `"betas"`, `self.a.b` -> `"a.b"`. Anything that is not a bare attribute
        chain rooted at `self` -- `dict(self.x)`, `self.x.copy()`, `{**self.x}`,
        `self.x.model_dump()`, a subscript, a literal -- returns None, which is exactly the
        "this expression built a new object" answer the alias tracking needs.
        """
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name) and node.id == "self" and parts:
            return ".".join(reversed(parts))
        return None

    @classmethod
    def _receiver(cls, node, aliases):
        """What a write's receiver expression resolves to, as a display string, or None.

        Either `self.<path>` directly, or a local name that was bound to one somewhere in this
        function.
        """
        direct = cls._self_attr_path(node)
        if direct is not None:
            return f"self.{direct}"
        if isinstance(node, ast.Name) and node.id in aliases:
            return f"{node.id} (aliases self.{aliases[node.id]})"
        return None

    @classmethod
    def _offenders_in(cls, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = set()

        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            # Pass 1: every local bound to a bare `self.<attr>` anywhere in this function.
            # Collected for the whole function rather than in statement order, so a write is
            # flagged regardless of which branch established the alias -- `gemini.py` bound the
            # alias in an `else` and wrote to it after the `if`.
            aliases = {}
            for node in ast.walk(func):
                if isinstance(node, ast.Assign):
                    source = cls._self_attr_path(node.value)
                    if source is not None:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                aliases[target.id] = source

            # Pass 2: writes reaching a tracked receiver.
            for node in ast.walk(func):
                targets = ()
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    targets = (node.target,)
                elif isinstance(node, ast.Delete):
                    targets = node.targets

                for target in targets:
                    if not isinstance(target, ast.Subscript):
                        continue
                    receiver = cls._receiver(target.value, aliases)
                    if receiver is not None:
                        verb = "del " if isinstance(node, ast.Delete) else ""
                        offenders.add(f"{path.name}:{node.lineno}: {verb}{receiver}[...]")

                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in cls._MUTATORS
                ):
                    receiver = cls._receiver(node.func.value, aliases)
                    if receiver is not None:
                        offenders.add(f"{path.name}:{node.lineno}: {receiver}.{node.func.attr}(...)")

        return offenders

    def test_the_scan_actually_sees_the_provider_tree(self):
        """A scan whose root went stale would pass by finding nothing."""
        sources = self._provider_sources()
        assert len(sources) > 30, f"only {len(sources)} provider files found; the scan root is probably stale"

    def test_the_scan_catches_the_shapes_it_claims_to(self):
        """The guard's own regression test.

        The first version of this scan shipped green against a tree that contained a live bug
        (`gemini.py`'s local alias), because it only matched a direct `self.<attr>` receiver. A
        structural guard that silently stops matching is worse than no guard, so the shapes are
        pinned against a synthetic source rather than trusted to the live tree -- which, once
        fixed, has no positive examples left to prove the scan still works.
        """
        import tempfile

        source = """
class P:
    def direct(self):
        self.headers["Authorization"] = "x"      # item assignment
        self.headers.update({"a": "b"})          # mutator call
        self.counts["n"] += 1                    # augmented assignment
        del self.headers["Authorization"]        # delete
        self.client.opts["timeout"] = 1          # nested path

    def aliased(self):
        config = self.generation_config          # the gemini.py shape
        config.update({"a": "b"})
        config["tools"] = []

    def safe(self):
        config = dict(self.generation_config)    # copied, not aliased
        config["tools"] = []
        other = {"a": "b"}
        other.update({"c": "d"})
        self.betas = [*self.betas, "new"]        # rebinding is fine
"""
        with tempfile.TemporaryDirectory() as tmp:
            probe = pathlib.Path(tmp) / "probe.py"
            probe.write_text(source, encoding="utf-8")
            found = self._offenders_in(probe)

        shapes = {entry.split(": ", 1)[1] for entry in found}
        assert shapes == {
            "self.headers[...]",
            "self.headers.update(...)",
            "self.counts[...]",
            "del self.headers[...]",
            "self.client.opts[...]",
            "config (aliases self.generation_config).update(...)",
            "config (aliases self.generation_config)[...]",
        }, f"the scan no longer matches the shapes it documents; got {sorted(shapes)}"

    def test_no_model_attribute_is_mutated_in_place(self):
        """`self.<attr>[...] = ...`, `self.<attr>.update(...)`, and the same writes reached
        through a local alias are the shapes a shallow copy cannot isolate. Rebinding
        (`self.<attr> = ...`) is fine and is not flagged."""
        offenders = set()
        for path in self._provider_sources():
            offenders |= self._offenders_in(path)

        assert not offenders, (
            "a provider mutates one of its own containers in place. `deep_copy` shares the model "
            "by reference, so that write reaches every copy of the agent and the original "
            "(aicoe-tracker#190). Rebind the attribute, or copy it before writing:\n  " + "\n  ".join(sorted(offenders))
        )
