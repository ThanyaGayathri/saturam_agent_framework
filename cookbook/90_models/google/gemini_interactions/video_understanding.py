"""
Gemini Interactions - Video Understanding
==========================================

Example showing video understanding with the Interactions API.
Supports video from URLs and local files.

Note: For larger videos, consider uploading via the Files API first.
"""

from egpt.agent import Agent
from egpt.media import Video
from egpt.models.google import GeminiInteractions

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------

agent = Agent(
    model=GeminiInteractions(id="gemini-3.5-flash"),
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # --- Video from URL ---
    agent.print_response(
        "Describe what happens in this video.",
        videos=[
            Video(
                url="https://download.samplelib.com/mp4/sample-5s.mp4",
                mime_type="video/mp4",
            )
        ],
    )
