# Background Tasks Cookbook

Examples for `background_tasks` in EgptOS.

## Files
- `background_evals_example.py` — Example: Per-Hook Background Control with AgentAsJudgeEval in EgptOS.
- `background_hooks_decorator.py` — Example: Using Background Post-Hooks in EgptOS.
- `background_hooks_example.py` — Example: Using Background Post-Hooks in EgptOS.
- `background_hooks_team.py` — Example: Background Hooks with Teams in EgptOS.
- `background_hooks_workflow.py` — Example: Background Hooks with Workflows in EgptOS.
- `background_output_evaluation.py` — Example: Background Output Evaluation with Agent-as-Judge.
- `evals_demo.py` — Simple example creating a session and using the EgptOS with a SessionApp to expose it.

## Prerequisites
- Load environment variables with `direnv allow` (requires `.envrc`).
- Run examples with `.venvs/demo/bin/python <path-to-file>.py`.
- Some examples require local services (for example Postgres, Redis, Slack, or MCP servers).
