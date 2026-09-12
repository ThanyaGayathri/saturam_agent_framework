import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, CircleHelp, Key, Globe, CheckCircle, Unplug } from "lucide-react";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import { LLM_PROVIDERS } from "../../domain/configure-catalogs";
import { normalizeLlm } from "../../storage/configure-workspace";

/* ─── Shared form primitives ─────────────────────────────── */
function SectionLabel({ label, required }) {
  return (
    <div className="flex items-center gap-1.5 pt-1">
      <span className="text-sm font-semibold text-text-primary">{label}</span>
      {required && <span className="text-[#d92d20]">*</span>}
      <CircleHelp size={14} className="shrink-0 text-text-muted" />
    </div>
  );
}

function IconInput({ icon: Icon, label, required, type = "text", value, onChange, placeholder }) {
  return (
    <div>
      {label && (
        <label className="mb-1.5 block text-sm font-medium text-text-primary">
          {label}{required && <span className="ml-0.5 text-[#d92d20]">*</span>}
        </label>
      )}
      <div className="relative">
        {Icon && <Icon size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />}
        <input
          type={type}
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          className={`w-full rounded-lg border border-border-light py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6] ${
            Icon ? "pl-9 pr-3" : "px-3"
          }`}
        />
      </div>
    </div>
  );
}
/* ──────────────────────────────────────────────────────────── */

