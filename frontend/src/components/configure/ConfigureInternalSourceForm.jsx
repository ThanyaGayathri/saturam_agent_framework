import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
 ArrowLeft,
 CircleHelp,
 Database,
 Server,
 Mail,
 Lock,
 Unplug,
 CheckCircle,
 CircleCheck,
 CircleX,
 Loader2,
} from "lucide-react";
import { Modal } from "../ui";
import { useConfigureWorkspace } from "../../context/ConfigureWorkspaceContext";
import {
 INTERNAL_SOURCE_TYPES,
 SSL_MODES,
} from "../../domain/configure-catalogs";
import { normalizeInternal } from "../../storage/configure-workspace";
import { API_SOURCE_TYPES, HARVESTABLE_SOURCE_TYPES } from "../../services/sources-api";
import postgresSvg from "../../assets/postgres.svg";
import snowflakeSvg from "../../assets/snowflake.svg";
import sapSvg from "../../assets/sap.svg";
import salesforceSvg from "../../assets/salesforce.svg";
import airflowSvg from "../../assets/airflow.svg";
import awsGlueSvg from "../../assets/aws-glue.svg";
import azureAdfSvg from "../../assets/azure-data-factory.svg";
import netSuiteSvg from "../../assets/netsuite.svg";

/**
 * Register, test and connect one database (#308).
 *
 * Everything here is a real call: Save is `POST /configure/sources` (or a PATCH of an existing
 * one), Test Connection is `POST /configure/sources/{id}/test` with the credentials the API
 * stored, and a saved Postgres/Snowflake source is handed to #306's harvest workflow on the way
 * out so the Data sources card shows it building. The API tests only a *saved* source -- there
 * is no "try these fields" route by contract, because a credential the product will act on
 * should be one it holds -- so testing from a new form saves first and says so.
 *
 * Errors are the backend's own `detail`, shown as written. A 422 from an incomplete credential
 * or a classified connection failure ("Authentication failed for user x") is more useful than
 * anything this form could invent.
 */

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

/** The picker offers what the API can register (`SourceType`), in the catalogue's own labels
 * and order, plus a plain "Other" for anything the catalogue does not name. */
