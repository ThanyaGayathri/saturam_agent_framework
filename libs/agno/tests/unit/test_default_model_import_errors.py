"""Tests for the default-model fallback paths when the `openai` SDK is unavailable.

Provider modules raise a bare ``ImportError`` when their SDK is missing (minted in
``egpt/integrations/models/openai/chat.py``), not ``ModuleNotFoundError``. Since
``ModuleNotFoundError`` is a *subclass* of ``ImportError`` and not the reverse, the handlers that
caught only ``ModuleNotFoundError`` never fired: users saw the raw SDK message instead of the
guidance telling them they can pass a ``model`` instead of installing anything, and the
``exit(1)`` branches were unreachable.

Each test here monkeypatches the ``get_model`` symbol in the module under test, so it exercises
the handler without needing an environment that actually lacks the SDK.
"""

import pytest

from egpt.exceptions import EvalError

# The exact exception provider modules raise when their SDK is absent.
_SDK_MISSING = ImportError("`openai` not installed. Please install using `pip install openai`")


def _raise_sdk_missing(*_args, **_kwargs):
    raise _SDK_MISSING


def _raise_module_not_found(*_args, **_kwargs):
    # ModuleNotFoundError must keep working: it is a subclass of ImportError, so widening the
    # handlers must not narrow what they used to catch.
    raise ModuleNotFoundError("No module named 'openai'")


@pytest.fixture(params=[_raise_sdk_missing, _raise_module_not_found], ids=["ImportError", "ModuleNotFoundError"])
def sdk_missing(request):
    return request.param


def test_agent_default_model_reports_actionable_guidance(monkeypatch, sdk_missing):
    """The agent path must surface "provide a `model`", not just the SDK's install hint."""
    from egpt.agent import Agent
    from egpt.agent import _init as agent_init

    monkeypatch.setattr(agent_init, "get_model", sdk_missing)

    with pytest.raises(ImportError) as exc:
        agent_init.set_default_model(Agent())

    assert "Please provide a `model` or install `openai`" in str(exc.value)


def test_team_default_model_exits_with_guidance(monkeypatch, sdk_missing):
    """The team path logs guidance and exits; before the fix the exception escaped uncaught."""
    from egpt.team import _init as team_init

    monkeypatch.setattr(team_init, "get_model", sdk_missing)

    class _Team:
        model = None

    with pytest.raises(SystemExit):
        team_init._set_default_model(_Team())


def test_culture_manager_exits_with_guidance(monkeypatch, sdk_missing):
    from egpt.culture import manager as culture_manager

    # Constructed before patching: CultureManager.__init__ calls get_model itself, so patching
    # first would fail in the constructor rather than in the branch under test.
    mgr = culture_manager.CultureManager()
    mgr.model = None
    monkeypatch.setattr(culture_manager, "get_model", sdk_missing)

    with pytest.raises(SystemExit):
        mgr.get_model()


def test_memory_manager_exits_with_guidance(monkeypatch, sdk_missing):
    from egpt.memory import manager as memory_manager

    mgr = memory_manager.MemoryManager()
    mgr.model = None
    monkeypatch.setattr(memory_manager, "get_model", sdk_missing)

    with pytest.raises(SystemExit):
        mgr.get_model()


def test_accuracy_eval_raises_eval_error(monkeypatch, sdk_missing):
    from egpt.eval import accuracy as accuracy_mod

    monkeypatch.setattr(accuracy_mod, "get_model", sdk_missing)

    ev = accuracy_mod.AccuracyEval(input="x", expected_output="y")
    with pytest.raises(EvalError) as exc:
        ev.get_evaluator_agent()

    assert "default model provider" in str(exc.value)


def test_agent_as_judge_eval_raises_eval_error(monkeypatch, sdk_missing):
    from egpt.eval import agent_as_judge as judge_mod

    monkeypatch.setattr(judge_mod, "get_model", sdk_missing)

    ev = judge_mod.AgentAsJudgeEval(criteria="be correct")
    with pytest.raises(EvalError) as exc:
        ev.get_evaluator_agent()

    assert "default model provider" in str(exc.value)


# --- The None guard (aicoe-tracker#152) -------------------------------------------------------
#
# get_model is typed Optional[Model]. It returns None only for a None argument, which these call
# sites cannot pass, so this is a latent-invariant guard rather than a live bug: if the contract
# ever widens, these must fail loudly instead of handing None to the evaluator agent.


def test_agent_rejects_unresolvable_model(monkeypatch):
    from egpt.agent import Agent
    from egpt.agent import _init as agent_init

    monkeypatch.setattr(agent_init, "get_model", lambda *_a, **_k: None)

    with pytest.raises(ValueError, match="Could not resolve the default model"):
        agent_init.set_default_model(Agent())


def test_team_rejects_unresolvable_model(monkeypatch):
    from egpt.team import _init as team_init

    monkeypatch.setattr(team_init, "get_model", lambda *_a, **_k: None)

    class _Team:
        model = None

    with pytest.raises(ValueError, match="Could not resolve the default model"):
        team_init._set_default_model(_Team())


def test_accuracy_eval_rejects_unresolvable_model(monkeypatch):
    from egpt.eval import accuracy as accuracy_mod

    monkeypatch.setattr(accuracy_mod, "get_model", lambda *_a, **_k: None)

    ev = accuracy_mod.AccuracyEval(input="x", expected_output="y")
    with pytest.raises(EvalError) as exc:
        ev.get_evaluator_agent()

    assert "Could not resolve the default evaluator model" in str(exc.value)


def test_agent_as_judge_eval_rejects_unresolvable_model(monkeypatch):
    from egpt.eval import agent_as_judge as judge_mod

    monkeypatch.setattr(judge_mod, "get_model", lambda *_a, **_k: None)

    ev = judge_mod.AgentAsJudgeEval(criteria="be correct")
    with pytest.raises(EvalError) as exc:
        ev.get_evaluator_agent()

    assert "Could not resolve the default evaluator model" in str(exc.value)
