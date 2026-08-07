"""Built-in model provider declarations.

One row per stable provider key:

    key -> (module, class_name, default_name, default_provider_display)

``default_name`` and ``default_provider_display`` are the class's default ``name`` and
(lowercased) ``provider`` attributes -- what ``Model.to_dict`` serializes. They are carried
as registration metadata because ``egpt/models/utils.py`` derives its ``(provider, name)``
resolution indices from them, so a serialized model rebuilds as the right class even when
several providers share a display string.

``module`` names the re-exporting package (``egpt.integrations.models.azure``), not the
defining submodule, so the layer-guards can chase the re-export back to the definition.
``vertexai-claude`` is the one exception -- its package does not re-export ``Claude``.

Adding a provider here is a one-row change. Adding one from outside this repository needs
no change here at all: declare an ``egpt.models`` entry point in your own package.
"""

from egpt.registry.discovery import SOURCE_BUILTIN, register

# Keys whose class is the default for a display `provider` string shared by several
# classes. "azure" alone is ambiguous between AzureOpenAI and AzureAIFoundry; "awsbedrock"
# between AwsBedrock and the Bedrock Claude wrapper. Marking the row rather than keeping a
# separate lookup means a third-party provider family can declare its own default too.
_PROVIDER_DEFAULTS = frozenset({"azure-openai", "aws-bedrock"})

