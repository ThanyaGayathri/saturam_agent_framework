"""aicoe-tracker#183: nothing structurally stops a run-path site from reading
``agent.model``/``team.model`` directly instead of ``egpt.run.base.run_model(component,
run_context)``.

aicoe-tracker#166 established the invariant: the model that serves a run must be read through
``run_model()``, because ``component.model`` is per-*object* configuration shared across every
run, while an escalating provider's resolution is per-*run* state carried on ``RunContext``.
Reading the attribute directly silently reintroduces aicoe-tracker#166's original bug --
cross-run/cross-tenant model bleed, or an escalated run's usage attributed to the wrong model.

That invariant held by convention and comments alone across ~40 call sites, and it was
independently missed twice in code review before this guard existed:

- The four streaming-event sites in ``agent/_response.py``/``team/_response.py`` passed
  ``agent.model``/``team.model`` straight into the streamed event payload, so an escalated run's
  *events* reported the wrong model even though the *model call itself* was correctly resolved a
  few lines above (fixed in aicoe-tracker#182's PR).
- ``eval/accuracy.py`` attributed a completed run's DB/telemetry record to
  ``self.agent.model``/``self.team.model`` instead of the model that actually served it.

This guard scans every module under ``egpt/agent/``, ``egpt/team/``, and ``egpt/eval/`` for a
direct read of ``agent.model``/``team.model``/``self.agent.model``/``self.team.model`` and fails
on anything with no recorded reason. Like ``test_env_read_guard.py``, the allowlist is a ratchet
checked in both directions: an unrecorded access fails ("route it through ``run_model()``, or add
an entry with a reason"), and a stale entry with no matching access also fails ("the backlog
shrank -- delete this entry").

The detector does not try to distinguish "reads the model to call it" from "checks the model is
configured" (``if agent.model is None: ...``) -- both are `Load`-context reads of the same
attribute, and the two are indistinguishable by shape alone (a presence check and a value read
look identical to an AST walk; only the surrounding logic tells them apart). Presence checks are
legitimate and expected to stay, so they get allowlist entries too, same as every other read that
is not a serving-model bypass -- the allowlist's own reason column is where that distinction
lives, not the detector.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, Set, Tuple

from tests.unit._layer_guards import _parse, package_root

# --------------------------------------------------------------------------- #
# Detector
# --------------------------------------------------------------------------- #

_TARGET_ATTR = "model"
_TARGET_BASES = frozenset({"agent", "team"})


def find_model_reads(path: Path) -> Set[Tuple[int, str]]:
    """Every direct ``agent.model``/``team.model``-shaped read `path` makes.

    Returns ``(lineno, marker)`` pairs. ``marker`` is ``"agent.model"``/``"team.model"`` for a
    read off a bare ``agent``/``team`` name, or ``"self.agent.model"``/``"self.team.model"`` for
    the one shape `` egpt/eval/accuracy.py`` needs (``self.agent``/``self.team`` attributes on
    an evaluator instance). Assignment targets (``agent.model = ...``) are not reads and are
    excluded; augmented/annotated assignment targets are excluded the same way.

    Deliberately does not resolve aliases (``a = agent; a.model``) or attempt data-flow -- every
    known call site in this tree, both fixed and outstanding, writes the attribute access
    directly. A guard that chased aliases would be harder to reason about than the sites it
    protects; if that ever proves too narrow, the miss will show up as a real bug slipping past
    review, same as the two misses that motivated this guard in the first place.
    """
    tree = _parse(path)

    store_ids: Set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute):
                    store_ids.add(id(target))

    found: Set[Tuple[int, str]] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Attribute) and node.attr == _TARGET_ATTR):
            continue
        if id(node) in store_ids:
            continue

        base = node.value
        if isinstance(base, ast.Name) and base.id in _TARGET_BASES:
            found.add((node.lineno, f"{base.id}.model"))
        elif (
            isinstance(base, ast.Attribute)
            and base.attr in _TARGET_BASES
            and isinstance(base.value, ast.Name)
            and base.value.id == "self"
        ):
            found.add((node.lineno, f"self.{base.attr}.model"))

    return found


def _scan(root: Path) -> Tuple[int, Set[Tuple[str, int, str]]]:
    checked = 0
    found: Set[Tuple[str, int, str]] = set()
    for path in sorted(root.rglob("*.py")):
        checked += 1
        for lineno, marker in find_model_reads(path):
            found.add((path.relative_to(package_root()).as_posix(), lineno, marker))
    return checked, found


# --------------------------------------------------------------------------- #
# Allowlist -- an exact inventory of every direct read, and why.
# --------------------------------------------------------------------------- #
#
# Line numbers are part of the record on purpose: an entry that survives a refactor untouched is
# a real signal that the read is still exactly where it was, not a stale approximation.

ALLOWED_MODEL_READS: Dict[Tuple[str, int, str], str] = {
    # -- construction / one-time setup, before any run starts --
    ("egpt/agent/_init.py", 229, "agent.model"): "presence check before the first-assignment branch",
    ("egpt/agent/_init.py", 263, "agent.model"): "wires CultureManager to the configured model at construction",
    ("egpt/agent/_init.py", 266, "agent.model"): "wires CultureManager to the configured model at construction",
    ("egpt/agent/_init.py", 281, "agent.model"): "wires MemoryManager to the configured model at construction",
    ("egpt/agent/_init.py", 284, "agent.model"): "wires MemoryManager to the configured model at construction",
    ("egpt/agent/_init.py", 320, "agent.model"): "wires LearningMachine to the configured model at construction",
    ("egpt/agent/_init.py", 328, "agent.model"): "wires LearningMachine to the configured model at construction",
    ("egpt/agent/_init.py", 338, "agent.model"): "wires SessionSummaryManager to the configured model at construction",
    ("egpt/agent/_init.py", 342, "agent.model"): "wires SessionSummaryManager to the configured model at construction",
    ("egpt/agent/_init.py", 353, "agent.model"): "wires CompressionManager to the configured model at construction",
    ("egpt/agent/_init.py", 357, "agent.model"): "wires CompressionManager to the configured model at construction",
    ("egpt/agent/_init.py", 393, "agent.model"): "presence check before model-type coercion at construction",
    ("egpt/agent/_init.py", 395, "agent.model"): "coerces the configured model at construction, twice (arg + assign)",
    ("egpt/agent/_init.py", 396, "agent.model"): "presence check after coercion at construction",
    ("egpt/agent/_init.py", 397, "agent.model"): "stamps model_type on the configured model at construction",
    ("egpt/agent/_init.py", 404, "agent.model"): "pins the caller's configured model at construction",
    ("egpt/agent/_init.py", 422, "agent.model"): "wires CompressionManager to the configured model at construction",
    ("egpt/team/_init.py", 521, "team.model"): "presence check before member-model inheritance at construction",
    ("egpt/team/_init.py", 522, "team.model"): "member-model inheritance is config propagation, not run serving",
    ("egpt/team/_init.py", 523, "team.model"): "member-model inheritance is config propagation, not run serving",
    ("egpt/team/_init.py", 566, "team.model"): "presence check before the first-assignment branch",
    ("egpt/team/_init.py", 606, "team.model"): "wires MemoryManager to the configured model at construction",
    ("egpt/team/_init.py", 609, "team.model"): "wires MemoryManager to the configured model at construction",
    ("egpt/team/_init.py", 621, "team.model"): "wires SessionSummaryManager to the configured model at construction",
    ("egpt/team/_init.py", 625, "team.model"): "wires SessionSummaryManager to the configured model at construction",
    ("egpt/team/_init.py", 634, "team.model"): "wires CompressionManager to the configured model at construction",
    ("egpt/team/_init.py", 638, "team.model"): "wires CompressionManager to the configured model at construction",
    ("egpt/team/_init.py", 642, "team.model"): "wires CompressionManager to the configured model at construction",
    ("egpt/team/_init.py", 669, "team.model"): "wires LearningMachine to the configured model at construction",
    ("egpt/team/_init.py", 676, "team.model"): "wires LearningMachine to the configured model at construction",
    ("egpt/team/_init.py", 732, "team.model"): "presence check before model-type coercion at construction",
    ("egpt/team/_init.py", 733, "team.model"): "coerces the configured model at construction, twice (arg + assign)",
    # -- pin/deep-copy bookkeeping: object-identity logic over the configured model, not serving --
    ("egpt/agent/_utils.py", 236, "agent.model"): "deep-copy pin bookkeeping, not run serving",
    ("egpt/agent/_utils.py", 250, "agent.model"): "deep-copy pin bookkeeping, not run serving",
    ("egpt/team/_utils.py", 183, "team.model"): "deep-copy pin bookkeeping, not run serving",
    ("egpt/team/_utils.py", 187, "team.model"): "deep-copy pin bookkeeping, not run serving",
    # -- telemetry's pre-resolution config snapshot: explicitly the expected shape --
    ("egpt/team/_telemetry.py", 20, "team.model"): "get_team_data's pre-resolution config snapshot",
    ("egpt/team/_telemetry.py", 21, "team.model"): "get_team_data's pre-resolution config snapshot",
    # -- serialization: to_dict()/config building, not response generation --
    ("egpt/agent/_storage.py", 387, "agent.model"): "session-creation bootstrap message, static role string only",
    ("egpt/agent/_storage.py", 452, "agent.model"): "session-creation bootstrap message, static role string only",
    ("egpt/agent/_storage.py", 474, "agent.model"): "presence check before to_dict() serialization",
    ("egpt/agent/_storage.py", 475, "agent.model"): "to_dict() serialization of the configured model",
    ("egpt/agent/_storage.py", 491, "agent.model"): "presence check before config-dict serialization",
    ("egpt/agent/_storage.py", 492, "agent.model"): "isinstance check before config-dict serialization",
    ("egpt/agent/_storage.py", 493, "agent.model"): "config-dict serialization of the configured model",
    ("egpt/agent/_storage.py", 495, "agent.model"): "config-dict serialization of the configured model",
    ("egpt/agent/_storage.py", 602, "agent.model"): "presence check before parse_tools() call",
    ("egpt/agent/_storage.py", 605, "agent.model"): "parse_tools() reads the configured model to introspect tools",
    ("egpt/team/_storage.py", 325, "team.model"): "session-creation bootstrap message, static role string only",
    ("egpt/team/_storage.py", 392, "team.model"): "session-creation bootstrap message, static role string only",
    ("egpt/team/_storage.py", 465, "team.model"): "presence check before to_dict() serialization",
    ("egpt/team/_storage.py", 466, "team.model"): "to_dict() serialization of the configured model",
    # -- presence guards left in place deliberately (mirrors the pattern in already-fixed code,
    # -- e.g. agent/_response.py's reason()/areason(), which keep `agent.model is not None` as
    # -- the guard while reading `run_model(agent, run_context)` for the value) --
    ("egpt/agent/_response.py", 268, "agent.model"): "presence guard; the value read is run_model(...)",
    ("egpt/agent/_response.py", 315, "agent.model"): "presence guard; the value read is run_model(...)",
    ("egpt/agent/_messages.py", 168, "agent.model"): "presence guard before building the default system message",
    ("egpt/agent/_messages.py", 524, "agent.model"): "presence guard before building the default system message",
    ("egpt/team/_response.py", 742, "team.model"): "presence guard; the value read is run_model(...)",
    ("egpt/team/_response.py", 787, "team.model"): "presence guard; the value read is run_model(...)",
    ("egpt/team/_messages.py", 328, "team.model"): "presence guard before the JSON-output-prompt branch",
    # -- eval/accuracy.py's *_with_output() paths never call .run()/.arun(): there is no run to
    # -- resolve a model against, so reading the configured model is the only correct answer --
    ("egpt/eval/accuracy.py", 764, "self.agent.model"): "run_with_output() never calls .run(); no run to resolve",
    ("egpt/eval/accuracy.py", 765, "self.agent.model"): "run_with_output() never calls .run(); no run to resolve",
    ("egpt/eval/accuracy.py", 770, "self.team.model"): "run_with_output() never calls .run(); no run to resolve",
    ("egpt/eval/accuracy.py", 771, "self.team.model"): "run_with_output() never calls .run(); no run to resolve",
    ("egpt/eval/accuracy.py", 883, "self.agent.model"): "arun_with_output() never calls .arun(); no run to resolve",
    ("egpt/eval/accuracy.py", 884, "self.agent.model"): "arun_with_output() never calls .arun(); no run to resolve",
    ("egpt/eval/accuracy.py", 889, "self.team.model"): "arun_with_output() never calls .arun(); no run to resolve",
    ("egpt/eval/accuracy.py", 890, "self.team.model"): "arun_with_output() never calls .arun(); no run to resolve",
    (
        "egpt/eval/accuracy.py",
        939,
        "self.agent.model",
    ): "_get_telemetry_data()'s no-override fallback -- correct only when no run happened",
    (
        "egpt/eval/accuracy.py",
        939,
        "self.team.model",
    ): "_get_telemetry_data()'s no-override fallback -- correct only when no run happened",
    # -- known outstanding gap: _tools.py's tool-execution helpers (run_tool, arun_tool,
    # -- reject_tool_call, handle_*_tool_update, handle_tool_call_updates*) read agent.model/
    # -- team.model directly. run_context *is* already in scope at every call site (all live in
    # -- _continue_run*, which declares it as a required parameter, after apply_run_model has
    # -- already run), so wiring these through run_model() is a one-keyword-argument-per-site
    # -- change, not a ripple. Deferred anyway: none of the five uses (tool_message_role,
    # -- get_function_call_to_run_from_tool_execution, create_function_call_result,
    # -- run_function_call, arun_function_calls) issues a model request or reads a provider
    # -- client/base_url -- they're local formatting/execution helpers, so the divergence is
    # -- attribution-only, not misrouted traffic. Tracked as a follow-up to aicoe-tracker#183,
    # -- not fixed here. Every entry below is the same gap, not five different ones.
    (
        "egpt/agent/_tools.py",
        521,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        531,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        555,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        566,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        579,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        587,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        651,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        653,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        656,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        735,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        736,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        738,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        764,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        767,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        770,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        849,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        901,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        951,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    (
        "egpt/agent/_tools.py",
        1002,
        "agent.model",
    ): "tool-call handling has no run_context yet -- aicoe-tracker#183 follow-up",
    # -- known outstanding gap: generate_session_name() is a separate public entrypoint, not
    # -- reachable from .run()/.arun() -- adjacent to the run path rather than part of it, and a
    # -- live model call with no run_context in scope at all. Tracked alongside _tools.py.
    ("egpt/agent/_session.py", 450, "agent.model"): "generate_session_name() is not reachable from .run()/.arun()",
    ("egpt/agent/_session.py", 471, "agent.model"): "generate_session_name() is not reachable from .run()/.arun()",
    ("egpt/team/_session.py", 296, "team.model"): "generate_session_name() is not reachable from .run()/.arun()",
    ("egpt/team/_session.py", 318, "team.model"): "generate_session_name() is not reachable from .run()/.arun()",
    # -- the streaming-event *cast* lines. `agent.model = cast(Model, agent.model)` narrows the
    # -- type before the function body, which otherwise reads `served_model = run_model(...)` for
    # -- every event-payload attribution -- the cast line itself is the only remaining read here.
    (
        "egpt/agent/_response.py",
        1061,
        "agent.model",
    ): "type-narrowing cast; the function body reads served_model = run_model(...)",
    (
        "egpt/agent/_response.py",
        1227,
        "agent.model",
    ): "type-narrowing cast; the function body reads served_model = run_model(...)",
    (
        "egpt/team/_response.py",
        1017,
        "team.model",
    ): "type-narrowing cast; the function body reads served_model = run_model(...)",
    (
        "egpt/team/_response.py",
        1182,
        "team.model",
    ): "type-narrowing cast; the function body reads served_model = run_model(...)",
    # -- os/: config-surface reads (schema serialization, /models discovery, per-request model
    # -- overrides applied before a run starts). None of these are on a post-run attribution
    # -- path -- the one that was (os/routers/evals/utils.py's reliability-eval reads) is fixed
    # -- above this PR's diff, not allowlisted.
    ("egpt/os/router.py", 247, "agent.model"): "/models discovery lists configured models, not served ones",
    ("egpt/os/router.py", 258, "team.model"): "/models discovery lists configured models, not served ones",
    ("egpt/os/routers/agents/schema.py", 158, "agent.model"): "AgentResponse schema serializes the configured model",
    ("egpt/os/routers/agents/schema.py", 159, "agent.model"): "AgentResponse schema serializes the configured model",
    ("egpt/os/routers/agents/schema.py", 160, "agent.model"): "AgentResponse schema serializes the configured model",
    (
        "egpt/os/routers/evals/evals.py",
        431,
        "agent.model",
    ): "pre-run: compares the configured model against a requested override before the eval runs",
    (
        "egpt/os/routers/evals/evals.py",
        435,
        "agent.model",
    ): "pre-run: compares the configured model against a requested override before the eval runs",
    (
        "egpt/os/routers/evals/evals.py",
        436,
        "agent.model",
    ): "pre-run: compares the configured model against a requested override before the eval runs",
    (
        "egpt/os/routers/evals/evals.py",
        458,
        "team.model",
    ): "pre-run: compares the configured model against a requested override before the eval runs",
    (
        "egpt/os/routers/evals/evals.py",
        462,
        "team.model",
    ): "pre-run: compares the configured model against a requested override before the eval runs",
    (
        "egpt/os/routers/evals/evals.py",
        463,
        "team.model",
    ): "pre-run: compares the configured model against a requested override before the eval runs",
    (
        "egpt/os/routers/teams/schema.py",
        129,
        "team.model",
    ): "pre-run tool preview for a config/schema endpoint -- shows what would run, not what did",
    ("egpt/os/routers/teams/schema.py", 149, "team.model"): "TeamResponse schema serializes the configured model",
    ("egpt/os/routers/teams/schema.py", 150, "team.model"): "TeamResponse schema serializes the configured model",
    ("egpt/os/routers/teams/schema.py", 151, "team.model"): "TeamResponse schema serializes the configured model",
    ("egpt/os/routers/teams/schema.py", 288, "team.model"): "TeamResponse schema serializes the configured model",
    ("egpt/os/routers/teams/schema.py", 289, "team.model"): "TeamResponse schema serializes the configured model",
    ("egpt/os/routers/teams/schema.py", 290, "team.model"): "TeamResponse schema serializes the configured model",
    ("egpt/os/routers/teams/schema.py", 291, "team.model"): "TeamResponse schema serializes the configured model",
    ("egpt/os/routers/teams/schema.py", 292, "team.model"): "TeamResponse schema serializes the configured model",
    ("egpt/os/routers/teams/schema.py", 293, "team.model"): "TeamResponse schema serializes the configured model",
}


def _egpt_root() -> Path:
    return package_root() / "egpt"


def _scan_roots() -> Tuple[Path, ...]:
    root = _egpt_root()
    return (root / "agent", root / "team", root / "eval", root / "os")


def test_no_unaccounted_model_reads_in_run_path():
    total_checked = 0
    total_found: Set[Tuple[str, int, str]] = set()
    for root in _scan_roots():
        checked, found = _scan(root)
        total_checked += checked
        total_found |= found

    # Non-vacuity floors: a scan root that goes stale (renamed/emptied) or a detector that
    # silently stops matching would otherwise report zero offenders and PASS.
    assert total_checked > 30, f"only {total_checked} files scanned; the scan roots are probably stale"
    assert len(total_found) >= 30, f"only {len(total_found)} model reads detected; the detector is probably broken"

    unexpected = sorted(total_found - set(ALLOWED_MODEL_READS))
    stale = sorted(set(ALLOWED_MODEL_READS) - total_found)

    assert not unexpected, (
        "direct agent.model/team.model read(s) in the run path with no recorded decision:\n"
        + "\n".join(f"  {path}:{lineno}: {marker}" for path, lineno, marker in unexpected)
        + "\nRoute them through egpt.run.base.run_model(component, run_context), or add an "
        "ALLOWED_MODEL_READS entry with a reason (aicoe-tracker#183)."
    )
    assert not stale, (
        "ALLOWED_MODEL_READS entries with no matching read -- the read moved, was fixed, or the "
        "line number drifted. Update or delete these:\n"
        + "\n".join(f"  {path}:{lineno}: {marker}" for path, lineno, marker in stale)
    )


# --------------------------------------------------------------------------- #
# Mutation verification: the detector itself, on synthetic sources.
# --------------------------------------------------------------------------- #


def _detect(tmp_path: Path, src: str) -> Set[Tuple[int, str]]:
    path = tmp_path / "mod.py"
    path.write_text(src)
    return find_model_reads(path)


def test_detector_catches_a_bare_agent_model_read(tmp_path: Path):
    assert _detect(tmp_path, "x = agent.model.id\n") == {(1, "agent.model")}


def test_detector_catches_a_bare_team_model_read(tmp_path: Path):
    assert _detect(tmp_path, "x = team.model.provider\n") == {(1, "team.model")}


def test_detector_catches_a_presence_check(tmp_path: Path):
    """Presence checks are still reads -- the guard does not try to tell them apart from a
    value-serving read; the allowlist's reason column is where that distinction is recorded."""
    assert _detect(tmp_path, "if agent.model is not None:\n    pass\n") == {(1, "agent.model")}


