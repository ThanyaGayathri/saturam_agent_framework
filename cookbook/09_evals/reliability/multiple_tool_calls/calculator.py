"""
Multiple Tool Call Reliability Evaluation
=========================================

Demonstrates reliability checks for multiple expected tool calls,
including subset matching with allow_additional_tool_calls.
"""

from typing import Optional

from egpt.agent import Agent
from egpt.eval.reliability import ReliabilityEval, ReliabilityResult
from egpt.models.openai import OpenAIChat
from egpt.run.agent import RunOutput
from egpt.tools.calculator import CalculatorTools


# ---------------------------------------------------------------------------
# Create Evaluation Functions
# ---------------------------------------------------------------------------
def multiply_and_exponentiate():
    agent = Agent(
        model=OpenAIChat(id="gpt-5.2"),
        tools=[CalculatorTools()],
    )
    response: RunOutput = agent.run(
        "What is 10*5 then to the power of 2? do it step by step"
    )
    evaluation = ReliabilityEval(
        name="Tool Calls Reliability",
        agent_response=response,
        expected_tool_calls=["multiply", "exponentiate"],
    )
    result: Optional[ReliabilityResult] = evaluation.run(print_results=True)
    if result:
        result.assert_passed()


def subset_matching():
    """Only require 'multiply' -- extra tool calls like 'exponentiate' are allowed."""
    agent = Agent(
        model=OpenAIChat(id="gpt-5.2"),
        tools=[CalculatorTools()],
    )
    response: RunOutput = agent.run(
        "What is 10*5 then to the power of 2? do it step by step"
    )
    evaluation = ReliabilityEval(
        name="Subset Tool Calls",
        agent_response=response,
        expected_tool_calls=["multiply"],
        allow_additional_tool_calls=True,
    )
    result: Optional[ReliabilityResult] = evaluation.run(print_results=True)
    if result:
        result.assert_passed()


# ---------------------------------------------------------------------------
# Run Evaluation
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    multiply_and_exponentiate()
    subset_matching()
