// The Database and Connectors menus that sit under (or above) the ask box.
//
// There were three near-identical copies of this markup before, each with its own hardcoded
// list of four logos, its own `useState` toggles that reset on navigation, and one with a
// connector count typed in as "1 of 4" while the list held three. This is the single version:
// the databases come from Configure, the switches persist, and the count is counted.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Database, LayoutGrid, Plus, ExternalLink } from "lucide-react";
import { Switch } from "../ui";
import { SourceLogoTile } from "./SourceLogoTile";
import { useDemoProfile } from "../../context/DemoProfileContext";
import { loadConfigureWorkspace } from "../../storage/configure-workspace";
import { useSourcePrefs } from "../../context/SourcePrefsContext";
import { internalSourceTypeLabel } from "../../domain/configure-catalogs";
import salesforceSvg from "../../assets/salesforce.svg";
import sapSvg from "../../assets/sap.svg";
import netsuiteSvg from "../../assets/netsuite.svg";

/** Connectors are a fixed catalogue: these are the SaaS systems the demo can reach. */
const CONNECTOR_ITEMS = [
  { id: "salesforce", name: "Salesforce", category: "CRM", img: salesforceSvg, defaultOn: true },
  { id: "sap", name: "SAP", category: "ERP", img: sapSvg, defaultOn: false },
  { id: "netsuite", name: "Oracle NetSuite", category: "ERP", img: netsuiteSvg, defaultOn: false },
];

/** Panels open downward under a composer, or upward when the composer sits at the bottom. */
function panelPosition(direction) {
  return direction === "up" ? "bottom-full mb-2" : "top-full mt-2";
}

/**
 * A scrolling list that fades at its bottom edge, so a long list reads as "there is more"
 * rather than as a list that happens to end at the panel border.
 */
function ScrollFadeList({ children }) {
  return (
    <div className="relative">
      <div className="max-h-[320px] space-y-0.5 overflow-y-auto pr-0.5">{children}</div>
      <div className="pointer-events-none absolute inset-x-0 bottom-0 h-6 bg-gradient-to-t from-[var(--app-surface-default)] to-transparent" />
    </div>
  );
}

function MenuFooterLink({ label, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center justify-between rounded-lg px-2 py-2.5 text-sm font-semibold text-text-primary transition hover:bg-surface-secondary"
    >
      {/* The icon sits in a tile the same width as a row's logo, so the label starts on the
          same vertical line as the source names above it. */}
      <span className="flex min-w-0 items-center gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center">
          <Plus size={16} className="text-text-muted" />
        </span>
        <span className="truncate">{label}</span>
      </span>
      <ExternalLink size={16} className="shrink-0 text-text-muted" aria-hidden />
    </button>
  );
}

