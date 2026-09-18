"""Domain knowledge ingestion -- layer 1 of ``docs/plan/domain-knowledge-layers.md``.

``prose`` ingests curated retail markdown, split on headings and tagged by origin.
``embedding`` resolves the one embedder every knowledge layer shares; ``vector`` builds the
vector store that prose lands in, chosen by ``EGPT_RETAIL_KNOWLEDGE_BACKEND`` (AZ-C).
"""
