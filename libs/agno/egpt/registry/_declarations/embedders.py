"""Built-in embedder declarations.

One row per embedder:

    key -> (module, class_name)

The key is the module's own basename, verbatim -- `aws_bedrock`, `sentence_transformer`,
`openai_like`. That follows the db and vectordb tables (`in_memory`, `gcs_json`,
`pineconedb`) rather than the hyphenated slugs of the models table, whose keys are
hyphenated because users type them in `get_model("provider:id")`. Deriving the key from the
module means there is no mapping to keep in sync, and the rule is checkable:
`test_embedder_keys_match_their_module` asserts it for every row, so renaming a module
cannot silently strand its key. The key follows the module, not the class -- hence `google`
for `GeminiEmbedder`, which also agrees with the models table's own `google` key.

`module` names the implementation module, not a re-exporting package. Unlike models and dbs,
`egpt/integrations/knowledge/embedder/__init__.py` is deliberately bare: every embedder here
is a vendor client behind an optional SDK, so re-exporting them would make importing the
package require all of them at once.

The table is complete against the integrations tree -- 18 rows for 18 `Embedder` subclasses
-- and `test_every_embedder_subclass_is_registered` keeps it that way. Completeness is the
point: ADR 0001's extraction prerequisite 6 wants every embedder reachable by name so memory
Mode A <-> B stays a config swap (SM-R3/SM-R4). These rows are that prerequisite's first
half. The second half is memory resolving its embedder by string instead of importing one,
which is memory-side work and stays open.
"""

from egpt.registry.discovery import SOURCE_BUILTIN, register

_ROWS = {
    "aws_bedrock": ("egpt.integrations.knowledge.embedder.aws_bedrock", "AwsBedrockEmbedder"),
    "azure_openai": ("egpt.integrations.knowledge.embedder.azure_openai", "AzureOpenAIEmbedder"),
    "cohere": ("egpt.integrations.knowledge.embedder.cohere", "CohereEmbedder"),
    "fastembed": ("egpt.integrations.knowledge.embedder.fastembed", "FastEmbedEmbedder"),
    "fireworks": ("egpt.integrations.knowledge.embedder.fireworks", "FireworksEmbedder"),
    "google": ("egpt.integrations.knowledge.embedder.google", "GeminiEmbedder"),
    "huggingface": ("egpt.integrations.knowledge.embedder.huggingface", "HuggingfaceCustomEmbedder"),
    "jina": ("egpt.integrations.knowledge.embedder.jina", "JinaEmbedder"),
    "langdb": ("egpt.integrations.knowledge.embedder.langdb", "LangDBEmbedder"),
    "mistral": ("egpt.integrations.knowledge.embedder.mistral", "MistralEmbedder"),
    "nebius": ("egpt.integrations.knowledge.embedder.nebius", "NebiusEmbedder"),
    "ollama": ("egpt.integrations.knowledge.embedder.ollama", "OllamaEmbedder"),
    "openai": ("egpt.integrations.knowledge.embedder.openai", "OpenAIEmbedder"),
    "openai_like": ("egpt.integrations.knowledge.embedder.openai_like", "OpenAILikeEmbedder"),
    "sentence_transformer": (
        "egpt.integrations.knowledge.embedder.sentence_transformer",
        "SentenceTransformerEmbedder",
    ),
    "together": ("egpt.integrations.knowledge.embedder.together", "TogetherEmbedder"),
    "vllm": ("egpt.integrations.knowledge.embedder.vllm", "VLLMEmbedder"),
    "voyageai": ("egpt.integrations.knowledge.embedder.voyageai", "VoyageAIEmbedder"),
}


def register_all() -> None:
    """Register every row. Called by the discovery seam, and safe to call again."""
    for key, (module, class_name) in _ROWS.items():
        register("embedder", key, module=module, attr=class_name, source=SOURCE_BUILTIN, override=True)
