"""
Streaming Deep Research Agent
==============================

A multi-tool research agent that exercises many different tool types
to stress-test the Slack streaming plan block UI.

Uses 7 toolkits across web search, finance, news, academic papers,
and calculations — a single query can trigger 8-12+ tool calls,
each rendering as a card in the plan block.

Slack scopes: app_mentions:read, assistant:write, chat:write, im:history
"""

from egpt.agent import Agent
from egpt.integrations.db.sqlite.sqlite import SqliteDb
from egpt.integrations.tools.arxiv import ArxivTools
from egpt.integrations.tools.calculator import CalculatorTools
from egpt.integrations.tools.duckduckgo import DuckDuckGoTools
from egpt.integrations.tools.hackernews import HackerNewsTools
from egpt.integrations.tools.newspaper4k import Newspaper4kTools
from egpt.integrations.tools.wikipedia import WikipediaTools
from egpt.integrations.tools.yfinance import YFinanceTools
from egpt.models.openai import OpenAIChat
from egpt.os.app import EgptOS
from egpt.os.interfaces.slack import Slack

agent_db = SqliteDb(
    session_table="deep_research_sessions", db_file="tmp/deep_research.db"
)

deep_research_agent = Agent(
    name="Deep Research Agent",
    model=OpenAIChat(id="gpt-4.1"),
    tools=[
        DuckDuckGoTools(),
        HackerNewsTools(),
        YFinanceTools(
            enable_stock_price=True,
            enable_company_info=True,
            enable_analyst_recommendations=True,
            enable_company_news=True,
        ),
        WikipediaTools(),
        ArxivTools(),
        CalculatorTools(),
        Newspaper4kTools(),
    ],
    instructions=[
        "You are a deep research assistant that gathers information from MANY sources.",
        "For every query, use AT LEAST 4 different tools to provide comprehensive answers.",
        "Always search the web AND check HackerNews AND Wikipedia for context.",
        "For finance questions, pull stock data, analyst recommendations, AND company news.",
        "For technical topics, also search Arxiv for relevant research papers.",
        "Use the calculator for any numerical analysis or comparisons.",
        "Use newspaper4k to read full articles when you find interesting URLs.",
        "Synthesize all findings into a well-structured summary with sections.",
    ],
    db=agent_db,
    add_history_to_context=True,
    num_history_runs=3,
    add_datetime_to_context=True,
    markdown=True,
)

agent_os = EgptOS(
    agents=[deep_research_agent],
    interfaces=[
        Slack(
            agent=deep_research_agent,
            streaming=True,
            reply_to_mentions_only=True,
            loading_messages=[
                "Researching across multiple sources...",
                "Gathering data from 7 different tools...",
                "Cross-referencing findings...",
                "Analyzing and synthesizing results...",
            ],
            suggested_prompts=[
                {
                    "title": "Deep Stock Analysis",
                    "message": "Do a deep analysis of NVDA: get the stock price, company info, analyst recommendations, latest news, search the web for recent developments, check HackerNews discussions, and look up Nvidia on Wikipedia for company background",
                },
                {
                    "title": "AI Research Deep Dive",
                    "message": "Research the latest developments in large language models: search the web, check HackerNews, look up recent Arxiv papers on LLMs, and read the Wikipedia article on large language models for background context",
                },
                {
                    "title": "Tech Company Comparison",
                    "message": "Compare AAPL and MSFT: get both stock prices, analyst recommendations, company info, search for recent news about both, and calculate the price-to-earnings ratio difference",
                },
                {
                    "title": "Trending Tech News",
                    "message": "What are the biggest tech stories today? Check HackerNews top stories, search the web for breaking tech news, and read the full text of the top 2 articles you find",
                },
            ],
        )
    ],
)
app = agent_os.get_app()


if __name__ == "__main__":
    agent_os.serve(app="streaming_deep_research:app", reload=True)
