"""
GitHub Authentication Setup Guide

1. Getting Personal Access Token (PAT):
   a. Navigate to GitHub Settings:
      - Log into GitHub
      - Click profile picture (top-right)
      - Select "Settings"
      - Go to "Developer settings" → "Personal access tokens" → "Tokens (classic)"

   b. Generate New Token:
      - Click "Generate new token (classic)"
      - Add descriptive note
      - Set expiration date
      - Select scopes (minimum 'repo' access)
      - Click "Generate token"
      - IMPORTANT: Save token immediately - only shown once!

2. Setting Environment Variables:

   # For Public GitHub
   export GITHUB_ACCESS_TOKEN="your_token_here"
   export GITHUB_BASE_URL="https://api.github.com"

   # For Enterprise GitHub
   export GITHUB_BASE_URL="https://YOUR-ENTERPRISE-HOSTNAME/api/v3"
"""

from egpt.agent import Agent
from egpt.integrations.tools.github import GithubTools

# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------


# Example 1: Include specific GitHub functions
agent = Agent(
    instructions=[
        "Use your tools to answer questions about the repo: modelcontextprotocol/modelcontextprotocol",
        "Do not create any issues or pull requests unless explicitly asked to do so",
    ],
    tools=[
        GithubTools(
            include_tools=[
                "search_repositories",
                "get_repository",
                "list_repositories",
                "get_pull_requests",
                "list_issues",
            ]
        )
    ],
)

# Example 2: Exclude dangerous functions
agent_safe = Agent(
    instructions=[
        "Use your tools to answer questions about the repo: modelcontextprotocol/modelcontextprotocol",
        "You can only read repository data, not modify anything",
    ],
    tools=[
        GithubTools(
            exclude_tools=[
                "delete_repository",
                "create_repository",
                "create_issue",
                "create_pull_request",
                "delete_file",
            ]
        )
    ],
)

# Example 3: Include all functions (default behavior)
agent_full = Agent(
    instructions=[
        "Use your tools to answer questions about the repo: modelcontextprotocol/modelcontextprotocol",
        "You have full access to GitHub repository management",
    ],
    tools=[GithubTools()],
)

