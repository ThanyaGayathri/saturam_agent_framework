/** Catalogs for Configure workspace (internal sources, LLM, cloud, external integrations). */

/** Internal data source connector types (all selectable in Configure). */
export const INTERNAL_SOURCE_TYPES = [
  { id: "postgres", label: "PostgreSQL", category: "Relational", implemented: true },
  { id: "snowflake", label: "Snowflake", category: "Data warehouse", implemented: true },
  { id: "sap_s4hana", label: "SAP S/4HANA", category: "Enterprise ERP", implemented: true },
  { id: "salesforce", label: "Salesforce", category: "CRM & Customer Data", implemented: true },
  { id: "hubspot", label: "HubSpot CRM", category: "CRM & Customer Data", implemented: true },
  { id: "logrocket", label: "LogRocket", category: "Product analytics", implemented: true },
  { id: "mssql", label: "Microsoft SQL Server", category: "Relational", implemented: true },
  { id: "bigquery", label: "Google BigQuery", category: "Data warehouse", implemented: true },
  { id: "mysql", label: "MySQL", category: "Relational", implemented: true },
  { id: "oracle_db", label: "Oracle Database", category: "Relational", implemented: true },
  { id: "netsuite", label: "Oracle NetSuite", category: "Enterprise", implemented: true },
  { id: "dynamics365", label: "Microsoft Dynamics 365", category: "Enterprise", implemented: true },
  { id: "airflow", label: "Apache Airflow", category: "Orchestration", implemented: true },
  { id: "aws_glue", label: "AWS Glue", category: "Orchestration", implemented: true },
  { id: "s3", label: "Amazon S3", category: "Object storage", implemented: true },
];

export const INTERNAL_AUTH_METHODS = [
  { value: "username_password", label: "Username and Password" },
  { value: "api_key", label: "API Key" },
  { value: "oauth", label: "OAuth" },
];

export const SSL_MODES = [
  { value: "disable", label: "Disable" },
  { value: "allow", label: "Allow" },
  { value: "prefer", label: "Prefer" },
  { value: "require", label: "Require" },
  { value: "verify_ca", label: "Verify CA" },
  { value: "verify_full", label: "Verify Full" },
];

export const LLM_PROVIDERS = [
  {
    id: "openai",
    label: "OpenAI",
    implemented: true,
    models: [
      { value: "gpt-4o", label: "gpt-4o" },
      { value: "gpt-4o-mini", label: "gpt-4o-mini" },
      { value: "gpt-4-turbo", label: "gpt-4-turbo" },
      { value: "gpt-3.5-turbo", label: "gpt-3.5-turbo" },
    ],
    needsBaseUrl: false,
  },
  {
    id: "anthropic",
    label: "Anthropic",
    implemented: true,
    models: [
      { value: "claude-sonnet-4-20250514", label: "claude-sonnet-4-20250514" },
      { value: "claude-opus-4-20250514", label: "claude-opus-4-20250514" },
      { value: "claude-haiku-4-20250514", label: "claude-haiku-4-20250514" },
    ],
    needsBaseUrl: false,
  },
  {
    id: "google",
    label: "Google",
    implemented: true,
    models: [
      { value: "gemini-2.0-flash", label: "gemini-2.0-flash" },
      { value: "gemini-1.5-pro", label: "gemini-1.5-pro" },
      { value: "gemini-1.5-flash", label: "gemini-1.5-flash" },
    ],
    needsBaseUrl: false,
  },
  {
    id: "meta_os",
    label: "Meta and Open Source",
    implemented: true,
    models: [
      { value: "llama-3.1-70b", label: "llama-3.1-70b" },
      { value: "mistral-large", label: "mistral-large" },
    ],
    needsBaseUrl: false,
  },
  {
    id: "azure_openai",
    label: "Azure OpenAI",
    implemented: true,
    models: [
      { value: "gpt-4o", label: "gpt-4o" },
      { value: "gpt-4o-mini", label: "gpt-4o-mini" },
      { value: "gpt-4-turbo", label: "gpt-4-turbo" },
    ],
    needsBaseUrl: true,
  },
  {
    id: "bedrock",
    label: "AWS Bedrock",
    implemented: true,
    models: [
      { value: "anthropic.claude-3-5-sonnet-20240620-v1:0", label: "Claude 3.5 Sonnet" },
      { value: "meta.llama3-70b-instruct-v1:0", label: "Llama 3 70B Instruct" },
    ],
    needsBaseUrl: false,
  },
];

/**
 * Retail (default + GFI) external platform catalog.
 * Media profile has its own list — see MEDIA_EXTERNAL_INTEGRATIONS below.
 */
