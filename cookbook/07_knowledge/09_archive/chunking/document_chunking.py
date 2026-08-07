from egpt.agent import Agent
from egpt.knowledge.chunking.document import DocumentChunking
from egpt.knowledge.knowledge import Knowledge
from egpt.integrations.knowledge.reader.pdf_reader import PDFReader
from egpt.vectordb.pgvector import PgVector

db_url = "postgresql+psycopg://ai:ai@localhost:5532/ai"

knowledge = Knowledge(
    vector_db=PgVector(table_name="recipes_document_chunking", db_url=db_url),
)

knowledge.insert(
    url="https://agno-public.s3.amazonaws.com/recipes/ThaiRecipes.pdf",
    reader=PDFReader(
        name="Document Chunking Reader",
        chunking_strategy=DocumentChunking(),
    ),
)

agent = Agent(
    knowledge=knowledge,
    search_knowledge=True,
)

agent.print_response("How to make Thai curry?", markdown=True)
