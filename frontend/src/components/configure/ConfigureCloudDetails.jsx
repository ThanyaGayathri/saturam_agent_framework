import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Button } from "../ui";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import {
  CLOUD_PROVIDERS,
  DEPLOYMENT_ENVIRONMENTS,
} from "../../domain/configure-catalogs";

const PROVIDER_SHORT = {
  gcp: "GCP",
  aws: "AWS",
  azure: "Azure",
  on_premise: "On-premise",
  hybrid: "Hybrid",
};

const DEFAULT_SUMMARY_FALLBACK = "SaaS hosted in cloud provider of your choice.";

function Row({ label, value }) {
  return (
    <div className="border-b border-border-light py-3 last:border-0">
      <p className="text-xs font-semibold uppercase tracking-wide text-text-muted">{label}</p>
      <p className="mt-1 text-sm text-text-primary">{value || "-"}</p>
    </div>
  );
}

export default function ConfigureCloudDetails() {
  const navigate = useNavigate();
  const { data, hydrated } = useConfigureWorkspace();
  const cloud = data.cloudProfile || {};

  if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;

  const providerLabel =
    CLOUD_PROVIDERS.find((p) => p.value === cloud.provider)?.label ||
    PROVIDER_SHORT[cloud.provider] ||
    cloud.provider ||
    "-";
  const envLabel =
    DEPLOYMENT_ENVIRONMENTS.find((e) => e.value === cloud.environment)?.label || cloud.environment || "-";
  const summary =
    cloud.summaryPublic?.trim() ||
    [providerLabel, cloud.region, envLabel].filter(Boolean).join(", ");
  const credentialsState =
    cloud.credentials && String(cloud.credentials).trim().length > 0 ? "Configured (stored locally in this browser)" : "Not set";

  return (
    <div className="space-y-6">
      <button
        type="button"
        onClick={() => navigate("/configure/internal-sources")}
        className="flex items-center gap-1 text-sm font-semibold text-primary hover:text-primary-hover"
      >
        <ArrowLeft size={18} />
        Back to Configure
      </button>

      <div>
        <h2 className="text-lg font-semibold text-text-primary">Cloud environment</h2>
        <p className="mt-1 text-sm text-text-secondary">
          Read-only summary. Cloud deployment is typically a one-time IT setup; use admin setup only when you need to change these values.
        </p>
      </div>

      <div className="max-w-2xl rounded-xl border border-border-light bg-surface p-6">
        <Row label="Summary" value={summary || DEFAULT_SUMMARY_FALLBACK} />
        <Row label="Cloud provider" value={providerLabel} />
        <Row label="Region" value={cloud.region || "-"} />
        <Row label="Environment" value={envLabel} />
        <Row label="Credentials" value={credentialsState} />
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" type="button" onClick={() => navigate("/configure/internal-sources")}>
          Close
        </Button>
        <Link
          to="/configure/cloud-setup"
          className="inline-flex items-center justify-center rounded-lg border border-border-light bg-surface px-4 py-2 text-sm font-semibold text-primary transition-colors hover:bg-surface-secondary"
        >
          Edit in admin setup (IT)
        </Link>
      </div>
    </div>
  );
}
