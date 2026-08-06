"""
Asynchronous Reliability Evaluation
==================================

Demonstrates running reliability checks with asynchronous evaluation.
"""

import asyncio
from typing import Optional

from egpt.agent import Agent
from egpt.eval.reliability import ReliabilityEval, ReliabilityResult
from egpt.integrations.tools.calculator import CalculatorTools
from egpt.models.openai import OpenAIChat
from egpt.run.agent import RunOutput


# ---------------------------------------------------------------------------
# Create Evaluation Function
# ---------------------------------------------------------------------------
def factorial():
    agent = Agent(
        model=OpenAIChat(id="gpt-5.2"),
        tools=[CalculatorTools()],
    )
    response: RunOutput = agent.run("What is 10!?")
    evaluation = ReliabilityEval(
        agent_response=response,
        expected_tool_calls=["factorial"],
    )

    # Run the evaluation calling the arun method.
    result: Optional[ReliabilityResult] = asyncio.run(
        evaluation.arun(print_results=True)
    )
    if result:
        result.assert_passed()


# ---------------------------------------------------------------------------
# Run Evaluation
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    factorial()
