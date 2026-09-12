/**
 * Server-only LLM routing (Vercel Edge / Node). Reads RETAILGPT_LLM_* from process.env — never import in client code.
 */

function trimBaseUrl(url) {
  if (!url || typeof url !== "string") return "";
  return url.replace(/\/+$/, "");
}

function getLlmProvider() {
  const explicit = (process.env.RETAILGPT_LLM_PROVIDER || "").toLowerCase().trim();
  if (explicit === "azure") return "azure";
  if (explicit === "openai") return "openai";
  const base = (process.env.RETAILGPT_LLM_API_BASE_URL || "").toLowerCase();
  if (base.includes("openai.azure.com")) return "azure";
  return "openai";
}

function azureDeploymentName() {
  return (
    (process.env.RETAILGPT_LLM_AZURE_DEPLOYMENT || process.env.RETAILGPT_LLM_MODEL || "").trim() || ""
  );
}

function azureApiVersion() {
  return (process.env.RETAILGPT_LLM_AZURE_API_VERSION || "2024-06-01").trim();
}

/**
 * OpenAI's newer model families (gpt-5, o1, o3, o4, and the "gpt-4.1" that
 * ships the new response schema) reject the legacy `max_tokens` parameter
 * and require `max_completion_tokens` instead. Detect by model name, with
 * an explicit env override for anything custom or forward-looking.
 *
 * Set `RETAILGPT_LLM_TOKENS_PARAM` to either `max_tokens` or
 * `max_completion_tokens` to force the field name and bypass detection.
 */
function maxTokensFieldName() {
  const override = (process.env.RETAILGPT_LLM_TOKENS_PARAM || "").trim().toLowerCase();
  if (override === "max_tokens" || override === "max_completion_tokens") return override;

  // Pull from the Azure deployment when present (deployment name is usually
  // the model family with a suffix, e.g. "gpt-5.4-mini-prod").
  const candidate = (
    process.env.RETAILGPT_LLM_AZURE_DEPLOYMENT ||
    process.env.RETAILGPT_LLM_MODEL ||
    ""
  )
    .trim()
    .toLowerCase();
  if (!candidate) return "max_tokens";
  if (/^(gpt-5|o1|o3|o4)\b/.test(candidate)) return "max_completion_tokens";
  if (/\bgpt-5\b|\bgpt-4\.1\b/.test(candidate)) return "max_completion_tokens";
  return "max_tokens";
}

export function isRetailgptLlmConfigured() {
  const base = trimBaseUrl(process.env.RETAILGPT_LLM_API_BASE_URL || "");
  const key = (process.env.RETAILGPT_LLM_API_KEY || "").trim();
  if (!base || !key) return false;
  if (getLlmProvider() === "azure") {
    return Boolean(azureDeploymentName());
  }
  return true;
}

/**
 * @param {Array<{ role: string, content: string }>} messages
 * @param {{ max_tokens: number, temperature: number, response_format?: { type: string } }} req
 * @returns {Promise<Response>}
 */
export async function forwardRetailgptChatCompletion(messages, req) {
  const base = trimBaseUrl(process.env.RETAILGPT_LLM_API_BASE_URL || "");
  const key = (process.env.RETAILGPT_LLM_API_KEY || "").trim();
  const model = (process.env.RETAILGPT_LLM_MODEL || "gpt-4o-mini").trim();
  const provider = getLlmProvider();

  let url;
  /** @type {Record<string, string>} */
  const headers = { "Content-Type": "application/json" };
  /** @type {Record<string, unknown>} */
  const body = {
    messages,
    temperature: req.temperature,
  };
  body[maxTokensFieldName()] = req.max_tokens;

  if (req.response_format) {
    body.response_format = req.response_format;
  }

  if (provider === "azure") {
    const deployment = encodeURIComponent(azureDeploymentName());
    const apiVersion = encodeURIComponent(azureApiVersion());
    url = `${base}/openai/deployments/${deployment}/chat/completions?api-version=${apiVersion}`;
    headers["api-key"] = key;
  } else {
    url = `${base}/chat/completions`;
    headers.Authorization = `Bearer ${key}`;
    body.model = model;
  }

  return fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}
