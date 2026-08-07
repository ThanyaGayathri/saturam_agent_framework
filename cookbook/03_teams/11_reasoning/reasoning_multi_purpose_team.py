"""
Reasoning Multi Purpose Team
============================

Demonstrates multi-purpose team reasoning with both sync and async patterns.
"""

import asyncio
from pathlib import Path
from textwrap import dedent

from egpt.agent import Agent
from egpt.integrations.tools.calculator import CalculatorTools
from egpt.integrations.tools.e2b import E2BTools
from egpt.integrations.tools.file import FileTools
from egpt.integrations.tools.github import GithubTools
from egpt.integrations.tools.pubmed import PubmedTools
from egpt.integrations.tools.python import PythonTools
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.integrations.knowledge.embedder.openai import OpenAIEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.models.openai import OpenAIResponses
from egpt.team import Team
from egpt.tools.knowledge import KnowledgeTools
from egpt.tools.reasoning import ReasoningTools
from egpt.vectordb.lancedb.lance_db import LanceDb
from egpt.vectordb.search import SearchType

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
cwd = Path(__file__).parent.resolve()

mcp_docs_knowledge = Knowledge(
    vector_db=LanceDb(
        uri="tmp/lancedb",
        table_name="mcp_docs_knowledge",
        search_type=SearchType.hybrid,
        embedder=OpenAIEmbedder(id="text-embedding-3-small"),
    ),
)

# ---------------------------------------------------------------------------
# Create Members
# ---------------------------------------------------------------------------
web_agent = Agent(
    name="Web Agent",
    role="Search the web for information",
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[WebSearchTools()],
    instructions=["Always include sources"],
)

finance_agent = Agent(
    name="Finance Agent",
    role="Get financial data",
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[YFinanceTools()],
    instructions=["Use tables to display data"],
)

writer_agent = Agent(
    name="Write Agent",
    role="Write content",
    model=OpenAIResponses(id="gpt-5.2"),
    description="You are an AI agent that can write content.",
    instructions=[
        "You are a versatile writer who can create content on any topic.",
        "When given a topic, write engaging and informative content in the requested format and style.",
        "If you receive mathematical expressions or calculations from the calculator agent, convert them into clear written text.",
        "Ensure your writing is clear, accurate and tailored to the specific request.",
        "Maintain a natural, engaging tone while being factually precise.",
        "Write something that would be good enough to be published in a newspaper like the New York Times.",
    ],
)

medical_agent = Agent(
    name="Medical Agent",
    role="Medical researcher",
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[PubmedTools()],
    instructions=[
        "You are a medical agent that can answer questions about medical topics.",
        "Always search for recent medical literature and evidence.",
    ],
)

calculator_agent = Agent(
    name="Calculator Agent",
    model=OpenAIResponses(id="gpt-5.2"),
    role="Calculate",
    tools=[CalculatorTools()],
)

mcp_docs_agent = Agent(
    name="MCP Docs Agent",
    role="You help answer questions about the Model Context Protocol.",
    model=OpenAIResponses(id="gpt-5-mini"),
    instructions="Search your knowledge before answering the question. Cite the Model Context Protocol documentation you used.",
    tools=[
        KnowledgeTools(
            knowledge=mcp_docs_knowledge,
            add_instructions=True,
            add_few_shot=True,
        ),
    ],
    add_history_to_context=True,
    add_datetime_to_context=True,
)

github_agent = Agent(
    name="Github Agent",
    role="Do analysis on Github repositories",
    model=OpenAIResponses(id="gpt-5-mini"),
    instructions=[
        "Use your tools to answer questions about the repo: modelcontextprotocol/modelcontextprotocol",
        "Do not create any issues or pull requests unless explicitly asked to do so",
    ],
    tools=[
        GithubTools(
            include_tools=[
                "list_issues",
                "list_issue_comments",
                "get_pull_request",
                "get_issue",
                "get_pull_request_comments",
            ]
        )
    ],
)

local_python_agent = Agent(
    name="Local Python Agent",
    role="Run Python code locally",
    model=OpenAIResponses(id="gpt-5-mini"),
    instructions=["Use your tools to run Python code locally"],
    tools=[
        FileTools(base_dir=cwd),
        PythonTools(
            base_dir=Path(cwd),
            include_tools=[
                "list_files",
                "run_python_file_return_variable",
                "save_to_file_and_run",
                "uv_pip_install_package",
            ],
        ),
    ],
)

code_agent = Agent(
    name="Code Agent",
    model=OpenAIResponses(id="gpt-5.2"),
    role="Execute and test code",
    tools=[E2BTools()],
    instructions=[
        "Execute code safely in the sandbox environment.",
        "Test code thoroughly before providing results.",
        "Provide clear explanations of code execution.",
    ],
)

# ---------------------------------------------------------------------------
# Create Team
# ---------------------------------------------------------------------------
sync_agent_team = Team(
    name="Multi-Purpose Team",
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[ReasoningTools(add_instructions=True, add_few_shot=True)],
    members=[
        web_agent,
        finance_agent,
        writer_agent,
        calculator_agent,
        mcp_docs_agent,
        github_agent,
        local_python_agent,
    ],
    instructions=[
        "You are a team of agents that can answer a variety of questions.",
        "You can use your member agents to answer the questions.",
        "You can also answer directly, you don't HAVE to forward the question to a member agent.",
        "Reason about more complex questions before delegating to a member agent.",
        "If the user is only being conversational, don't use any tools, just answer directly.",
    ],
    markdown=True,
    show_members_responses=True,
    share_member_interactions=True,
)

async_agent_team = Team(
    name="Multi-Purpose Agent Team",
    model=OpenAIResponses(id="gpt-5.2"),
    tools=[ReasoningTools()],
    members=[
        web_agent,
        finance_agent,
        medical_agent,
        calculator_agent,
        mcp_docs_agent,
        code_agent,
    ],
    instructions=[
        "You are a team of agents that can answer a variety of questions.",
        "Use reasoning tools to analyze questions before delegating.",
        "You can answer directly or forward to appropriate specialist agents.",
        "For complex questions, reason about the best approach first.",
        "If the user is just being conversational, respond directly without tools.",
    ],
    markdown=True,
    show_members_responses=True,
    share_member_interactions=True,
)


async def run_async_reasoning_demo() -> None:
    await mcp_docs_knowledge.ainsert(
        url="https://modelcontextprotocol.io/llms-full.txt"
    )
    await async_agent_team.aprint_response(input="Hi! What are you capable of doing?")


# ---------------------------------------------------------------------------
# Run Team
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(
        mcp_docs_knowledge.ainsert(url="https://modelcontextprotocol.io/llms-full.txt")
    )

    txt_path = Path(__file__).parent.resolve() / "medical_history.txt"
    loaded_txt = open(txt_path, "r", encoding="utf-8").read()
    sync_agent_team.print_response(
        input=dedent(
            f"""I have a patient with the following medical information:\n {loaded_txt}
                         What is the most likely diagnosis?
                        """
        ),
    )

    asyncio.run(run_async_reasoning_demo())
