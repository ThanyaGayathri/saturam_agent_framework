"""
AWS Bedrock Embedder
====================

Demonstrates Cohere v3 embeddings through AWS Bedrock and knowledge insertion.

Requirements:
- AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
- AWS region configured (AWS_REGION)
- boto3 installed: pip install boto3
"""

from egpt.knowledge.chunking.fixed import FixedSizeChunking
from egpt.integrations.knowledge.embedder.aws_bedrock import AwsBedrockEmbedder
from egpt.knowledge.knowledge import Knowledge
from egpt.integrations.knowledge.reader.pdf_reader import PDFReader
from egpt.vectordb.pgvector import PgVector

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
embedder = AwsBedrockEmbedder()

# ---------------------------------------------------------------------------
# Create Knowledge Base
# ---------------------------------------------------------------------------
knowledge = Knowledge(
    vector_db=PgVector(
        table_name="recipes",
        db_url="postgresql+psycopg://ai:ai@localhost:5532/ai",
        embedder=AwsBedrockEmbedder(input_type="search_document"),
    ),
)


# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
def main() -> None:
    embeddings = embedder.get_embedding("The quick brown fox jumps over the lazy dog.")
    print(f"Embeddings (first 5): {embeddings[:5]}")
    print(f"Dimensions: {len(embeddings)}")

    knowledge.insert(
        url="https://agno-public.s3.amazonaws.com/recipes/ThaiRecipes.pdf",
        reader=PDFReader(chunking_strategy=FixedSizeChunking(chunk_size=1500)),
    )


if __name__ == "__main__":
    main()
