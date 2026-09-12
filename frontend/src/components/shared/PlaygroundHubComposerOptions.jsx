// Shared "Ask anything" toolbar: attach, data sources, connectors, mic, send. Matches Playground home hub.
import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Paperclip,
  Database,
  LayoutGrid,
  Plus,
  ExternalLink,
} from "lucide-react";
import { Button, Input, Select, SlidePanel, Switch } from "../ui";
import { MicButton } from "./MicButton";
import { PaperPlaneSendIcon } from "../icons/PaperPlaneSendIcon";
import snowflakeSvg from "../../assets/snowflake.svg";
import airflowSvg from "../../assets/airflow.svg";
import awsGlueSvg from "../../assets/aws-glue.svg";
import adfSvg from "../../assets/azure-data-factory.svg";
import salesforceSvg from "../../assets/salesforce.svg";
import sapSvg from "../../assets/sap.svg";
import netsuiteSvg from "../../assets/netsuite.svg";

const DATABASES = [
  { name: "Snowflake", img: snowflakeSvg },
  { name: "Airflow", img: airflowSvg },
  { name: "Amazon Glue", img: awsGlueSvg },
  { name: "Azure Data Factory", img: adfSvg },
];

const CONNECTOR_ITEMS = [
  { id: "salesforce", name: "Salesforce", img: salesforceSvg },
  { id: "sap", name: "SAP", img: sapSvg },
  { id: "netsuite", name: "Oracle NetSuite", img: netsuiteSvg },
];

const DATASOURCE_TYPES = [
  { value: "snowflake", label: "Snowflake" },
  { value: "bigquery", label: "BigQuery" },
  { value: "redshift", label: "Redshift" },
  { value: "postgres", label: "PostgreSQL" },
  { value: "mysql", label: "MySQL" },
];

function ConnectorToggle({ enabled, onChange, name }) {
  return (
    <Switch
      checked={enabled}
      onCheckedChange={onChange}
      size="comfortable"
      stopClickPropagation
      aria-label={enabled ? `Disable connection for ${name}` : `Enable connection for ${name}`}
    />
  );
}

