import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, CircleHelp, Database, Server, Mail, Lock, Unplug, CheckCircle } from "lucide-react";
import { Modal } from "../ui";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import {
 INTERNAL_SOURCE_TYPES,
 SSL_MODES,
} from "../../domain/configure-catalogs";
import { normalizeInternal } from "../../storage/configure-workspace";
import postgresSvg from "../../assets/postgres.svg";
import snowflakeSvg from "../../assets/snowflake.svg";
import sapSvg from "../../assets/sap.svg";
import salesforceSvg from "../../assets/salesforce.svg";
import airflowSvg from "../../assets/airflow.svg";
import awsGlueSvg from "../../assets/aws-glue.svg";
import azureAdfSvg from "../../assets/azure-data-factory.svg";
import netSuiteSvg from "../../assets/netsuite.svg";

const SOURCE_ICON = {
 postgres: postgresSvg,
 snowflake: snowflakeSvg,
 sap_s4hana: sapSvg,
 salesforce: salesforceSvg,
 airflow: airflowSvg,
 aws_glue: awsGlueSvg,
 mssql: azureAdfSvg,
 dynamics365: azureAdfSvg,
 netsuite: netSuiteSvg,
};

const EMPTY = {
 sourceType: "postgres",
 name: "",
 host: "",
 port: "",
 databaseName: "",
 schema: "",
 authMethod: "username_password",
 username: "",
 password: "",
 apiKey: "",
 sslMode: "require",
 permissionLevel: "read_only",
};

const AUTH_OPTIONS = [
 { value: "username_password", label: "Username & Password" },
 { value: "api_key", label: "Private Key" },
 { value: "oauth", label: "OAuth" },
];

/* Styled label for form sections */
function SectionLabel({ label, required }) {
 return (
 <div className="flex items-center gap-1.5 pt-1">
 <span className="text-sm font-semibold text-text-primary">{label}</span>
 {required && <span className="text-[#d92d20]">*</span>}
 <CircleHelp size={14} className="shrink-0 text-text-muted" />
 </div>
 );
}

/* Input with left icon */
function IconInput({ icon: Icon, label, required, type = "text", value, onChange, placeholder }) {
 return (
 <div>
 {label && (
 <label className="mb-1.5 block text-sm font-medium text-text-primary">
 {label}
 {required && <span className="ml-0.5 text-[#d92d20]">*</span>}
 </label>
 )}
 <div className="relative">
 <Icon size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
 <input
 type={type}
 value={value}
 onChange={onChange}
 placeholder={placeholder}
 className="w-full rounded-lg border border-border-light py-2.5 pl-9 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6]"
 />
 </div>
 </div>
 );
}

/* Styled native select */
function StyledSelect({ label, required, value, onChange, children }) {
 return (
 <div>
 {label && (
 <label className="mb-1.5 block text-sm font-medium text-text-primary">
 {label}
 {required && <span className="ml-0.5 text-[#d92d20]">*</span>}
 </label>
 )}
 <select
 value={value}
 onChange={onChange}
 className="w-full rounded-lg border border-border-light py-2.5 pl-3 pr-8 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6]"
 >
 {children}
 </select>
 </div>
 );
}

