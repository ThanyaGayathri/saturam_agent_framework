from egpt.agent.agent import Agent
from egpt.media import Image
from egpt.models.azure import AzureOpenAI


def test_image_input():
    agent = Agent(model=AzureOpenAI(id="gpt-4o-mini"), markdown=True, telemetry=False)

    response = agent.run(
        "Tell me about this image.",
        images=[Image(url="https://upload.wikimedia.org/wikipedia/commons/0/0c/GoldenGateBridge-001.jpg")],
    )

    assert "golden" in response.content.lower()
    assert "bridge" in response.content.lower()