export default function ComposerSourceMenus({ direction = "down" }) {
  const navigate = useNavigate();
  // Read-only view of the Configure workspace: its provider is scoped to /configure, and these
  // menus live on the Playground and chat composers. Same approach as Monitor.
  const { storageKeys, profileId } = useDemoProfile();
  const [workspace, setWorkspace] = useState(null);
  useEffect(() => {
    setWorkspace(loadConfigureWorkspace(storageKeys.CONFIGURE_WORKSPACE, profileId));
  }, [storageKeys.CONFIGURE_WORKSPACE, profileId]);
  const {
    isDatabaseOn,
    isConnectorOn,
    setDatabaseOn,
    setConnectorOn,
  } = useSourcePrefs();

  // One menu at a time: `open` is "db", "connectors" or null rather than two booleans that
  // could both be true.
  const [open, setOpen] = useState(null);
  const wrapRef = useRef(null);

  // Seeded rows (`seed: true`) are static placeholders, not connections anyone made. Configure
  // excludes them from its grid and from every count it shows, so listing them here would have
  // the Playground claiming eight databases on a workspace Configure calls empty.
  const allDatabases = workspace?.internalSources || [];
  const databases = allDatabases.filter((s) => !s.seed);
  // With nothing connected, the menu offers the placeholders the workspace already carries,
  // each linking to its Configure form. That is the path that turns one real: saving it with
  // real credentials clears `seed`, and it moves into the list above with a switch of its own.
  const unconfigured = allDatabases.filter((s) => s.seed);

  // These rows read as product names ("Snowflake"), not connection names ("Snowflake -
  // RETAIL_DW"): nothing is connected yet, so the instance half says nothing useful. The full
  // name is kept whenever trimming it would leave two rows reading the same, which happens on
  // profiles with several connections to one system.
  const unconfiguredRows = useMemo(() => {
    const product = (name) => String(name || "").split(" - ")[0].trim() || name;
    const seen = {};
    for (const s of unconfigured) {
      const key = product(s.name);
      seen[key] = (seen[key] || 0) + 1;
    }
    return unconfigured.map((s) => ({
      source: s,
      label: seen[product(s.name)] > 1 ? s.name : product(s.name),
    }));
  }, [unconfigured]);
  const activeCount = databases.filter((s) => isDatabaseOn(s)).length;
  const connectorsOnCount = CONNECTOR_ITEMS.filter((c) => isConnectorOn(c)).length;

  const close = useCallback(() => setOpen(null), []);

  useEffect(() => {
    if (!open) return undefined;
    function onPointerDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) close();
    }
    function onKeyDown(e) {
      if (e.key === "Escape") close();
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, close]);

  return (
    <div ref={wrapRef} className="flex items-center gap-2">
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((v) => (v === "db" ? null : "db"))}
          className={`rounded p-1.5 transition hover:bg-surface-secondary ${
            open === "db" ? "bg-surface-tertiary text-primary" : "text-text-muted hover:text-primary"
          }`}
          title="Databases"
          aria-haspopup="menu"
          aria-expanded={open === "db"}
        >
          <Database size={18} />
        </button>

        {open === "db" && (
          <div
            className={`absolute left-0 z-50 w-[320px] rounded-xl border border-border bg-surface p-3 shadow-xl ${panelPosition(direction)}`}
            role="menu"
          >
            <div className="mb-2 flex items-baseline justify-between px-1">
              <p className="text-sm font-semibold text-text-primary">Databases</p>
              <p className="text-xs tabular-nums text-text-muted">
                {activeCount} of {databases.length} active
              </p>
            </div>

            {databases.length === 0 && unconfigured.length === 0 ? (
              <p className="px-2 py-3 text-sm text-text-secondary">
                No databases yet. Add one in Configure and it appears here.
              </p>
            ) : (
              <ScrollFadeList>
                {databases.map((db) => {
                  const failed = db.connectionStatus === "failed";
                  const on = isDatabaseOn(db);
                  return (
                    // The switch is the only control on the row, and the row hovers exactly
                    // like a Connectors row.
                    <div
                      key={db.id}
                      className={`flex w-full items-center justify-between gap-2 rounded-lg px-2 py-2 transition hover:bg-surface-secondary ${
                        failed ? "opacity-70" : ""
                      }`}
                    >
                      <span className="flex min-w-0 items-center gap-3">
                        <SourceLogoTile sourceType={db.sourceType} name={db.name} />
                        <span className="min-w-0">
                          <span className="block truncate text-sm text-text-primary">{db.name}</span>
                          <span
                            className={`block truncate text-xs ${
                              failed ? "font-medium text-status-critical" : "text-text-muted"
                            }`}
                          >
                            {failed ? "Connection failed" : internalSourceTypeLabel(db.sourceType)}
                          </span>
                        </span>
                      </span>
                      <Switch
                        checked={on}
                        disabled={failed}
                        onCheckedChange={(next) => setDatabaseOn(db, next)}
                        size="comfortable"
                        stopClickPropagation
                        aria-label={on ? `Switch off ${db.name}` : `Switch on ${db.name}`}
                      />
                    </div>
                  );
                })}

                {/* The rest of what the workspace holds: placeholders nobody has connected
                    yet. No switch, because there is nothing to read from them; the link is
                    what turns one into a real connection. */}
                {unconfiguredRows.length ? (
                  <>
                    {databases.length ? (
                      <p className="px-2 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
                        Not connected yet
                      </p>
                    ) : null}
                    {unconfiguredRows.map(({ source: db, label }) => (
                      <div
                        key={db.id}
                        className="flex items-center justify-between gap-3 rounded-lg px-2 py-2 transition hover:bg-surface-secondary"
                      >
                        <span className="flex min-w-0 items-center gap-3">
                          <SourceLogoTile sourceType={db.sourceType} name={label} />
                          <span className="truncate text-sm text-text-primary">{label}</span>
                        </span>
                        <button
                          type="button"
                          onClick={() => {
                            close();
                            navigate(`/configure/internal-sources/edit/${encodeURIComponent(db.id)}`);
                          }}
                          className="shrink-0 rounded text-sm font-semibold text-primary transition hover:text-primary-hover"
                        >
                          Configure
                        </button>
                      </div>
                    ))}
                  </>
                ) : null}
              </ScrollFadeList>
            )}

            <div className="my-2 h-px bg-border" />
            <MenuFooterLink
              label="Add New Database"
              onClick={() => {
                close();
                navigate("/configure/internal-sources/new");
              }}
            />
          </div>
        )}
      </div>

      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((v) => (v === "connectors" ? null : "connectors"))}
          className={`flex items-center gap-1.5 rounded-lg border px-2 py-1.5 text-sm transition ${
            open === "connectors"
              ? "border-primary-light bg-surface-tertiary text-primary"
              : "border-border-light bg-surface-secondary text-text-muted hover:border-border hover:text-text-tertiary"
          }`}
          title="Connectors"
          aria-haspopup="menu"
          aria-expanded={open === "connectors"}
        >
          <LayoutGrid size={16} />
          <span className="font-medium tabular-nums">
            {connectorsOnCount} of {CONNECTOR_ITEMS.length}
          </span>
        </button>

        {open === "connectors" && (
          <div
            className={`absolute left-0 z-50 w-[320px] rounded-xl border border-border bg-surface p-3 shadow-xl ${panelPosition(direction)}`}
            role="menu"
          >
            <div className="mb-2 flex items-baseline justify-between px-1">
              <p className="text-sm font-semibold text-text-primary">Connectors</p>
              <p className="text-xs tabular-nums text-text-muted">
                {connectorsOnCount} of {CONNECTOR_ITEMS.length} active
              </p>
            </div>
            <ScrollFadeList>
              {CONNECTOR_ITEMS.map((c) => {
                const on = isConnectorOn(c);
                return (
                  <div
                    key={c.id}
                    className="flex items-center justify-between gap-2 rounded-lg px-2 py-2 transition hover:bg-surface-secondary"
                  >
                    <span className="flex min-w-0 items-center gap-3">
                      <SourceLogoTile src={c.img} name={c.name} />
                      <span className="min-w-0">
                        <span className="block truncate text-sm text-text-primary">{c.name}</span>
                        <span className="block truncate text-xs text-text-muted">{c.category}</span>
                      </span>
                    </span>
                    <Switch
                      checked={on}
                      onCheckedChange={(next) => setConnectorOn(c, next)}
                      size="comfortable"
                      stopClickPropagation
                      aria-label={on ? `Switch off ${c.name}` : `Switch on ${c.name}`}
                    />
                  </div>
                );
              })}
            </ScrollFadeList>
            <div className="my-2 h-px bg-border" />
            <MenuFooterLink
              label="Manage Connectors"
              onClick={() => {
                close();
                navigate("/configure/external");
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}
