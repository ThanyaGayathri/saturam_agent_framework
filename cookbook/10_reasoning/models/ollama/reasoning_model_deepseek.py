"""
Reasoning Model Deepseek
========================

Demonstrates this reasoning cookbook example.
"""

from egpt.agent import Agent
from egpt.models.ollama.chat import Ollama


# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------
def run_example() -> None:
    agent = Agent(
        model=Ollama(id="llama3.2:latest"),
        reasoning_model=Ollama(id="deepseek-r1:14b", options={"num_predict": 4096}),
    )
    agent.print_response(
        "Solve the trolley problem. Evaluate multiple ethical frameworks. "
        "Include an ASCII diagram of your solution.",
        stream=True,
    )


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_example()
