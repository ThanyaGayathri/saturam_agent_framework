"""
Lmstudio Image Agent
====================

Cookbook example for `lmstudio/image_agent.py`.
"""

import httpx
from egpt.agent import Agent
from egpt.media import Image
from egpt.models.lmstudio import LMStudio

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=LMStudio(id="llama3.2-vision"),
    markdown=True,
)

response = httpx.get(
    "https://upload.wikimedia.org/wikipedia/commons/0/0c/GoldenGateBridge-001.jpg"
)

agent.print_response(
    "Tell me about this image",
    images=[Image(content=response.content)],
    stream=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
