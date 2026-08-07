"""Run `uv pip install groq` to install dependencies."""

from egpt.agent import Agent
from egpt.integrations.tools.models.groq import GroqTools
from egpt.models.openai import OpenAIChat

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

url = "https://agno-public.s3.amazonaws.com/demo_data/sample_conversation.wav"

agent = Agent(
    name="Groq Transcription Agent",
    model=OpenAIChat(id="gpt-5.2"),
    tools=[GroqTools(exclude_tools=["generate_speech"])],
)

agent.print_response(f"Please transcribe the audio file located at '{url}' to English")

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
