"""
Skills With Agentos
===================

Demonstrates skills with agentos.
"""

from pathlib import Path

from egpt.agent import Agent
from egpt.models.openai import OpenAIChat
from egpt.os import AgentOS
from egpt.skills import LocalSkills, Skills

# ---------------------------------------------------------------------------
# Create Example
# ---------------------------------------------------------------------------

# Get the skills directory relative to this file
skills_dir = Path(__file__).parent / "sample_skills"

# Create an agent with skills
skills_agent = Agent(
    name="Skills Agent",
    model=OpenAIChat(id="gpt-4o"),
    skills=Skills(loaders=[LocalSkills(str(skills_dir))]),
    instructions=["You are a helpful assistant with access to specialized skills."],
    markdown=True,
)

# Setup AgentOS
agent_os = AgentOS(
    description="Agent with Skills Demo - Execute skill scripts via AgentOS",
    agents=[skills_agent],
)
app = agent_os.get_app()


# ---------------------------------------------------------------------------
# Run Example
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    agent_os.serve(app="skills_with_agentos:app", reload=True)
