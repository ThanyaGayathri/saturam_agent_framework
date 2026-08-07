"""
Tool-Enabled Accuracy Evaluation
================================

Demonstrates accuracy evaluation for an agent using calculator tools.
"""

from typing import Optional

from egpt.agent import Agent
from egpt.eval.accuracy import AccuracyEval, AccuracyResult
from egpt.integrations.tools.calculator import CalculatorTools
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Create Evaluation
# ---------------------------------------------------------------------------
evaluation = AccuracyEval(
    name="Tools Evaluation",
    model=OpenAIChat(id="o4-mini"),
    agent=Agent(
        model=OpenAIChat(id="gpt-5.2"),
        tools=[CalculatorTools()],
    ),
    input="What is 10!?",
    expected_output="3628800",
)

# ---------------------------------------------------------------------------
# Run Evaluation
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    result: Optional[AccuracyResult] = evaluation.run(print_results=True)
    assert result is not None and result.avg_score >= 8
