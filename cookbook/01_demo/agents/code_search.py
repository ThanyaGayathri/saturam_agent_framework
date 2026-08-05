"""
CodeSearch Agent
================

Answers questions about the egpt repository.

Uses ``WorkspaceContextProvider``, which exposes a read-only ``Workspace`` toolkit (list / search / read) behind a sub-agent.

The parent agent sees a single ``query_codebase(question)`` tool.
"""

from pathlib import Path

from db import get_db
from egpt.agent import Agent
from egpt.context.workspace import WorkspaceContextProvider
from settings import default_model, sub_agent_model

REPO_ROOT = Path(__file__).resolve().parents[3]

code_search_provider = WorkspaceContextProvider(
    id="codebase",
    name="EGPT Repo",
    root=REPO_ROOT,
    model=sub_agent_model(),
)


CODE_SEARCH_INSTRUCTIONS = """\
You answer questions about the egpt repository by searching the code with
query_codebase. Ground every answer in what you actually find: cite real
file paths (with line numbers where useful) and quote the code rather than
paraphrasing it. If the repository does not contain the answer — a function
that isn't defined, a file that doesn't exist — say so plainly instead of
guessing. For off-topic questions, say it's outside this codebase and offer
to take a code question instead. Keep answers in tidy markdown.
"""


code_search = Agent(
    id="code-search",
    name="CodeSearch",
    model=default_model(),
    db=get_db(),
    tools=code_search_provider.get_tools(),
    instructions=CODE_SEARCH_INSTRUCTIONS
    + "\n\n"
    + code_search_provider.instructions(),
    add_datetime_to_context=True,
    add_history_to_context=True,
    num_history_runs=5,
    markdown=True,
)
