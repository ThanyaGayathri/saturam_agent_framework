"""
Dashscope Image Agent Bytes
===========================

Cookbook example for `dashscope/image_agent_bytes.py`.
"""

from pathlib import Path

from egpt.agent import Agent
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.media import Image
from egpt.models.dashscope import DashScope
from egpt.utils.media import download_image

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=DashScope(id="qwen-vl-plus"),
    tools=[WebSearchTools()],
    markdown=True,
)

image_path = Path(__file__).parent.joinpath("sample.jpg")

download_image(
    url="https://upload.wikimedia.org/wikipedia/commons/a/a7/Camponotus_flavomarginatus_ant.jpg",
    output_path=str(image_path),
)

# Read the image file content as bytes
image_bytes = image_path.read_bytes()

agent.print_response(
    "Analyze this image of an ant. Describe its features, species characteristics, and search for more information about this type of ant.",
    images=[
        Image(content=image_bytes),
    ],
    stream=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
