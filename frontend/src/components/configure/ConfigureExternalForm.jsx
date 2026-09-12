import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, CircleHelp, Key, Globe, Link2, CheckCircle, Unplug, Mail, Lock } from "lucide-react";
import { Modal } from "../ui";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import {
  EXTERNAL_INTEGRATIONS,
  INTEGRATION_AUTH_TYPES,
  SYNC_FREQUENCIES,
  INTEGRATION_ENVIRONMENTS,
  externalIntegrationMeta,
} from "../../domain/configure-catalogs";
import { normalizeExternal } from "../../storage/configure-workspace";
import shopifySvg from "../../assets/shopify.svg";
import slackSvg   from "../../assets/slack.svg";
import salesforceSvg from "../../assets/salesforce.svg";

const BRAND_ICON = {
  shopify: shopifySvg,
  slack:   slackSvg,
  sfcc:    salesforceSvg,
};

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

function IconInput({ icon: Icon, label, required, type = "text", value, onChange, placeholder, readOnly }) {
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
          readOnly={readOnly}
          className={`w-full rounded-lg border border-border-light py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6] ${
            Icon ? "pl-9 pr-3" : "px-3"
          } ${readOnly ? "bg-surface-tertiary text-text-secondary" : ""}`}
        />
      </div>
    </div>
  );
}

function StyledSelect({ label, required, value, onChange, children, disabled }) {
  return (
    <div>
      {label && (
        <label className="mb-1.5 block text-sm font-medium text-text-primary">
          {label}{required && <span className="ml-0.5 text-[#d92d20]">*</span>}
        </label>
      )}
      <select
        value={value}
        onChange={onChange}
        disabled={disabled}
        className="w-full rounded-lg border border-border-light py-2.5 pl-3 pr-8 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6] disabled:bg-surface-tertiary disabled:text-text-secondary"
      >
        {children}
      </select>
    </div>
  );
}
/* ──────────────────────────────────────────────────────────── */

const DEFAULTS = {
  providerId:   "shopify",
  name:         "",
  integrationType: "",
  environment:  "production",
  syncFrequency: "hourly",
  dataScope:    "orders, customers, inventory",
  status:       "active",
  authType:     "api_key",
  clientId: "", clientSecret: "", authUrl: "", tokenUrl: "",
  redirectUri: "", scopes: "", apiKey: "", apiSecret: "",
  baseUrl: "", apiVersion: "", basicUser: "", basicPassword: "",
  webhookSecret: "", webhookEvents: "order.created, inventory.updated",
};

const WEBHOOK_URL = "https://api.retailgpt.demo/webhooks/inbound/demo-id";

