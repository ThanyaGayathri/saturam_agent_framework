"""
Single Tool Call Reliability Evaluation
=======================================

Demonstrates reliability checks for one expected tool call,
including argument validation.
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
def factorial():
    agent = Agent(
        model=OpenAIChat(id="gpt-5.2"),
        tools=[CalculatorTools()],
    )
    response: RunOutput = agent.run("What is 10! (ten factorial)?")
    evaluation = ReliabilityEval(
        name="Tool Call Reliability",
        agent_response=response,
        expected_tool_calls=["factorial"],
    )
    result: Optional[ReliabilityResult] = evaluation.run(print_results=True)
    if result:
        result.assert_passed()


def multiply_with_argument_check():
    """Verify that the tool was called with the correct arguments."""
    agent = Agent(
        model=OpenAIChat(id="gpt-5.2"),
        tools=[CalculatorTools()],
    )
    response: RunOutput = agent.run("What is 10 * 5?")
    evaluation = ReliabilityEval(
        name="Tool Call Argument Validation",
        agent_response=response,
        expected_tool_calls=["multiply"],
        expected_tool_call_arguments={
            "multiply": {"a": 10, "b": 5},
        },
    )
    result: Optional[ReliabilityResult] = evaluation.run(print_results=True)
    if result:
        result.assert_passed()


# ---------------------------------------------------------------------------
# Run Evaluation
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    factorial()
    multiply_with_argument_check()