_ROWS = {
    "aimlapi": ("egpt.integrations.models.aimlapi", "AIMLAPI", "AIMLAPI", "aimlapi"),
    "anthropic": ("egpt.integrations.models.anthropic", "Claude", "Claude", "anthropic"),
    "aws-bedrock": ("egpt.integrations.models.aws", "AwsBedrock", "AwsBedrock", "awsbedrock"),
    "aws-claude": ("egpt.integrations.models.aws", "Claude", "AwsBedrockAnthropicClaude", "awsbedrock"),
    "azure-ai-foundry": ("egpt.integrations.models.azure", "AzureAIFoundry", "AzureAIFoundry", "azure"),
    "azure-foundry-claude": (
        "egpt.integrations.models.azure",
        "AzureFoundryClaude",
        "AzureFoundryClaude",
        "azurefoundry",
    ),
    "azure-openai": ("egpt.integrations.models.azure", "AzureOpenAI", "AzureOpenAI", "azure"),
    "cerebras": ("egpt.integrations.models.cerebras", "Cerebras", "Cerebras", "cerebras"),
    "cerebras-openai": ("egpt.integrations.models.cerebras", "CerebrasOpenAI", "CerebrasOpenAI", "cerebrasopenai"),
    "cohere": ("egpt.integrations.models.cohere", "Cohere", "cohere", "cohere"),
    "cometapi": ("egpt.integrations.models.cometapi", "CometAPI", "CometAPI", "openai"),
    "cloudflare": ("egpt.integrations.models.cloudflare", "Cloudflare", "Cloudflare", "cloudflare"),
    "dashscope": ("egpt.integrations.models.dashscope", "DashScope", "Qwen", "dashscope"),
    "deepinfra": ("egpt.integrations.models.deepinfra", "DeepInfra", "DeepInfra", "deepinfra"),
    "deepseek": ("egpt.integrations.models.deepseek", "DeepSeek", "DeepSeek", "deepseek"),
    "fireworks": ("egpt.integrations.models.fireworks", "Fireworks", "Fireworks", "fireworks"),
    "google": ("egpt.integrations.models.google", "Gemini", "Gemini", "google"),
    "google-interactions": ("egpt.integrations.models.google", "GeminiInteractions", "GeminiInteractions", "google"),
    "groq": ("egpt.integrations.models.groq", "Groq", "Groq", "groq"),
    "huggingface": ("egpt.integrations.models.huggingface", "HuggingFace", "HuggingFace", "huggingface"),
    "ibm": ("egpt.integrations.models.ibm", "WatsonX", "WatsonX", "ibm"),
    "inception": ("egpt.integrations.models.inception", "Inception", "Inception", "inceptionlabs"),
    "internlm": ("egpt.integrations.models.internlm", "InternLM", "InternLM", "internlm"),
    "langdb": ("egpt.integrations.models.langdb", "LangDB", "LangDB", "langdb"),
    "litellm": ("egpt.integrations.models.litellm", "LiteLLM", "LiteLLM", "litellm"),
    "litellm-openai": ("egpt.integrations.models.litellm", "LiteLLMOpenAI", "LiteLLMOpenAI", "litellm"),
    "llama-cpp": ("egpt.integrations.models.llama_cpp", "LlamaCpp", "LlamaCpp", "llamacpp"),
    "llama-openai": ("egpt.integrations.models.meta", "LlamaOpenAI", "LlamaOpenAI", "llamaopenai"),
    "lmstudio": ("egpt.integrations.models.lmstudio", "LMStudio", "LMStudio", "lmstudio"),
    "meta": ("egpt.integrations.models.meta", "Llama", "Llama", "llama"),
    "minimax": ("egpt.integrations.models.minimax", "MiniMax", "MiniMax", "minimax"),
    "mistral": ("egpt.integrations.models.mistral", "MistralChat", "MistralChat", "mistral"),
    "moonshot": ("egpt.integrations.models.moonshot", "MoonShot", "Moonshot", "moonshot"),
    "n1n": ("egpt.integrations.models.n1n", "N1N", "N1N", "n1n"),
    "nebius": ("egpt.integrations.models.nebius", "Nebius", "Nebius", "nebius"),
    "neosantara": ("egpt.integrations.models.neosantara", "Neosantara", "Neosantara", "neosantara"),
    "nexus": ("egpt.integrations.models.nexus", "Nexus", "Nexus", "nexus"),
    "nvidia": ("egpt.integrations.models.nvidia", "Nvidia", "Nvidia", "nvidia"),
    "ollama": ("egpt.integrations.models.ollama", "Ollama", "Ollama", "ollama"),
    "ollama-responses": ("egpt.integrations.models.ollama", "OllamaResponses", "OllamaResponses", "ollama"),
    # "openai" and "openai-responses" deliberately share a target: "openai" is the stable
    # default key and OpenAIResponses is what it builds. That duplicate is why the name
    # index below drops "OpenAIResponses" as ambiguous.
    "openai": ("egpt.integrations.models.openai", "OpenAIResponses", "OpenAIResponses", "openai"),
    "openai-chat": ("egpt.integrations.models.openai", "OpenAIChat", "OpenAIChat", "openai"),
    "openai-responses": ("egpt.integrations.models.openai", "OpenAIResponses", "OpenAIResponses", "openai"),
    "open-responses": ("egpt.integrations.models.openai", "OpenResponses", "OpenResponses", "openresponses"),
    "openrouter": ("egpt.integrations.models.openrouter", "OpenRouter", "OpenRouter", "openrouter"),
    "openrouter-responses": (
        "egpt.integrations.models.openrouter",
        "OpenRouterResponses",
        "OpenRouterResponses",
        "openrouter",
    ),
    "perplexity": ("egpt.integrations.models.perplexity", "Perplexity", "Perplexity", "perplexity"),
    "portkey": ("egpt.integrations.models.portkey", "Portkey", "Portkey", "portkey"),
    "requesty": ("egpt.integrations.models.requesty", "Requesty", "Requesty", "requesty"),
    "sambanova": ("egpt.integrations.models.sambanova", "Sambanova", "Sambanova", "sambanova"),
    "siliconflow": ("egpt.integrations.models.siliconflow", "Siliconflow", "Siliconflow", "siliconflow"),
    "together": ("egpt.integrations.models.together", "Together", "Together", "together"),
    "tuning-engines": ("egpt.integrations.models.tuning_engines", "TuningEngines", "Tuning Engines", "tuning engines"),
    "vercel": ("egpt.integrations.models.vercel", "V0", "v0", "vercel"),
    "vertexai-claude": ("egpt.integrations.models.vertexai.claude", "Claude", "Claude", "vertexai"),
    "vllm": ("egpt.integrations.models.vllm", "VLLM", "VLLM", "vllm"),
    "xai": ("egpt.integrations.models.xai", "xAI", "xAI", "xai"),
    "xiaomi": ("egpt.integrations.models.xiaomi", "MiMo", "MiMo", "xiaomi mimo"),
}


def register_all() -> None:
    """Register every row. Called by the discovery seam, and safe to call again."""
    for key, (module, class_name, default_name, provider_display) in _ROWS.items():
        register(
            "model",
            key,
            module=module,
            attr=class_name,
            source=SOURCE_BUILTIN,
            override=True,
            default_name=default_name,
            provider_display=provider_display,
            provider_default=key in _PROVIDER_DEFAULTS,
        )