export default function ConfigureExternalForm() {
  const { id }           = useParams();
  const [searchParams]   = useSearchParams();
  const navigate         = useNavigate();
  const isNew            = !id || id === "new";
  const { data, hydrated, upsertExternal, addExternal } = useConfigureWorkspace();

  const [form, setForm] = useState(DEFAULTS);

  const existing = useMemo(() => {
    if (isNew) return null;
    return (data.externalIntegrations || []).find((x) => x.id === decodeURIComponent(id));
  }, [data.externalIntegrations, id, isNew]);

  useEffect(() => {
    if (!hydrated) return;
    if (isNew) {
      const q   = searchParams.get("provider");
      const pid = q && externalIntegrationMeta(q) ? q : "shopify";
      const m   = externalIntegrationMeta(pid);
      setForm({
        ...DEFAULTS,
        providerId:      pid,
        authType:        m?.defaultAuth || "api_key",
        integrationType: m?.category    || "",
        name:            m?.label ? `${m.label} (production)` : "",
      });
      return;
    }
    if (existing) setForm({ ...DEFAULTS, ...existing });
  }, [hydrated, isNew, existing, searchParams]);

  if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;
  if (!isNew && !existing) {
    return (
      <div className="space-y-4">
        <p className="text-sm text-text-secondary">Integration not found.</p>
        <button type="button" onClick={() => navigate("/configure/external")}
          className="text-sm font-medium text-primary hover:underline">← Back</button>
      </div>
    );
  }

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const meta = externalIntegrationMeta(form.providerId);

  const onSave = () => {
    const common = {
      ...form,
      webhookUrl:       WEBHOOK_URL,
      connectionStatus: "connected",
      name:             form.name.trim(),
      baseUrl:          form.authType !== "oauth2" ? form.baseUrl : form.baseUrl,
    };
    if (isNew) addExternal(common);
    else upsertExternal(normalizeExternal({ id: existing.id, ...common }));
    navigate("/configure/external");
  };

  const onTestConnection = () => window.alert("Demo: connection test would run here.");

  const providerIcon = BRAND_ICON[form.providerId];
  const canSave = form.name.trim() && form.providerId;

  const AUTH_OPTIONS = [
    { value: "oauth2",   label: "OAuth 2.0"  },
    { value: "api_key",  label: "API Key"     },
    { value: "basic",    label: "Basic Auth"  },
    { value: "webhook",  label: "Webhook"     },
  ];

  return (
    <div className="space-y-5">
      {/* Back link */}
      <button type="button" onClick={() => navigate("/configure/external")}
        className="flex items-center gap-1.5 text-sm font-semibold text-primary hover:text-[#7f56d9]">
        <ArrowLeft size={16} />
        Back to configure
      </button>

      {/* Main card */}
      <div className="rounded-2xl border border-border bg-surface">
        {/* Card header */}
        <div className="px-8 py-6">
          <h2 className="text-xl font-bold text-text-primary">
            {isNew ? "New Integration" : "Edit Integration"}
          </h2>
          <p className="mt-1 text-sm text-text-secondary">
            Configure provider, authentication, and sync settings.
          </p>
        </div>
        <div className="border-t border-border" />

        {/* Integration Details */}
        <div className="grid grid-cols-[200px_1fr] gap-8 px-8 py-6">
          <SectionLabel label="Integration Details" required />
          <div className="space-y-4">
            {/* Provider with icon */}
            <div>
              <label className="mb-1.5 block text-sm font-medium text-text-primary">
                Provider <span className="text-[#d92d20]">*</span>
              </label>
              <div className="flex items-center gap-2">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-surface p-1.5">
                  {providerIcon
                    ? <img src={providerIcon} alt="" className="h-full w-full object-contain" />
                    : <Link2 size={16} className="text-primary" />}
                </div>
                <div className="relative flex-1">
                  <select
                    value={form.providerId}
                    disabled={!isNew}
                    onChange={(e) => {
                      const pid = e.target.value;
                      const m   = externalIntegrationMeta(pid);
                      set("providerId", pid);
                      if (m?.defaultAuth) set("authType", m.defaultAuth);
                      set("integrationType", m?.category || "");
                    }}
                    className="w-full appearance-none rounded-lg border border-border-light py-2.5 pl-3 pr-8 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6] disabled:bg-surface-tertiary"
                  >
                    {EXTERNAL_INTEGRATIONS.map((e) => (
                      <option key={e.id} value={e.id}>{e.label}</option>
                    ))}
                  </select>
                  <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-muted">▾</span>
                </div>
              </div>
            </div>

            <IconInput icon={Link2} label="Integration name" required
              placeholder={`${meta?.label || "Integration"} (production)`}
              value={form.name} onChange={(e) => set("name", e.target.value)} />

            <div className="grid grid-cols-2 gap-3">
              <StyledSelect label="Environment" value={form.environment}
                onChange={(e) => set("environment", e.target.value)}>
                {INTEGRATION_ENVIRONMENTS.map((x) => (
                  <option key={x.value} value={x.value}>{x.label}</option>
                ))}
              </StyledSelect>
              <StyledSelect label="Sync frequency" value={form.syncFrequency}
                onChange={(e) => set("syncFrequency", e.target.value)}>
                {SYNC_FREQUENCIES.map((x) => (
                  <option key={x.value} value={x.value}>{x.label}</option>
                ))}
              </StyledSelect>
            </div>

            <IconInput icon={null} label="Data scope"
              placeholder="orders, customers, inventory"
              value={form.dataScope} onChange={(e) => set("dataScope", e.target.value)} />

            {/* Status */}
            <div>
              <label className="mb-2 block text-sm font-medium text-text-primary">Status</label>
              <div className="flex gap-5">
                {["active", "inactive"].map((s) => (
                  <label key={s} className="flex cursor-pointer items-center gap-2">
                    <input type="radio" name="status" value={s}
                      checked={form.status === s} onChange={() => set("status", s)}
                      className="h-4 w-4 border-border-light text-primary focus:ring-[#6941c6]" />
                    <span className="text-sm font-medium capitalize text-text-primary">{s}</span>
                  </label>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="border-t border-border" />

        {/* Authentication */}
        <div className="grid grid-cols-[200px_1fr] gap-8 px-8 py-6">
          <SectionLabel label="Authentication" required />
          <div className="space-y-4">
            {/* Auth type radio */}
            <div>
              <label className="mb-2 block text-sm font-medium text-text-primary">Auth type</label>
              <div className="flex flex-wrap gap-5">
                {AUTH_OPTIONS.map((opt) => (
                  <label key={opt.value} className="flex cursor-pointer items-center gap-2">
                    <input type="radio" name="authType" value={opt.value}
                      checked={form.authType === opt.value} onChange={() => set("authType", opt.value)}
                      className="h-4 w-4 border-border-light text-primary focus:ring-[#6941c6]" />
                    <span className="text-sm font-medium text-text-primary">{opt.label}</span>
                  </label>
                ))}
              </div>
            </div>

            {form.authType === "oauth2" && (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <IconInput icon={Key}  label="Client ID"     value={form.clientId}     onChange={(e) => set("clientId",     e.target.value)} />
                  <IconInput icon={Lock} label="Client Secret" value={form.clientSecret} onChange={(e) => set("clientSecret", e.target.value)} type="password" />
                </div>
                <IconInput icon={Globe} label="Authorization URL" value={form.authUrl}     onChange={(e) => set("authUrl",     e.target.value)} placeholder="https://…/oauth/authorize" />
                <IconInput icon={Globe} label="Token URL"         value={form.tokenUrl}    onChange={(e) => set("tokenUrl",    e.target.value)} placeholder="https://…/oauth/token" />
                <IconInput icon={Globe} label="Redirect URI"      value={form.redirectUri} onChange={(e) => set("redirectUri", e.target.value)} />
                <IconInput icon={null}  label="Scopes"            value={form.scopes}      onChange={(e) => set("scopes",      e.target.value)} placeholder="read_orders, write_inventory" />
                <button type="button"
                  onClick={() => window.alert("Demo: OAuth consent would open.")}
                  className="inline-flex items-center gap-2 rounded-lg border border-border-light px-4 py-2 text-sm font-semibold text-text-primary hover:bg-surface-secondary">
                  Connect via OAuth
                </button>
              </>
            )}
            {form.authType === "api_key" && (
              <>
                <div className="grid grid-cols-2 gap-3">
                  <IconInput icon={Key}  label="API Key"    type="password" value={form.apiKey}    onChange={(e) => set("apiKey",    e.target.value)} placeholder="sk-…" />
                  <IconInput icon={Lock} label="API Secret" type="password" value={form.apiSecret} onChange={(e) => set("apiSecret", e.target.value)} placeholder="Optional" />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <IconInput icon={Globe} label="Base URL"    value={form.baseUrl}    onChange={(e) => set("baseUrl",    e.target.value)} placeholder="https://api.example.com" />
                  <IconInput icon={null}  label="API version" value={form.apiVersion} onChange={(e) => set("apiVersion", e.target.value)} placeholder="v1" />
                </div>
              </>
            )}
            {form.authType === "basic" && (
              <div className="grid grid-cols-2 gap-3">
                <IconInput icon={Mail}  label="Username" value={form.basicUser}     onChange={(e) => set("basicUser",     e.target.value)} />
                <IconInput icon={Lock}  label="Password" value={form.basicPassword} onChange={(e) => set("basicPassword", e.target.value)} type="password" />
              </div>
            )}
            {form.authType === "webhook" && (
              <>
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-text-primary">
                    Webhook URL <span className="text-xs text-text-muted">(read-only)</span>
                  </label>
                  <div className="flex gap-2">
                    <input readOnly value={WEBHOOK_URL}
                      className="flex-1 rounded-lg border border-border-light bg-surface-tertiary px-3 py-2.5 text-sm text-text-secondary" />
                    <button type="button"
                      onClick={() => navigator.clipboard?.writeText(WEBHOOK_URL)}
                      className="rounded-lg border border-border-light px-3 py-2 text-sm font-medium text-text-primary hover:bg-surface-secondary">
                      Copy
                    </button>
                  </div>
                </div>
                <IconInput icon={Key}  label="Webhook secret" value={form.webhookSecret} onChange={(e) => set("webhookSecret", e.target.value)} type="password" />
                <IconInput icon={null} label="Event types"    value={form.webhookEvents} onChange={(e) => set("webhookEvents", e.target.value)} placeholder="order.created, inventory.updated" />
              </>
            )}
          </div>
        </div>

        {/* Test connection panel */}
        <div className="mx-6 mb-6 flex items-center justify-between gap-4 rounded-xl bg-surface-tertiary px-5 py-4">
          <div className="flex items-center gap-3">
            <Unplug size={22} className="shrink-0 text-text-secondary" />
            <div>
              <p className="font-semibold text-text-primary">Test your connection</p>
              <p className="text-xs text-text-secondary">Verify your credentials are correct</p>
            </div>
          </div>
          <button type="button" onClick={onTestConnection}
            className="shrink-0 rounded-lg border border-border-light bg-surface px-4 py-2 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary">
            Test Connection
          </button>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between py-2">
        <button type="button" onClick={() => setForm(DEFAULTS)}
          className="text-sm font-medium text-text-secondary hover:text-text-primary">
          Reset to default
        </button>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => navigate("/configure/external")}
            className="rounded-lg border border-border-light px-4 py-2 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary">
            Cancel
          </button>
          <button type="button" onClick={onSave} disabled={!canSave}
            className={`flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors ${
              canSave
                ? "bg-primary text-white hover:bg-primary-hover"
                : "cursor-default bg-surface-tertiary text-text-muted"
            }`}>
            <CheckCircle size={15} />
            {isNew ? "Create Integration" : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
