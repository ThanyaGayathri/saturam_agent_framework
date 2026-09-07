"""Memory configuration for the retail deployment: the provider config and the consolidation scopes.

Phase zero builds a **vector-only** provider (pgvector on the control-plane Postgres, no Neo4j)
under **Mode B**, because the embedder is an Azure OpenAI deployment and Mode A refuses every
online embedder by design. The graph tier and the per-user scopes arrive with K1 and K5.
"""
