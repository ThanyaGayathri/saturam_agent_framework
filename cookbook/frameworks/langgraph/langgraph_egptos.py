"""
Run a LangGraph agent through EgptOS endpoints.

This shows how to register a LangGraph agent alongside native EGPT agents
and serve them all through the same EgptOS runtime.

Requirements:
    pip install langgraph langchain-openai

Usage:
    .venvs/demo/bin/python cookbook/frameworks/langgraph_egptos.py

Then call the API:
    # Streaming
    curl -X POST http://localhost:7777/agents/langgraph-chatbot/runs \
        -F "message=What is quantum computing?" \
        -F "stream=true" \
        --no-buffer

    # Non-streaming
    curl -X POST http://localhost:7777/agents/langgraph-chatbot/runs \
        -F "message=What is quantum computing?" \
        -F "stream=false"

    # List agents
    curl http://localhost:7777/agents
"""

from egpt.agents.langgraph import LangGraphAgent
from egpt.os import EgptOS
from langchain_openai import ChatOpenAI
from langgraph.graph import MessagesState, StateGraph


# ----- Build a LangGraph agent -----
def chatbot(state: MessagesState):
    return {"messages": [ChatOpenAI(model="gpt-5.4").invoke(state["messages"])]}


graph = StateGraph(MessagesState)
graph.add_node("chatbot", chatbot)
graph.set_entry_point("chatbot")
compiled = graph.compile()


# ----- Wrap for EgptOS -----
langgraph_agent = LangGraphAgent(
    name="LangGraph Chatbot",
    description="A simple chatbot built with LangGraph, served through EgptOS",
    graph=compiled,
)

# ----- Serve through EgptOS -----
agent_os = EgptOS(agents=[langgraph_agent])
app = agent_os.get_app()

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent_os.serve(app="langgraph_egptos:app", reload=True)