export const EXTERNAL_INTEGRATIONS = [
  {
    id: "shopify",
    label: "Shopify",
    category: "E-Commerce and Distribution",
    implemented: true,
    defaultAuth: "oauth2",
  },
  {
    id: "klaviyo",
    label: "Klaviyo",
    category: "Marketing and Loyalty",
    implemented: true,
    defaultAuth: "api_key",
  },
  {
    id: "slack",
    label: "Slack",
    category: "Communication and Alerts",
    implemented: true,
    defaultAuth: "oauth2",
  },
  {
    id: "stripe",
    label: "Stripe",
    category: "Payments",
    implemented: true,
    defaultAuth: "api_key",
  },
  { id: "sfcc", label: "Salesforce Commerce Cloud", category: "E-Commerce", implemented: true, defaultAuth: "oauth2" },
  { id: "magento", label: "Adobe Commerce / Magento", category: "E-Commerce", implemented: true, defaultAuth: "api_key" },
  { id: "braze", label: "Braze", category: "Marketing", implemented: true, defaultAuth: "api_key" },
  { id: "teams", label: "Microsoft Teams", category: "Communication", implemented: true, defaultAuth: "oauth2" },
  { id: "fedex", label: "FedEx", category: "Logistics", implemented: true, defaultAuth: "api_key" },
  { id: "webhook", label: "Custom Webhook", category: "Programmatic", implemented: true, defaultAuth: "webhook" },
];

/** Media & OTT external platform catalog (Default Media profile). */
export const MEDIA_EXTERNAL_INTEGRATIONS = [
  { id: "youtube", label: "YouTube Analytics", category: "Video analytics", implemented: true, defaultAuth: "oauth2" },
  { id: "instagram", label: "Instagram / Meta Business", category: "Social listening", implemented: true, defaultAuth: "oauth2" },
  { id: "twitter", label: "X / Twitter", category: "Social listening", implemented: true, defaultAuth: "oauth2" },
  { id: "google_trends", label: "Google Trends", category: "Search intelligence", implemented: true, defaultAuth: "api_key" },
  { id: "similarweb", label: "Similarweb", category: "Competitor analytics", implemented: true, defaultAuth: "api_key" },
  { id: "perplexity", label: "Perplexity", category: "AI answer engines", implemented: true, defaultAuth: "api_key" },
  { id: "chatgpt", label: "ChatGPT (AEO tracker)", category: "AI answer engines", implemented: true, defaultAuth: "api_key" },
  { id: "google_ai_overviews", label: "Google AI Overviews", category: "AI answer engines", implemented: true, defaultAuth: "api_key" },
  { id: "barc", label: "BARC India", category: "Broadcast ratings", implemented: true, defaultAuth: "api_key" },
  { id: "gam", label: "Google Ad Manager", category: "Ad-ops", implemented: true, defaultAuth: "oauth2" },
  { id: "brandwatch", label: "Brandwatch / Talkwalker", category: "Social listening", implemented: true, defaultAuth: "api_key" },
  { id: "slack", label: "Slack", category: "Communication and Alerts", implemented: true, defaultAuth: "oauth2" },
  { id: "teams", label: "Microsoft Teams", category: "Communication", implemented: true, defaultAuth: "oauth2" },
  { id: "webhook", label: "Custom Webhook", category: "Programmatic", implemented: true, defaultAuth: "webhook" },
];

/** Profile-aware external integrations catalog. */
export function externalIntegrationsForProfile(profileId) {
  return profileId === "default-media" ? MEDIA_EXTERNAL_INTEGRATIONS : EXTERNAL_INTEGRATIONS;
}

/** Profile-aware lookup by id (matches getExternalIntegrationById below). */
export function getExternalIntegrationByIdForProfile(id, profileId) {
  const list = externalIntegrationsForProfile(profileId);
  return list.find((e) => e.id === id) || EXTERNAL_INTEGRATIONS.find((e) => e.id === id);
}

export const INTEGRATION_AUTH_TYPES = [
  { value: "oauth2", label: "OAuth 2.0" },
  { value: "api_key", label: "API Key" },
  { value: "basic", label: "Basic Auth" },
  { value: "webhook", label: "Webhook (inbound)" },
];

export const SYNC_FREQUENCIES = [
  { value: "realtime", label: "Real-time" },
  { value: "15m", label: "Every 15 minutes" },
  { value: "hourly", label: "Hourly" },
  { value: "daily", label: "Daily" },
];

export const DEPLOYMENT_ENVIRONMENTS = [
  { value: "production", label: "Production" },
  { value: "staging", label: "Staging" },
  { value: "development", label: "Development" },
];

export const INTEGRATION_ENVIRONMENTS = [
  { value: "production", label: "Production" },
  { value: "sandbox", label: "Sandbox" },
  { value: "development", label: "Development" },
];

export const CLOUD_PROVIDERS = [
  { value: "gcp", label: "GCP" },
  { value: "aws", label: "AWS" },
  { value: "azure", label: "Azure" },
  { value: "on_premise", label: "On-premise" },
  { value: "hybrid", label: "Hybrid" },
];

/** Simplified region lists for demo (not exhaustive). */
export const CLOUD_REGIONS_BY_PROVIDER = {
  gcp: ["us-central1", "us-east1", "europe-west1", "asia-south1"],
  aws: ["us-east-1", "us-west-2", "eu-west-1", "ap-southeast-1"],
  azure: ["eastus", "westeurope", "southeastasia"],
  on_premise: ["N/A (on-premise)"],
  hybrid: ["Configured per workload"],
};

export function internalSourceTypeLabel(id) {
  return INTERNAL_SOURCE_TYPES.find((t) => t.id === id)?.label || id;
}

export function llmProviderLabel(id) {
  return LLM_PROVIDERS.find((p) => p.id === id)?.label || id;
}

export function externalIntegrationMeta(id) {
  return EXTERNAL_INTEGRATIONS.find((e) => e.id === id);
}
