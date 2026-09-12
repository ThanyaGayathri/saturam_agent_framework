import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Button, Input, Select } from "../ui";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import {
  CLOUD_PROVIDERS,
  CLOUD_REGIONS_BY_PROVIDER,
  DEPLOYMENT_ENVIRONMENTS,
} from "../../domain/configure-catalogs";

const PROVIDER_SHORT = {
  gcp: "GCP",
  aws: "AWS",
  azure: "Azure",
  on_premise: "On-premise",
  hybrid: "Hybrid",
};

export default function ConfigureCloudSetup() {
  const navigate = useNavigate();
  const { data, hydrated, setCloudProfile } = useConfigureWorkspace();
  const cloud = data.cloudProfile || {};

  const [provider, setProvider] = useState("gcp");
  const [region, setRegion] = useState("us-central1");
  const [credentials, setCredentials] = useState("");
  const [environment, setEnvironment] = useState("production");
  const [summaryPublic, setSummaryPublic] = useState("");

  useEffect(() => {
    if (!hydrated) return;
    setProvider(cloud.provider || "gcp");
    setRegion(cloud.region || CLOUD_REGIONS_BY_PROVIDER.gcp[0]);
    setCredentials(cloud.credentials || "");
    setEnvironment(cloud.environment || "production");
    setSummaryPublic(cloud.summaryPublic || "");
  }, [hydrated, cloud]);

  const regionOptions = (CLOUD_REGIONS_BY_PROVIDER[provider] || []).map((r) => ({ value: r, label: r }));

  useEffect(() => {
    if (regionOptions.length && !regionOptions.some((o) => o.value === region)) {
      setRegion(regionOptions[0].value);
    }
  }, [provider]);

  if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;

  const onSave = () => {
    const envLabel = DEPLOYMENT_ENVIRONMENTS.find((e) => e.value === environment)?.label || environment;
    const autoSummary =
      summaryPublic.trim() ||
      `${PROVIDER_SHORT[provider] || provider}, ${region}, ${envLabel}`;
    setCloudProfile({
      provider,
      region,
      credentials,
      environment,
      summaryPublic: autoSummary,
    });
    navigate("/configure/cloud");
  };

  return (
    <div className="space-y-6">
      <button
        type="button"
        onClick={() => navigate("/configure/cloud")}
        className="flex items-center gap-1 text-sm font-semibold text-primary hover:text-primary-hover"
      >
        <ArrowLeft size={18} />
        Back to cloud details
      </button>

      <div>
        <h2 className="text-lg font-semibold text-text-primary">Cloud / admin setup</h2>
        <p className="mt-1 text-sm text-text-secondary">
          One-time deployment context (SaaS customers can leave defaults). No need to choose GKE, EKS, or other internal services - the platform maps provider and environment for you.
        </p>
      </div>

      <div className="grid max-w-2xl grid-cols-1 gap-4 rounded-xl border border-border-light bg-surface p-6">
        <Select
          label="Cloud provider"
          options={CLOUD_PROVIDERS.map((p) => ({ value: p.value, label: p.label }))}
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
        />
        <Select
          label="Region"
          options={regionOptions.length ? regionOptions : [{ value: region, label: region }]}
          value={region}
          onChange={(e) => setRegion(e.target.value)}
        />
        <Select
          label="Environment"
          options={DEPLOYMENT_ENVIRONMENTS.map((x) => ({ value: x.value, label: x.label }))}
          value={environment}
          onChange={(e) => setEnvironment(e.target.value)}
        />
        <div>
          <label className="mb-1.5 block text-sm font-medium text-text-primary">Credentials</label>
          <p className="mb-2 text-xs text-text-secondary">
            Service account JSON (GCP), IAM role ARN notes (AWS), or service principal details (Azure). Stored only in your browser for this prototype.
          </p>
          <textarea
            className="min-h-[120px] w-full rounded-lg border border-border-light bg-surface px-3 py-2 text-sm outline-none focus:border-primary focus:ring-1 focus:ring-primary"
            value={credentials}
            onChange={(e) => setCredentials(e.target.value)}
            placeholder="Paste or describe stored credentials reference…"
          />
        </div>
        <Input
          label="Display summary (optional)"
          placeholder="Shown at bottom of Configure, e.g. SaaS hosted in cloud provider of your choice."
          value={summaryPublic}
          onChange={(e) => setSummaryPublic(e.target.value)}
        />
      </div>

      <div className="flex gap-3">
        <Button variant="secondary" type="button" onClick={() => navigate("/configure/cloud")}>
          Cancel
        </Button>
        <Button type="button" onClick={onSave}>
          Save
        </Button>
      </div>
    </div>
  );
}
