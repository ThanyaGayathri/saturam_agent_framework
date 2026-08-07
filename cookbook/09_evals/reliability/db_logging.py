"""
Reliability Evaluation with Database Logging
============================================

Demonstrates storing reliability evaluation results in PostgreSQL.
"""

from typing import Optional

from egpt.agent import Agent
from egpt.eval.reliability import ReliabilityEval, ReliabilityResult
from egpt.integrations.db.postgres.postgres import PostgresDb
from egpt.integrations.tools.calculator import CalculatorTools
from egpt.models.openai import OpenAIChat
from egpt.run.agent import RunOutput

# ---------------------------------------------------------------------------
# Create Database
# ---------------------------------------------------------------------------
db_url = "postgresql+psycopg://ai:ai@localhost:5432/ai"
db = PostgresDb(db_url=db_url, eval_table="eval_runs")

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    model=OpenAIChat(id="gpt-5.2"),
    tools=[CalculatorTools()],
)

# ---------------------------------------------------------------------------
# Create Evaluation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Run Evaluation
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    response: RunOutput = agent.run("What is 10!?")
    evaluation = ReliabilityEval(
        db=db,
        name="Tool Call Reliability",
        agent_response=response,
        expected_tool_calls=["factorial"],
    )
    result: Optional[ReliabilityResult] = evaluation.run(print_results=True)
    if result:
        result.assert_passed()
