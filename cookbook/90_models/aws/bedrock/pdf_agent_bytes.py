"""
Aws Pdf Agent Bytes
===================

Cookbook example for `aws/bedrock/pdf_agent_bytes.py`.
"""

from pathlib import Path

from egpt.agent import Agent
from egpt.media import File
from egpt.models.aws import AwsBedrock
from egpt.utils.media import download_file

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

pdf_path = Path(__file__).parent.joinpath("ThaiRecipes.pdf")

download_file(
    "https://agno-public.s3.amazonaws.com/recipes/ThaiRecipes.pdf", str(pdf_path)
)

agent = Agent(
    model=AwsBedrock(id="amazon.nova-pro-v1:0"),
    markdown=True,
)

pdf_bytes = pdf_path.read_bytes()

agent.print_response(
    "Give the recipe of Gaeng Kiew Wan Goong",
    files=[File(content=pdf_bytes, format="pdf", name="Thai Recipes")],
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