export default function ConfigureModelForm() {
  const { id }   = useParams();
  const navigate = useNavigate();
  const isNew    = !id || id === "new";
  const { data, hydrated, upsertLlm, addLlm } = useConfigureWorkspace();

  const [providerId, setProviderId] = useState(() => LLM_PROVIDERS[0]?.id || "openai");
  const [modelId,    setModelId   ] = useState("");
  const [apiKey,     setApiKey    ] = useState("");
  const [baseUrl,    setBaseUrl   ] = useState("");
  const [isDefault,  setIsDefault ] = useState(false);

  const existing = useMemo(() => {
    if (isNew) return null;
    return (data.llmModels || []).find((x) => x.id === decodeURIComponent(id));
  }, [data.llmModels, id, isNew]);

  const provider     = LLM_PROVIDERS.find((p) => p.id === providerId);
  const modelOptions = (provider?.models || []).map((m) => ({ value: m.value, label: m.label }));
  const showBaseUrl  = provider?.needsBaseUrl;

  useEffect(() => {
    if (!hydrated) return;
    if (isNew) {
      const p = LLM_PROVIDERS[0];
      setProviderId(p?.id || "openai");
      setModelId(p?.models?.[0]?.value || "");
      setApiKey("");
      setBaseUrl("");
      setIsDefault((data.llmModels || []).length === 0);
      return;
    }
    if (existing) {
      setProviderId(existing.providerId);
      setModelId(existing.modelId);
      setApiKey(existing.apiKey);
      setBaseUrl(existing.baseUrl);
      setIsDefault(!!existing.isDefault);
    }
  }, [hydrated, isNew, existing, data.llmModels?.length]);

  useEffect(() => {
    if (!provider?.models?.length) return;
    if (!modelOptions.some((o) => o.value === modelId)) {
      setModelId(provider.models[0].value);
    }
  }, [providerId]);

  if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;
  if (!isNew && !existing) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-text-secondary">Model not found.</p>
        <button type="button" onClick={() => navigate("/configure/models")}
          className="text-sm font-medium text-primary hover:underline">← Back</button>
      </div>
    );
  }

  const onSave = () => {
    const payload = {
      providerId,
      modelId,
      apiKey,
      baseUrl: showBaseUrl ? baseUrl : "",
      isDefault,
    };
    if (isNew) addLlm(payload);
    else upsertLlm(normalizeLlm({ id: existing.id, ...payload }));
    navigate("/configure/models");
  };

  const canSave = apiKey.trim() && modelId;

  return (
    <div className="space-y-5">
      {/* Back link */}
      <button type="button" onClick={() => navigate("/configure/models")}
        className="flex items-center gap-1.5 text-sm font-semibold text-primary hover:text-[#7f56d9]">
        <ArrowLeft size={16} />
        Back to configure
      </button>

      {/* Main card */}
      <div className="rounded-2xl border border-border bg-surface">
        {/* Header */}
        <div className="px-8 py-6">
          <h2 className="text-xl font-bold text-text-primary">
            {isNew ? "Add Model" : "Edit Model"}
          </h2>
          <p className="mt-1 text-sm text-text-secondary">
            Choose a provider and model, then enter your API key.
          </p>
        </div>
        <div className="border-t border-border" />

        {/* Model Provider section */}
        <div className="grid grid-cols-[200px_1fr] gap-8 px-8 py-6">
          <SectionLabel label="Model Provider" required />
          <div className="space-y-4">
            {/* Provider select */}
            <div>
              <label className="mb-1.5 block text-sm font-medium text-text-primary">
                Provider <span className="text-[#d92d20]">*</span>
              </label>
              <div className="relative">
                <select
                  value={providerId}
                  onChange={(e) => setProviderId(e.target.value)}
                  className="w-full appearance-none rounded-lg border border-border-light py-2.5 pl-3 pr-8 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6]"
                >
                  {LLM_PROVIDERS.map((p) => (
                    <option key={p.id} value={p.id}>{p.label}</option>
                  ))}
                </select>
                <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-muted">▾</span>
              </div>
            </div>

            {/* Model select */}
            <div>
              <label className="mb-1.5 block text-sm font-medium text-text-primary">
                Model <span className="text-[#d92d20]">*</span>
              </label>
              <div className="relative">
                <select
                  value={modelId}
                  onChange={(e) => setModelId(e.target.value)}
                  disabled={!modelOptions.length}
                  className="w-full appearance-none rounded-lg border border-border-light py-2.5 pl-3 pr-8 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6] disabled:bg-surface-tertiary"
                >
                  {modelOptions.length
                    ? modelOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)
                    : <option value="">No models for this provider</option>}
                </select>
                <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-muted">▾</span>
              </div>
            </div>

            {/* Default toggle */}
            <label className="flex cursor-pointer items-center gap-2.5">
              <input
                type="checkbox"
                checked={isDefault}
                onChange={(e) => setIsDefault(e.target.checked)}
                className="h-4 w-4 rounded border-border-light text-primary focus:ring-[#6941c6]"
              />
              <span className="text-sm text-text-primary">
                Set as default model for all agents
                <span className="ml-1 text-text-muted">(can be overridden per use case)</span>
              </span>
            </label>
          </div>
        </div>

        <div className="border-t border-border" />

        {/* Authentication section */}
        <div className="grid grid-cols-[200px_1fr] gap-8 px-8 py-6">
          <SectionLabel label="Authentication" required />
          <div className="space-y-4">
            <IconInput
              icon={Key}
              label="API Key"
              required
              type="password"
              placeholder="sk-…"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
            {showBaseUrl && (
              <IconInput
                icon={Globe}
                label="Base URL"
                placeholder="https://…"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            )}
          </div>
        </div>

        {/* Test connection panel */}
        <div className="mx-6 mb-6 flex items-center justify-between gap-4 rounded-xl bg-surface-tertiary px-5 py-4">
          <div className="flex items-center gap-3">
            <Unplug size={22} className="shrink-0 text-text-secondary" />
            <div>
              <p className="font-semibold text-text-primary">Test your connection</p>
              <p className="text-xs text-text-secondary">Verify your API key is valid</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => window.alert("Demo: API key validation would run here.")}
            className="shrink-0 rounded-lg border border-border-light bg-surface px-4 py-2 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
          >
            Test Connection
          </button>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between py-2">
        <button
          type="button"
          onClick={() => { setApiKey(""); setBaseUrl(""); setIsDefault(false); }}
          className="text-sm font-medium text-text-secondary hover:text-text-primary"
        >
          Reset to default
        </button>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => navigate("/configure/models")}
            className="rounded-lg border border-border-light px-4 py-2 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={!canSave}
            className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${
              canSave
                ? "bg-primary text-white hover:bg-primary-hover"
                : "cursor-default bg-surface-tertiary text-text-muted"
            }`}
          >
            <CheckCircle size={15} />
            {isNew ? "Add Model" : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