const TYPE_OPTIONS = [
 ...INTERNAL_SOURCE_TYPES.filter((t) => API_SOURCE_TYPES.includes(t.id)),
 { id: "other", label: "Other" },
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
function IconInput({ icon: Icon, label, required, type = "text", value, onChange, placeholder, autoComplete }) {
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
 autoComplete={autoComplete}
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

function formFromSource(source) {
 return {
 sourceType: source.sourceType,
 name: source.name,
 host: source.host,
 port: source.port,
 databaseName: source.databaseName,
 schema: source.schema,
 authMethod: source.authMethod,
 username: source.username,
 // Stored secrets never come back from the API; a demo row's are blank anyway.
 password: "",
 apiKey: "",
 sslMode: source.sslMode,
 permissionLevel: source.permissionLevel,
 };
}

const errorText = (err, fallback) => err?.detail || err?.message || fallback;

export default function ConfigureInternalSourceForm() {
 const { id } = useParams();
 const navigate = useNavigate();
 const isNew = !id || id === "new";
 const { data, hydrated, upsertInternal, testInternalConnection, startInternalHarvest } = useConfigureWorkspace();

 const [form, setForm] = useState(EMPTY);
 const [rwModalOpen, setRwModalOpen] = useState(false);
 // One thing at a time: "saving" or "testing", else null.
 const [busy, setBusy] = useState(null);
 const [error, setError] = useState(null);
 const [notice, setNotice] = useState(null);
 // The last connection test: idle | testing | ok | failed, with the backend's own sentence.
 const [test, setTest] = useState({ state: "idle", detail: "", testedAt: null });

 const existing = useMemo(() => {
 if (isNew) return null;
 return (data.internalSources || []).find((x) => x.id === decodeURIComponent(id));
 }, [data.internalSources, id, isNew]);

 // A real source (the API's) vs a seeded demo row. Editing a demo row and saving registers a
 // real source; the demo row itself stays a fixture.
 const isLive = !!existing?.remote;

 // Load the form once per source id -- not on every context refresh. The harvest poll
 // re-reads the list every few seconds while something builds, and resetting the fields on
 // each tick would wipe whatever the person is typing.
 const loadedFor = useRef(null);
 useEffect(() => {
 if (!hydrated) return;
 if (isNew) {
 if (loadedFor.current !== "new") {
 loadedFor.current = "new";
 setForm(EMPTY);
 }
 return;
 }
 if (existing && loadedFor.current !== existing.id) {
 loadedFor.current = existing.id;
 setForm(formFromSource(existing));
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

 /** POST for a new form or a demo row; PATCH for a real source. Returns the saved source. */
 const save = async () => {
 const base = isLive ? { id: existing.id } : {};
 const record = { ...normalizeInternal({ ...base, ...form, seed: false }), remote: isLive };
 return upsertInternal(record);
 };

 const onSave = async () => {
 setBusy("saving");
 setError(null);
 setNotice(null);
 let saved;
 try {
 saved = await save();
 } catch (err) {
 setError(errorText(err, "Could not save the source."));
 setBusy(null);
 return;
 }
 // Connect = register, then model. Only for types #306's workflow has a connector for, and
 // only when there is something to (re)build -- a modelled source keeps its model on a
 // rename. A failure to *start* the run is shown here, on the saved source, not swallowed.
 const wantsHarvest =
 HARVESTABLE_SOURCE_TYPES.has(saved.sourceType) &&
 (!isLive || ["not_started", "error"].includes(saved.harvestState));
 if (wantsHarvest) {
 try {
 await startInternalHarvest(saved.id);
 } catch (err) {
 setBusy(null);
 setError(`Saved, but modelling could not start: ${errorText(err, "unknown error")}`);
 if (!isLive) navigate(`/configure/internal-sources/edit/${encodeURIComponent(saved.id)}`, { replace: true });
 return;
 }
 }
 setBusy(null);
 navigate("/configure/internal-sources");
 };

 /** Test uses the *stored* credentials, so a new form (or a demo row) is saved first and the
 * form switches to editing the saved source. Unsaved edits on a real source are saved too,
 * so what is tested is what is on screen. */
 const onTestConnection = async () => {
 setBusy("testing");
 setError(null);
 setNotice(null);
 setTest({ state: "testing", detail: "", testedAt: null });
 let sourceId = isLive ? existing.id : null;
 try {
 const saved = await save();
 if (!isLive) {
 sourceId = saved.id;
 setNotice("Saved first — the API tests the credentials it stores, not the ones on screen.");
 loadedFor.current = saved.id; // keep the typed values; do not reload the form from the API
 navigate(`/configure/internal-sources/edit/${encodeURIComponent(saved.id)}`, { replace: true });
 }
 // The secret is stored now; clear it from the screen so a later Save does not rotate it
 // to the same value and a stale field never lingers in the DOM.
 setForm((f) => ({ ...f, password: "", apiKey: "" }));
 } catch (err) {
 setTest({ state: "idle", detail: "", testedAt: null });
 setError(errorText(err, "Could not save the source before testing."));
 setBusy(null);
 return;
 }
 try {
 const result = await testInternalConnection(sourceId);
 setTest({ state: result?.ok ? "ok" : "failed", detail: result?.detail || "", testedAt: result?.tested_at || null });
 } catch (err) {
 setTest({ state: "idle", detail: "", testedAt: null });
 setError(errorText(err, "The connection test could not run."));
 } finally {
 setBusy(null);
 }
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
 const canSave = form.name.trim() && form.host.trim() && !busy;
 const harvestable = HARVESTABLE_SOURCE_TYPES.has(form.sourceType);

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

 {(error || notice) && (
 <div className="px-8 pb-4">
 {error && (
 <div role="alert" className="rounded-xl border border-status-critical-border bg-status-critical-light px-4 py-3 text-sm text-status-critical">
 {error}
 </div>
 )}
 {notice && !error && (
 <div className="rounded-xl border border-status-info-border bg-status-info-light px-4 py-3 text-sm text-status-info">
 {notice}
 </div>
 )}
 </div>
 )}

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
 {TYPE_OPTIONS.map((t) => (
 <option key={t.id} value={t.id}>{t.label}</option>
 ))}
 </select>
 <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-text-muted">▾</span>
 </div>
 </div>
 {!harvestable && (
 <p className="mt-1.5 text-xs text-text-muted">
 Registers and can be tested, but no connector models this type yet — it will show as "Not modelled".
 </p>
 )}
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
 placeholder="warehouse.internal"
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
 inputMode="numeric"
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
 placeholder="egpt_reader"
 autoComplete="off"
 value={form.username}
 onChange={(e) => setField("username", e.target.value)}
 />
 <IconInput
 icon={Lock}
 label="Password"
 required={!isLive}
 type="password"
 autoComplete="new-password"
 placeholder={isLive && existing.hasCredentials ? "Stored — enter a new one to replace it" : "••••••••"}
 value={form.password}
 onChange={(e) => setField("password", e.target.value)}
 />
 </div>
 )}
 {form.authMethod === "api_key" && (
 <IconInput
 icon={Lock}
 label="API Key / Private Key"
 required={!isLive}
 type="password"
 autoComplete="new-password"
 placeholder={isLive && existing.hasCredentials ? "Stored — enter a new one to replace it" : "sk-…"}
 value={form.apiKey}
 onChange={(e) => setField("apiKey", e.target.value)}
 />
 )}
 {form.authMethod === "oauth" && (
 <div className="space-y-3">
 <p className="text-sm text-text-secondary">
 Paste the access token your organisation's OAuth flow issued. The API stores it like any other
 secret; there is no browser redirect flow here.
 </p>
 <IconInput
 icon={Lock}
 label="Access token"
 required={!isLive}
 type="password"
 autoComplete="new-password"
 placeholder={isLive && existing.hasCredentials ? "Stored — enter a new one to replace it" : "token"}
 value={form.apiKey}
 onChange={(e) => setField("apiKey", e.target.value)}
 />
 </div>
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
 {form.permissionLevel === "read_write" && (
 <p className="mt-2 text-xs text-status-warning">
 A read-write source is registered but never modelled or exposed to a model; the harvest refuses it.
 </p>
 )}
 </div>
 </div>
 </div>

 {/* Test connection panel */}
 <div className="mx-6 mb-6 rounded-xl bg-surface-tertiary px-5 py-4">
 <div className="flex items-center justify-between gap-4">
 <div className="flex items-center gap-3">
 <Unplug size={22} className="shrink-0 text-text-secondary" />
 <div>
 <p className="font-semibold text-text-primary">Test your connection</p>
 <p className="text-xs text-text-secondary">
 {isLive
 ? "Tries the connection with the credentials the API has stored."
 : "Saves the source, then tries the connection with the stored credentials."}
 </p>
 </div>
 </div>
 <button
 type="button"
 onClick={onTestConnection}
 disabled={!!busy || !form.name.trim() || !form.host.trim()}
 className="flex shrink-0 items-center gap-2 rounded-lg border border-border-light bg-surface px-4 py-2 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary disabled:cursor-default disabled:opacity-60"
 >
 {busy === "testing" && <Loader2 size={14} className="animate-spin" />}
 {busy === "testing" ? "Testing…" : "Test Connection"}
 </button>
 </div>
 {test.state !== "idle" && test.state !== "testing" && (
 <div
 role="status"
 className={`mt-3 flex items-start gap-2 rounded-lg px-3 py-2.5 text-sm ${
 test.state === "ok"
 ? "bg-status-success-light text-status-success"
 : "bg-status-critical-light text-status-critical"
 }`}
 >
 {test.state === "ok" ? <CircleCheck size={16} className="mt-0.5 shrink-0" /> : <CircleX size={16} className="mt-0.5 shrink-0" />}
 <div>
 <p className="font-semibold">{test.state === "ok" ? "Connected" : "Connection failed"}</p>
 {test.detail && <p className="mt-0.5">{test.detail}</p>}
 </div>
 </div>
 )}
 </div>
 </div>

 {/* Footer */}
 <div className="flex items-center justify-between py-2">
 <button
 type="button"
 onClick={() => setForm(isLive ? formFromSource(existing) : EMPTY)}
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
 {busy === "saving" ? <Loader2 size={15} className="animate-spin" /> : <CheckCircle size={15} />}
 {busy === "saving" ? "Connecting…" : isLive ? "Save Changes" : "Create Datasource"}
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
