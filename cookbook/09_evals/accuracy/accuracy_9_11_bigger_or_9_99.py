"""
Comparison Accuracy Evaluation
==============================

Demonstrates accuracy evaluation for numeric comparison tasks.
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
    name="Comparison Evaluation",
    model=OpenAIChat(id="o4-mini"),
    agent=Agent(
        model=OpenAIChat(id="gpt-4o"),
        tools=[CalculatorTools()],
        instructions="You must use the calculator tools for comparisons.",
    ),
    input="9.11 and 9.9 -- which is bigger?",
    expected_output="9.9",
    additional_guidelines="Its ok for the output to include additional text or information relevant to the comparison.",
)

# ---------------------------------------------------------------------------
# Run Evaluation
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    result: Optional[AccuracyResult] = evaluation.run(print_results=True)
    assert result is not None and result.avg_score >= 8
