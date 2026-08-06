"""
Webbrowser Tools
=============================

Demonstrates webbrowser tools.
"""

from egpt.agent import Agent
from egpt.integrations.tools.webbrowser import WebBrowserTools
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.models.google import Gemini

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


# Example 1: Enable specific WebBrowser functions
agent = Agent(
    model=Gemini("gemini-2.0-flash"),
    tools=[WebBrowserTools(enable_open_page=True), WebSearchTools()],
    instructions=[
        "Find related websites and pages using DuckDuckGo",
        "Use web browser to open the site",
    ],
    markdown=True,
)

# Example 2: Enable all WebBrowser functions
agent_all = Agent(
    model=Gemini("gemini-2.0-flash"),
    tools=[WebBrowserTools(all=True), WebSearchTools()],
    instructions=[
        "Find related websites and pages using DuckDuckGo",
        "Use web browser to open the site with full functionality",
    ],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response(
        "Find an article explaining MCP and open it in the web browser."
    )