export default function ConfigureInternalSourceForm() {
 const { id } = useParams();
 const navigate = useNavigate();
 const isNew = !id || id === "new";
 const { data, hydrated, upsertInternal } = useConfigureWorkspace();

 const [form, setForm] = useState(EMPTY);
 const [rwModalOpen, setRwModalOpen] = useState(false);

 const existing = useMemo(() => {
 if (isNew) return null;
 return (data.internalSources || []).find((x) => x.id === decodeURIComponent(id));
 }, [data.internalSources, id, isNew]);

 useEffect(() => {
 if (!hydrated) return;
 if (isNew) { setForm(EMPTY); return; }
 if (existing) {
 setForm({
 sourceType: existing.sourceType,
 name: existing.name,
 host: existing.host,
 port: existing.port,
 databaseName: existing.databaseName,
 schema: existing.schema,
 authMethod: existing.authMethod,
 username: existing.username,
 password: existing.password,
 apiKey: existing.apiKey,
 sslMode: existing.sslMode,
 permissionLevel: existing.permissionLevel,
 });
 }
 }, [hydrated, isNew, existing]);

 if (!hydrated) return <p className="text-sm text-text-secondary">Loading…</p>;
 if (!isNew && !existing) {
 return (
 <div className="space-y-4">
 <p className="text-sm text-text-secondary">Source not found.</p>
 <button
 type="button"
 onClick={() => navigate("/configure/internal-sources")}
 className="text-sm font-medium text-primary hover:underline"
 >
 ← Back
 </button>
 </div>
 );
 }

 const setField = (k, v) => setForm((f) => ({ ...f, [k]: v }));

 const onSave = () => {
 // Saving an edit -- even to a seeded placeholder -- means someone has taken ownership of
 // this row with real values, so it stops being a "seed" the Data Sources grid hides.
 // Without this, a seeded row edited with real credentials would stay permanently excluded
 // from its own listing page with no way back through the UI.
 const base = isNew ? { seed: false } : { id: existing.id, seed: false };
 const record = normalizeInternal({ ...base, ...form });
 upsertInternal(record);
 navigate("/configure/internal-sources");
 };

 const onTestConnection = () => {
 window.alert("Demo only: connection test would run here. Marked as connected for this session.");
 };

 const permissionToggle = (nextRw) => {
 if (nextRw) setRwModalOpen(true);
 else setField("permissionLevel", "read_only");
 };

 const confirmRw = () => {
 setForm((f) => ({ ...f, permissionLevel: "read_write" }));
 setRwModalOpen(false);
 };

 const typeIcon = SOURCE_ICON[form.sourceType];
 const canSave = form.name.trim() && form.host.trim();

 return (
 <div className="space-y-5">
 {/* Back link */}
 <button
 type="button"
 onClick={() => navigate("/configure/internal-sources")}
 className="flex items-center gap-1.5 text-sm font-semibold text-primary hover:text-[#7f56d9]"
 >
 <ArrowLeft size={16} />
 Back to configure
 </button>

 {/* Main card */}
 <div className="rounded-2xl border border-border bg-surface">
 {/* Card header */}
 <div className="px-8 py-6">
 <h2 className="text-xl font-bold text-text-primary">
 {isNew ? "New database Connection" : "Edit data source"}
 </h2>
 <p className="mt-1 text-sm text-text-secondary">
 Configure connection details, authentication, and schema settings.
 </p>
 </div>

 <div className="border-t border-border" />

 {/* Connection Details section */}
 <div className="grid grid-cols-[200px_1fr] gap-8 px-8 py-6">
 <SectionLabel label="Connection Details" required />
 <div className="space-y-4">
 {/* Source type with icon */}
 <div>
 <label className="mb-1.5 block text-sm font-medium text-text-primary">
 Datasource Type <span className="text-[#d92d20]">*</span>
 </label>
 <div className="flex items-center gap-2">
 <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-border bg-surface p-1.5">
 {typeIcon ? (
 <img src={typeIcon} alt="" className="h-full w-full object-contain" />
 ) : (
 <Database size={18} className="text-primary" />
 )}
 </div>
 <div className="relative flex-1">
 <select
 value={form.sourceType}
 onChange={(e) => setField("sourceType", e.target.value)}
 className="w-full appearance-none rounded-lg border border-border-light py-2.5 pl-3 pr-8 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6]"
 >
 {INTERNAL_SOURCE_TYPES.map((t) => (
 <option key={t.id} value={t.id}>{t.label}</option>
 ))}
 </select>
 <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-muted">▾</span>
 </div>
 </div>
 </div>

 <IconInput
 icon={Database}
 label="Datasource Name"
 required
 placeholder="e.g. Production DB"
 value={form.name}
 onChange={(e) => setField("name", e.target.value)}
 />
 <IconInput
 icon={Server}
 label="Host"
 required
 placeholder="localhost"
 value={form.host}
 onChange={(e) => setField("host", e.target.value)}
 />
 <IconInput
 icon={Database}
 label="Database"
 required
 placeholder="postgres"
 value={form.databaseName}
 onChange={(e) => setField("databaseName", e.target.value)}
 />

 {/* Advanced: port, schema, SSL - collapsed visually in a subtle row */}
 <div className="grid grid-cols-3 gap-3">
 <div>
 <label className="mb-1.5 block text-sm font-medium text-text-primary">Port</label>
 <input
 type="text"
 placeholder="5432"
 value={form.port}
 onChange={(e) => setField("port", e.target.value)}
 className="w-full rounded-lg border border-border-light px-3 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6]"
 />
 </div>
 <div>
 <label className="mb-1.5 block text-sm font-medium text-text-primary">Schema</label>
 <input
 type="text"
 placeholder="public"
 value={form.schema}
 onChange={(e) => setField("schema", e.target.value)}
 className="w-full rounded-lg border border-border-light px-3 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-1 focus:ring-[#6941c6]"
 />
 </div>
 <StyledSelect
 label="SSL Mode"
 value={form.sslMode}
 onChange={(e) => setField("sslMode", e.target.value)}
 >
 {SSL_MODES.map((s) => (
 <option key={s.value} value={s.value}>{s.label}</option>
 ))}
 </StyledSelect>
 </div>
 </div>
 </div>

 <div className="border-t border-border" />

 {/* Authentication Method section */}
 <div className="grid grid-cols-[200px_1fr] gap-8 px-8 py-6">
 <SectionLabel label="Authentication Method" required />
 <div className="space-y-4">
 {/* Radio buttons */}
 <div className="flex flex-wrap gap-5">
 {AUTH_OPTIONS.map((opt) => (
 <label key={opt.value} className="flex cursor-pointer items-center gap-2">
 <input
 type="radio"
 name="authMethod"
 value={opt.value}
 checked={form.authMethod === opt.value}
 onChange={() => setField("authMethod", opt.value)}
 className="h-4 w-4 border-border-light text-primary focus:ring-[#6941c6]"
 />
 <span className="text-sm font-medium text-text-primary">{opt.label}</span>
 </label>
 ))}
 </div>

 {form.authMethod === "username_password" && (
 <div className="grid grid-cols-2 gap-3">
 <IconInput
 icon={Mail}
 label="Username"
 required
 placeholder="Enter your email"
 value={form.username}
 onChange={(e) => setField("username", e.target.value)}
 />
 <IconInput
 icon={Lock}
 label="Password"
 required
 type="password"
 placeholder="••••••••"
 value={form.password}
 onChange={(e) => setField("password", e.target.value)}
 />
 </div>
 )}
 {form.authMethod === "api_key" && (
 <IconInput
 icon={Lock}
 label="API Key / Private Key"
 required
 type="password"
 placeholder="sk-…"
 value={form.apiKey}
 onChange={(e) => setField("apiKey", e.target.value)}
 />
 )}
 {form.authMethod === "oauth" && (
 <p className="text-sm text-text-secondary">
 Use API key or username and password if connecting outside your organisation's OAuth flow.
 </p>
 )}

 {/* Permission level */}
 <div className="pt-1">
 <p className="mb-2 text-sm font-medium text-text-primary">Permission level</p>
 <div className="flex gap-2">
 <button
 type="button"
 onClick={() => permissionToggle(false)}
 className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
 form.permissionLevel === "read_only"
 ? "border-primary bg-primary-light dark:bg-primary/20 text-primary"
 : "border-border-light text-text-secondary hover:bg-surface-secondary"
 }`}
 >
 Read-Only
 </button>
 <button
 type="button"
 onClick={() => { if (form.permissionLevel !== "read_write") permissionToggle(true); }}
 className={`rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors ${
 form.permissionLevel === "read_write"
 ? "border-primary bg-primary-light dark:bg-primary/20 text-primary"
 : "border-border-light text-text-secondary hover:bg-surface-secondary"
 }`}
 >
 Read-Write
 </button>
 </div>
 </div>
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
 <button
 type="button"
 onClick={onTestConnection}
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
 onClick={() => setForm(EMPTY)}
 className="text-sm font-medium text-text-secondary hover:text-text-primary"
 >
 Reset to default
 </button>
 <div className="flex items-center gap-3">
 <button
 type="button"
 onClick={() => navigate("/configure/internal-sources")}
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
 {isNew ? "Create Datasource" : "Save Changes"}
 </button>
 </div>
 </div>

 {/* Read-Write confirmation modal */}
 {rwModalOpen && (
 <Modal title="Enable read-write access?" onClose={() => setRwModalOpen(false)}>
 <p className="mb-4 text-sm text-text-secondary">
 Read-Write allows the AI to create and update records in this system, not only query them. Enable only if your
 organisation accepts this risk and credentials are scoped appropriately.
 </p>
 <div className="flex justify-end gap-2">
 <button
 type="button"
 onClick={() => setRwModalOpen(false)}
 className="rounded-lg border border-border-light px-4 py-2 text-sm font-semibold text-text-primary hover:bg-surface-secondary"
 >
 Cancel
 </button>
 <button
 type="button"
 onClick={confirmRw}
 className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-white hover:bg-primary-hover"
 >
 Enable Read-Write
 </button>
 </div>
 </Modal>
 )}
 </div>
 );
}