def test_detector_catches_self_agent_and_self_team(tmp_path: Path):
    src = "class E:\n    def f(self):\n        x = self.agent.model.id\n        y = self.team.model.id\n"
    assert _detect(tmp_path, src) == {(3, "self.agent.model"), (4, "self.team.model")}


def test_detector_ignores_assignment_targets(tmp_path: Path):
    """`agent.model = x` writes the attribute; it is not a bypass of run_model()."""
    assert _detect(tmp_path, "agent.model = resolved\n") == set()


def test_detector_still_catches_the_rhs_of_an_assignment(tmp_path: Path):
    """`x.model = agent.model` -- the LHS is a write (ignored), the RHS is a genuine read."""
    assert _detect(tmp_path, "x.model = agent.model\n") == {(1, "agent.model")}


def test_detector_ignores_unrelated_bases(tmp_path: Path):
    """Some other object's `.model` attribute is not this guard's concern."""
    assert _detect(tmp_path, "x = provider.model\ny = self.model\n") == set()


def test_detector_ignores_unrelated_attributes_on_agent_or_team(tmp_path: Path):
    assert _detect(tmp_path, "x = agent.name\ny = team.id\n") == set()


def test_detector_does_not_fire_on_a_naked_self_model(tmp_path: Path):
    """`self.model` (no intervening `.agent`/`.team`) is a different shape -- e.g.
    `AgentAsJudgeEval.model`, the judge's own configured field, not an Agent/Team read."""
    assert _detect(tmp_path, "class E:\n    def f(self):\n        x = self.model.id\n") == set()