# Basic repository listing

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent.print_response("List open pull requests", markdown=True)

    # Example: Get comprehensive repository stats
    # agent.print_response(
    #     "Get comprehensive stats for the modelcontextprotocol/modelcontextprotocol repository", markdown=True
    # )

    # Example: Get detailed pull request information
    # agent.print_response(
    #     "Get comprehensive details for pull request #100 in the modelcontextprotocol/modelcontextprotocol repository",
    #     markdown=True,
    # )

    # Example: Working with issues
    # agent.print_response(
    #     "List all open issues in the modelcontextprotocol/modelcontextprotocol repository", markdown=True
    # )

    # Example: Get specific issue details
    # agent.print_response(
    #     "Get details for issue #50 in the modelcontextprotocol/modelcontextprotocol repository", markdown=True
    # )

    # Example: File operations - checking file content
    # agent.print_response(
    #     "Show me the content of the README.md file in the modelcontextprotocol/modelcontextprotocol repository",
    #     markdown=True,
    # )

    # Example: Directory listing
    # agent.print_response(
    #     "List all files in the docs directory of the modelcontextprotocol/modelcontextprotocol repository",
    #     markdown=True,
    # )

    # Example: List branch content
    # agent.print_response(
    #     "Show me the files in the main branch of the modelcontextprotocol/modelcontextprotocol repository",
    #     markdown=True,
    # )

    # Example: Branch operations
    # agent.print_response("List all branches in the modelcontextprotocol/modelcontextprotocol repository", markdown=True)

    # Example: Search code in repository
    # agent.print_response(
    #     "Search for 'Agent' class definitions in the modelcontextprotocol/modelcontextprotocol repository",
    #     markdown=True,
    # )

    # Example: Search issues and pull requests
    # agent.print_response(
    #     "Find all issues and PRs mentioning 'bug' in the modelcontextprotocol/modelcontextprotocol repository",
    #     markdown=True,
    # )

    # Example: Creating a pull request (commented out by default)
    # agent.print_response("Create a pull request from 'feature-branch' to 'main' in modelcontextprotocol/modelcontextprotocol titled 'New Feature' with description 'Implements the new feature'", markdown=True)

    # Example: Creating a branch (commented out by default)
    # agent.print_response("Create a new branch called 'feature-branch' from the main branch in the modelcontextprotocol/modelcontextprotocol repository", markdown=True)

    # Example: Setting default branch (commented out by default)
    # agent.print_response("Set the default branch to 'develop' in the modelcontextprotocol/modelcontextprotocol repository", markdown=True)

    # Example: File creation (commented out by default)
    # agent.print_response("Create a file called 'test.md' with content 'This is a test' in the modelcontextprotocol/modelcontextprotocol repository", markdown=True)

    # Example: Update file (commented out by default)
    # agent.print_response("Update the README.md file in the modelcontextprotocol/modelcontextprotocol repository to add a new section about installation", markdown=True)

    # Example: Delete file (commented out by default)
    # agent.print_response("Delete the file test.md from the modelcontextprotocol/modelcontextprotocol repository", markdown=True)

    # Example: Requesting a review for a pull request (commented out by default)
    # agent.print_response("Request a review from user 'username' for pull request #100 in the modelcontextprotocol/modelcontextprotocol repository", markdown=True)

    # # Advanced examples (commented out by default)

    # # Example usage: Search for python projects on github that have more than 1000 stars
    # agent.print_response("Search for python projects on github that have more than 1000 stars", markdown=True, stream=True)

    # # Example usage: Search for python projects on github that have more than 1000 stars, but return the 2nd page of results
    # agent.print_response("Search for python projects on github that have more than 1000 stars, but return the 2nd page of results", markdown=True, stream=True)

    # # Example usage: Get pull request details
    # agent.print_response("Get details of #1239", markdown=True)

    # # Example usage: Get pull request changes
    # agent.print_response("Show changes for #1239", markdown=True)

    # # Example usage: Get pull request count
    # agent.print_response("How many pull requests are there in the modelcontextprotocol/modelcontextprotocol repository?", markdown=True)

    # # Example usage: Get pull request count by author
    # agent.print_response("How many pull requests has user 'username' created in the modelcontextprotocol/modelcontextprotocol repository?", markdown=True)

    # # Example usage: List open issues
    # agent.print_response("What is the latest opened issue?", markdown=True)

    # # Example usage: Create an issue
    # agent.print_response("Explain the comments for the most recent issue", markdown=True)

    # # Example usage: Create a Repo
    # agent.print_response("Create a repo called egpt-test and add description hello", markdown=True)

    # # Example usage: Get repository stars
    # agent.print_response("How many stars does the modelcontextprotocol/modelcontextprotocol repository have?", markdown=True)

    # # Example usage: Get pull requests by query parameters
    # agent.print_response("Get open pull requests from the modelcontextprotocol/modelcontextprotocol repository on the main branch sorted by creation date", markdown=True)

    # # Example usage: Get pull request comments
    # agent.print_response("Show me all review comments on pull request #100 in the modelcontextprotocol/modelcontextprotocol repository", markdown=True)

    # # Example usage: Create a pull request comment
    # agent.print_response("Add a comment 'Nice work!' to line 10 of file.py in the latest commit of PR #100 in the modelcontextprotocol/modelcontextprotocol repository", markdown=True)

    # # Example usage: Edit a pull request comment
    # agent.print_response("Update comment #1057297855 in the modelcontextprotocol/modelcontextprotocol repository to say 'Updated: This looks good now'", markdown=True)

    # # Example usage: Get repository stars
    # agent.print_response("How many stars does the modelcontextprotocol/modelcontextprotocol repository have?", markdown=True)

    # # Example usage: Get pull requests by query parameters
    # agent.print_response("Get open pull requests from the modelcontextprotocol/modelcontextprotocol repository on the main branch sorted by creation date", markdown=True)

    # # Example usage: Get pull request comments
    # agent.print_response("Show me all review comments on pull request #100 in the modelcontextprotocol/modelcontextprotocol repository", markdown=True)

    # # Example usage: Create a pull request comment
    # agent.print_response("Add a comment 'Nice work!' to line 10 of file.py in the latest commit of PR #100 in the modelcontextprotocol/modelcontextprotocol repository", markdown=True)

    # # Example usage: Edit a pull request comment
    # agent.print_response("Update comment #1057297855 in the modelcontextprotocol/modelcontextprotocol repository to say 'Updated: This looks good now'", markdown=True)
