from egpt.agent import Agent
from egpt.integrations.models.vercel import V0
from egpt.integrations.tools.websearch import WebSearchTools
from egpt.media import Image


def test_image_input():
    agent = Agent(
        model=V0(id="v0-1.0-md"),
        tools=[WebSearchTools(cache_results=True)],
        markdown=True,
        telemetry=False,
    )

    response = agent.run(
        "Tell me about this image and give me the latest news about it.",
        images=[Image(url="https://upload.wikimedia.org/wikipedia/commons/0/0c/GoldenGateBridge-001.jpg")],
    )

    assert response.content is not None
    assert "golden" in response.content.lower()
