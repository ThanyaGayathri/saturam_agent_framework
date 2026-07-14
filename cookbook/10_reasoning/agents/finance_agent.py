"""
Reasoning Finance Agent
=======================

Demonstrates built-in and DeepSeek-backed reasoning for financial reporting.
"""

from egpt.agent import Agent
from egpt.models.deepseek import DeepSeek
from egpt.models.openai import OpenAIChat
from egpt.tools.yfinance import YFinanceTools

# ---------------------------------------------------------------------------
# Create Agents
# ---------------------------------------------------------------------------
cot_agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[YFinanceTools()],
    instructions="Use tables to display data",
    use_json_mode=True,
    reasoning=True,
    markdown=True,
)

deepseek_agent = Agent(
    model=OpenAIChat(id="gpt-4o"),
    tools=[YFinanceTools()],
    instructions=["Use tables where possible"],
    reasoning_model=DeepSeek(id="deepseek-reasoner"),
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agents
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    prompt = "Write a report comparing NVDA to TSLA"

    print("=== Built-in Chain Of Thought ===")
    cot_agent.print_response(prompt, stream=True, show_full_reasoning=True)

    print("\n=== DeepSeek Reasoning Model ===")
    deepseek_agent.print_response(prompt, stream=True, show_full_reasoning=True)