export default function PlaygroundHubComposerOptions({
  attachments,
  dictation,
  onSend,
  actionsRowClassName = "mt-2",
  sendButtonTitle = "Send",
}) {
  const navigate = useNavigate();
  const [showDbDropdown, setShowDbDropdown] = useState(false);
  const [showDbPanel, setShowDbPanel] = useState(false);
  const [showConnDropdown, setShowConnDropdown] = useState(false);
  const [connectorEnabled, setConnectorEnabled] = useState({
    salesforce: true,
    sap: false,
    netsuite: false,
  });
  const [authMethod, setAuthMethod] = useState("password");
  const dbDropdownRef = useRef(null);
  const connDropdownRef = useRef(null);

  useEffect(() => {
    function handleClick(e) {
      if (dbDropdownRef.current && !dbDropdownRef.current.contains(e.target)) {
        setShowDbDropdown(false);
      }
      if (connDropdownRef.current && !connDropdownRef.current.contains(e.target)) {
        setShowConnDropdown(false);
      }
    }
    if (showDbDropdown || showConnDropdown) {
      document.addEventListener("mousedown", handleClick);
      return () => document.removeEventListener("mousedown", handleClick);
    }
  }, [showDbDropdown, showConnDropdown]);

  return (
    <>
      <div className={`flex items-center justify-between ${actionsRowClassName}`}>
        <div className="relative flex items-center gap-2">
          <label
            className="flex cursor-pointer items-center rounded p-1.5 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
            aria-label="Attach files"
            title="Attach files"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                attachments.openPicker();
              }
            }}
          >
            <input ref={attachments.inputRef} {...attachments.fileInputProps} />
            <Paperclip size={18} className="pointer-events-none" />
          </label>

          <div ref={dbDropdownRef} className="relative">
            <button
              type="button"
              onClick={() => {
                setShowConnDropdown(false);
                setShowDbDropdown((v) => !v);
              }}
              className={`rounded p-1.5 transition hover:bg-surface-secondary ${
                showDbDropdown ? "bg-surface-tertiary text-primary" : "text-text-muted hover:text-primary"
              }`}
              title="Database"
            >
              <Database size={18} />
            </button>

            {showDbDropdown && (
              <div className="absolute bottom-full left-0 z-30 mb-2 w-[280px] rounded-xl border border-border bg-surface p-3 shadow-xl">
                <p className="mb-2 px-1 text-sm font-semibold text-text-primary">Database</p>
                <div className="space-y-1">
                  {DATABASES.map((db) => (
                    <div
                      key={db.name}
                      className="flex items-center justify-between rounded-lg px-2 py-2 hover:bg-surface-secondary"
                    >
                      <div className="flex items-center gap-3">
                        <img src={db.img} alt={db.name} className="h-6 w-6" />
                        <span className="text-sm text-text-primary">{db.name}</span>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          setShowDbDropdown(false);
                          setShowDbPanel(true);
                        }}
                        className="text-sm font-semibold text-primary hover:text-primary-hover"
                      >
                        Configure
                      </button>
                    </div>
                  ))}
                </div>
                <div className="my-2 h-px bg-border" />
                <button
                  type="button"
                  onClick={() => {
                    setShowDbDropdown(false);
                    setShowDbPanel(true);
                  }}
                  className="flex w-full items-center justify-between rounded-lg px-2 py-2.5 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
                >
                  <span className="flex items-center gap-2">
                    <Plus size={16} className="text-text-muted" />
                    Add New Database
                  </span>
                  <ExternalLink size={16} className="shrink-0 text-text-muted" aria-hidden />
                </button>
              </div>
            )}
          </div>

          <div ref={connDropdownRef} className="relative">
            <button
              type="button"
              onClick={() => {
                setShowDbDropdown(false);
                setShowConnDropdown((v) => !v);
              }}
              className={`flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-sm transition ${
                showConnDropdown
                  ? "border-primary-light bg-surface-tertiary text-primary"
                  : "border-border-light bg-surface-secondary text-text-muted hover:border-border hover:text-text-tertiary"
              }`}
              title="Connectors"
            >
              <LayoutGrid size={16} />
              <span className="font-medium tabular-nums">1 of 4</span>
            </button>

            {showConnDropdown && (
              <div className="absolute bottom-full left-0 z-30 mb-2 w-[300px] rounded-xl border border-border bg-surface p-3 shadow-xl">
                <p className="mb-2 px-1 text-sm font-semibold text-text-primary">Connectors</p>
                <div className="space-y-0.5">
                  {CONNECTOR_ITEMS.map((c) => (
                    <div
                      key={c.id}
                      className="flex items-center justify-between rounded-lg px-2 py-2.5 hover:bg-surface-secondary"
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <img src={c.img} alt="" className="h-7 w-7 shrink-0 object-contain" />
                        <span className="truncate text-sm text-text-primary">{c.name}</span>
                      </div>
                      <ConnectorToggle
                        name={c.name}
                        enabled={connectorEnabled[c.id]}
                        onChange={(next) => setConnectorEnabled((prev) => ({ ...prev, [c.id]: next }))}
                      />
                    </div>
                  ))}
                </div>
                <div className="my-2 h-px bg-border" />
                <button
                  type="button"
                  onClick={() => {
                    setShowConnDropdown(false);
                    navigate("/configure/external");
                  }}
                  className="flex w-full items-center justify-between rounded-lg px-2 py-2.5 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
                >
                  <span className="flex items-center gap-2">
                    <Plus size={16} className="text-text-muted" />
                    Manage Connectors
                  </span>
                  <ExternalLink size={16} className="shrink-0 text-text-muted" aria-hidden />
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <MicButton
            supported={dictation.supported}
            listening={dictation.listening}
            onToggle={dictation.toggle}
          />
          <button
            type="button"
            onClick={onSend}
            className="rounded p-1.5 text-text-muted transition hover:bg-surface-secondary hover:text-primary"
            title={sendButtonTitle}
            aria-label={sendButtonTitle}
          >
            <PaperPlaneSendIcon size={18} />
          </button>
        </div>
      </div>

      {showDbPanel && (
        <SlidePanel
          title="New database Connection"
          subtitle="Configure connection details, authentication, and schema settings."
          onClose={() => setShowDbPanel(false)}
          footer={
            <>
              <Button variant="secondary" onClick={() => setShowDbPanel(false)}>Cancel</Button>
              <Button onClick={() => setShowDbPanel(false)}>Confirm</Button>
            </>
          }
        >
          <div className="space-y-5">
            <h3 className="text-sm font-semibold text-text-primary">Connection Details</h3>
            <Select label="Datasource Type" required options={DATASOURCE_TYPES} />
            <Input label="Datasource Name" required placeholder="e.g. Production DB" />
            <Input label="Host" required placeholder="localhost" />
            <Input label="Database" required placeholder="postgres" />

            <div className="h-px bg-border" />

            <h3 className="text-sm font-semibold text-text-primary">Authentication Method</h3>
            <div className="flex items-center gap-6">
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="radio"
                  name="playground-db-auth"
                  value="password"
                  checked={authMethod === "password"}
                  onChange={() => setAuthMethod("password")}
                  className="h-4 w-4 text-primary"
                />
                <span className="text-sm text-text-primary">Username & Password</span>
              </label>
              <label className="flex cursor-pointer items-center gap-2">
                <input
                  type="radio"
                  name="playground-db-auth"
                  value="key"
                  checked={authMethod === "key"}
                  onChange={() => setAuthMethod("key")}
                  className="h-4 w-4 text-primary"
                />
                <span className="text-sm text-text-primary">Private Key</span>
              </label>
            </div>

            {authMethod === "password" ? (
              <>
                <Input label="Username" required placeholder="Enter your email" />
                <Input label="Password" required placeholder="••••••••" type="password" />
              </>
            ) : (
              <Input label="Private Key Path" required placeholder="/path/to/key.pem" />
            )}
          </div>
        </SlidePanel>
      )}
    </>
  );
}
