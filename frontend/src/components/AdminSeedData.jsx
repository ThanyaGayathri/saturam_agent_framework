import { useEffect, useMemo, useState } from "react";
import { Eye, EyeOff, Search, ShieldCheck } from "lucide-react";
import { useDemoProfile } from "../context/DemoProfileContext";
import { loadKpiRegistry } from "../storage/kpi-registry";
import { loadProcesses } from "../storage/processes";
import { loadUseCaseCatalog, getDefaultCatalogEntries } from "../storage/use-cases-catalog";
import { useHiddenSeedRows } from "../hooks/useHiddenSeedRows";
import { kpiSeedId } from "../domain/seed-ids";
import { useBusinessAreas } from "../hooks/useBusinessAreas";
import { usePrototypeAuth } from "../context/PrototypeAuthContext";
import { fetchDashboardList } from "../services/dashboards-api";

/**
 * Admin surface for seed visibility.
 *
 * Not linked from the main nav — reachable via the profile menu ("Manage
 * seed data") or by knowing the URL. Any admin here can hide a seed KPI /
 * process / use case for the whole team; the hide list is stored in
 * hidden_seed_rows and consumed by the three domain hooks.
 *
 * Reads seed data from the storage-layer loaders directly, so this page
 * sees ALL seeds regardless of the hide filter (which normal screens run).
 */

const KIND_LABEL = {
  kpi: "KPIs",
  process: "Processes",
  use_case: "Use Cases",
};

function makeBusinessAreaLabel(areas) {
  return (id) => areas.find((b) => b.id === id)?.label || id || "—";
}

function ToggleButton({ hidden, disabled, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
        hidden
          ? "border-status-warning-border bg-status-warning-light text-status-warning hover:brightness-95"
          : "border-border bg-surface text-text-secondary hover:border-border-light hover:bg-surface-secondary"
      } ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
    >
      {hidden ? (
        <>
          <EyeOff size={13} strokeWidth={2} aria-hidden />
          Hidden — click to show
        </>
      ) : (
        <>
          <Eye size={13} strokeWidth={2} aria-hidden />
          Visible — click to hide
        </>
      )}
    </button>
  );
}

function SectionHeader({ title, total, hiddenCount }) {
  return (
    <div className="mb-3 flex items-baseline justify-between">
      <div className="flex items-baseline gap-2">
        <h2 className="text-base font-semibold text-text-primary">{title}</h2>
        <span className="text-xs text-text-muted">
          {total} total · {hiddenCount} hidden
        </span>
      </div>
    </div>
  );
}

function SearchBox({ value, onChange, placeholder }) {
  return (
    <div className="relative mb-3">
      <Search
        size={14}
        strokeWidth={2}
        className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
        aria-hidden
      />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full max-w-md rounded-lg border border-border bg-surface py-2 pl-8 pr-3 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
      />
    </div>
  );
}

function KpiTable({ kpis, isHidden, onToggle, supabaseEnabled, businessAreaLabel }) {
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const n = q.trim().toLowerCase();
    if (!n) return kpis;
    return kpis.filter(
      (k) =>
        k.name.toLowerCase().includes(n) ||
        (k.category || "").toLowerCase().includes(n) ||
        (k.subjectArea || "").toLowerCase().includes(n) ||
        businessAreaLabel(k.businessAreaId).toLowerCase().includes(n)
    );
  }, [kpis, q]);
  return (
    <div>
      <SearchBox value={q} onChange={setQ} placeholder="Search KPI name, category, subject area…" />
      <div className="overflow-hidden rounded-xl border border-border">
        <table className="w-full text-left text-sm">
          <thead className="bg-surface-secondary text-xs uppercase tracking-wide text-text-muted">
            <tr>
              <th className="px-4 py-2.5 font-semibold">Name</th>
              <th className="px-4 py-2.5 font-semibold">Category</th>
              <th className="px-4 py-2.5 font-semibold">Business Area</th>
              <th className="px-4 py-2.5 font-semibold">Subject Area</th>
              <th className="px-4 py-2.5 font-semibold" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-text-muted">
                  No matching KPIs.
                </td>
              </tr>
            ) : (
              filtered.map((k) => {
                const seedId = kpiSeedId(k.category, k.subjectArea, k.name);
                const hidden = isHidden("kpi", seedId);
                return (
                  <tr key={seedId} className={hidden ? "bg-status-warning-light/30" : ""}>
                    <td className="px-4 py-2.5 font-medium text-text-primary">{k.name}</td>
                    <td className="px-4 py-2.5 text-text-secondary">{k.category}</td>
                    <td className="px-4 py-2.5 text-text-secondary">
                      {businessAreaLabel(k.businessAreaId)}
                    </td>
                    <td className="px-4 py-2.5 text-text-secondary">{k.subjectArea}</td>
                    <td className="px-4 py-2.5 text-right">
                      <ToggleButton
                        hidden={hidden}
                        disabled={!supabaseEnabled}
                        onClick={() =>
                          hidden
                            ? onToggle.show("kpi", seedId)
                            : onToggle.hide("kpi", seedId)
                        }
                      />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ProcessTable({ processes, isHidden, onToggle, supabaseEnabled, businessAreaLabel }) {
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const n = q.trim().toLowerCase();
    if (!n) return processes;
    return processes.filter(
      (p) =>
        (p.name || "").toLowerCase().includes(n) ||
        businessAreaLabel(p.businessAreaId).toLowerCase().includes(n) ||
        (p.subjectArea || "").toLowerCase().includes(n)
    );
  }, [processes, q]);
  return (
    <div>
      <SearchBox value={q} onChange={setQ} placeholder="Search process name, business area…" />
      <div className="overflow-hidden rounded-xl border border-border">
        <table className="w-full text-left text-sm">
          <thead className="bg-surface-secondary text-xs uppercase tracking-wide text-text-muted">
            <tr>
              <th className="px-4 py-2.5 font-semibold">Name</th>
              <th className="px-4 py-2.5 font-semibold">Business Area</th>
              <th className="px-4 py-2.5 font-semibold">Subject Area</th>
              <th className="px-4 py-2.5 font-semibold" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-text-muted">
                  No matching processes.
                </td>
              </tr>
            ) : (
              filtered.map((p) => {
                const hidden = isHidden("process", p.id);
                return (
                  <tr key={p.id} className={hidden ? "bg-status-warning-light/30" : ""}>
                    <td className="px-4 py-2.5 font-medium text-text-primary">{p.name}</td>
                    <td className="px-4 py-2.5 text-text-secondary">
                      {businessAreaLabel(p.businessAreaId)}
                    </td>
                    <td className="px-4 py-2.5 text-text-secondary">{p.subjectArea || "—"}</td>
                    <td className="px-4 py-2.5 text-right">
                      <ToggleButton
                        hidden={hidden}
                        disabled={!supabaseEnabled}
                        onClick={() =>
                          hidden
                            ? onToggle.show("process", p.id)
                            : onToggle.hide("process", p.id)
                        }
                      />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function UseCaseTable({ useCases, isHidden, onToggle, supabaseEnabled }) {
  const [q, setQ] = useState("");
  const [tierFilter, setTierFilter] = useState("all");

  // Build the list of tiers we actually see in the current data so the
  // dropdown never offers an empty option.
  const availableTiers = useMemo(() => {
    const s = new Set();
    for (const u of useCases) {
      const t = (u.tier || "").toLowerCase().trim();
      if (t) s.add(t);
    }
    return Array.from(s).sort();
  }, [useCases]);

  const filtered = useMemo(() => {
    const n = q.trim().toLowerCase();
    return useCases.filter((u) => {
      const tier = (u.tier || "").toLowerCase().trim();
      if (tierFilter !== "all" && tier !== tierFilter) return false;
      if (!n) return true;
      return (
        (u.title || "").toLowerCase().includes(n) ||
        (u.businessArea || "").toLowerCase().includes(n) ||
        (u.actionType || "").toLowerCase().includes(n) ||
        tier.includes(n)
      );
    });
  }, [useCases, q, tierFilter]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <SearchBox value={q} onChange={setQ} placeholder="Search use case title, business area…" />
        </div>
        <label className="flex items-center gap-2 text-sm text-text-secondary">
          <span className="font-medium text-text-primary">Tier</span>
          <select
            value={tierFilter}
            onChange={(e) => setTierFilter(e.target.value)}
            className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          >
            <option value="all">All tiers</option>
            {availableTiers.map((t) => (
              <option key={t} value={t}>
                {t.charAt(0).toUpperCase() + t.slice(1)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="overflow-hidden rounded-xl border border-border">
        <table className="w-full text-left text-sm">
          <thead className="bg-surface-secondary text-xs uppercase tracking-wide text-text-muted">
            <tr>
              <th className="px-4 py-2.5 font-semibold">Title</th>
              <th className="px-4 py-2.5 font-semibold">Business Area</th>
              <th className="px-4 py-2.5 font-semibold">Action</th>
              <th className="px-4 py-2.5 font-semibold">Tier</th>
              <th className="px-4 py-2.5 font-semibold" />
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-text-muted">
                  No matching use cases.
                </td>
              </tr>
            ) : (
              filtered.map((u) => {
                const hidden = isHidden("use_case", u.id);
                return (
                  <tr key={u.id} className={hidden ? "bg-status-warning-light/30" : ""}>
                    <td className="px-4 py-2.5 font-medium text-text-primary">{u.title}</td>
                    <td className="px-4 py-2.5 text-text-secondary">{u.businessArea || "—"}</td>
                    <td className="px-4 py-2.5 text-text-secondary">{u.actionType || "—"}</td>
                    <td className="px-4 py-2.5 text-text-secondary">{u.tier || "—"}</td>
                    <td className="px-4 py-2.5 text-right">
                      <ToggleButton
                        hidden={hidden}
                        disabled={!supabaseEnabled}
                        onClick={() =>
                          hidden
                            ? onToggle.show("use_case", u.id)
                            : onToggle.hide("use_case", u.id)
                        }
                      />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Flatten the nested KPI registry into a single list keyed by (category, section, name).
function flattenKpiSeeds(kpiRegistry) {
  const out = [];
  for (const cat of kpiRegistry?.categories || []) {
    if (cat.category === "Custom KPIs") continue; // Supabase-backed, not seed
    for (const sec of cat.sections || []) {
      for (const k of sec.kpis || []) {
        if (k.__supabaseId) continue; // custom, not seed
        out.push({
          name: k.name,
          category: cat.category,
          subjectArea: sec.name,
          businessAreaId: k.businessAreaId || sec.businessAreaId,
        });
      }
    }
  }
  return out;
}

function seedProcesses(loaded) {
  return (loaded?.processes || []).filter((p) => !p.__supabaseId);
}

function seedUseCases(profileId, storageKey) {
  const cat = loadUseCaseCatalog(storageKey, profileId);
  const entries = Array.isArray(cat?.entries) && cat.entries.length > 0
    ? cat.entries
    : getDefaultCatalogEntries(profileId);
  return entries.filter((e) => {
    if (e.__supabaseId) return false;
    return !e.source || e.source === "seed";
  });
}

// Proves the async fetch pattern end to end against the real API (issue #230/A2) - not a
// production screen. `POST`/`PUT`/`DELETE` dashboard wiring is issue #233/D2, once the tile
// shape has been aligned (issue #226/D1).
//
// Fetches only on explicit click, never on mount: this is a debug panel on an admin page,
// not something every visitor should trigger a live backend call for. That also sidesteps
// `React.StrictMode` (see `main.jsx`) double-invoking a mount effect in dev - there's no
// mount effect here to double-invoke. A future accessor that copies this pattern for
// something non-idempotent (a POST/PUT) still needs its own guard against that double-fire;
// GET is idempotent so it isn't a concern for this one.
function LiveBackendCheck() {
  const [state, setState] = useState({ status: "idle" });

  useEffect(() => {
    if (state.status !== "loading") return undefined;
    const controller = new AbortController();
    fetchDashboardList({ signal: controller.signal })
      .then((dashboards) => setState({ status: "ready", dashboards }))
      .catch((error) => {
        if (error?.name === "AbortError") return;
        setState({ status: "error", error });
      });
    return () => controller.abort();
  }, [state.status]);

  return (
    <div className="mb-6 rounded-lg border border-border bg-surface-secondary p-3 text-sm">
      <div className="mb-2 flex items-center justify-between gap-3">
        <div className="font-semibold text-text-primary">
          Live backend check (<code className="font-mono text-xs">GET /dashboards</code>)
        </div>
        {state.status !== "loading" && (
          <button
            type="button"
            onClick={() => setState({ status: "loading" })}
            className="inline-flex items-center rounded-lg border border-border bg-surface px-3 py-1 text-xs font-semibold text-text-secondary transition hover:border-border-light hover:bg-surface-secondary"
          >
            {state.status === "idle" ? "Check backend" : "Recheck"}
          </button>
        )}
      </div>
      {state.status === "idle" && <p className="text-text-muted">Not checked yet.</p>}
      {state.status === "loading" && <p className="text-text-muted">Loading…</p>}
      {state.status === "error" && (
        <p className="text-status-critical">
          {state.error?.message || "Could not reach the retail API."}
        </p>
      )}
      {state.status === "ready" && (
        <p className="text-text-secondary">
          {state.dashboards.length === 0
            ? "Connected - no dashboards yet."
            : `Connected - ${state.dashboards.length} dashboard(s): ${state.dashboards
                .map((d) => `${d.name} (${d.owner})`)
                .join(", ")}`}
        </p>
      )}
    </div>
  );
}

const TAB_BASE = "relative inline-flex items-center gap-2 pb-3 pt-2 text-base font-semibold transition-colors";
const TAB_ACTIVE = "text-primary";
const TAB_IDLE = "text-text-muted hover:text-text-secondary";
const TAB_UNDERLINE_ACTIVE = "bg-primary";
const TAB_UNDERLINE_IDLE = "bg-transparent";
const TAB_COUNT_BASE = "rounded-full px-2 py-0.5 text-[11px] font-medium";
const TAB_COUNT_ACTIVE = "bg-primary-light text-primary";
const TAB_COUNT_IDLE = "bg-surface-secondary text-text-muted";

export default function AdminSeedData() {
  const { profileId, storageKeys } = useDemoProfile();
  const { user } = usePrototypeAuth();
  const { isHidden, countsByKind, hide, show, supabaseEnabled, hydrated } = useHiddenSeedRows();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();
  const businessAreaLabel = useMemo(() => makeBusinessAreaLabel(BUSINESS_AREAS), [BUSINESS_AREAS]);
  const [tab, setTab] = useState("kpi");

  const kpis = useMemo(() => {
    const loaded = loadKpiRegistry(storageKeys.KPI_REGISTRY, profileId);
    return flattenKpiSeeds(loaded);
  }, [storageKeys.KPI_REGISTRY, profileId]);

  const processes = useMemo(() => {
    const loaded = loadProcesses(storageKeys.PROCESSES, profileId);
    return seedProcesses(loaded);
  }, [storageKeys.PROCESSES, profileId]);

  const useCases = useMemo(
    () => seedUseCases(profileId, storageKeys.USE_CASE_CATALOG),
    [profileId, storageKeys.USE_CASE_CATALOG]
  );

  const authorLabel = user?.email || user?.name || null;
  const onToggle = useMemo(
    () => ({
      hide: (kind, id) => hide(kind, id, authorLabel),
      show: (kind, id) => show(kind, id),
    }),
    [hide, show, authorLabel]
  );

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-10 lg:py-10">
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="mb-1.5 inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary-light/60 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-primary">
            <ShieldCheck size={13} strokeWidth={2} aria-hidden />
            Admin
          </div>
          <h1 className="text-2xl font-semibold text-text-primary">Manage seed data</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Hide seed KPIs, processes, and use cases from the team's demo views.
            Hidden rows are stored in Supabase and disappear for every user within
            a few seconds. Toggle a row back to show to restore it.
          </p>
        </div>
      </div>

      <LiveBackendCheck />

      {!supabaseEnabled && (
        <div className="mb-6 rounded-lg border border-status-warning-border bg-status-warning-light/50 p-3 text-sm text-status-warning">
          Supabase isn't configured in this environment — hide toggles are disabled.
          Set <span className="font-mono">VITE_SUPABASE_URL</span> and{" "}
          <span className="font-mono">VITE_SUPABASE_ANON_KEY</span> to enable.
        </div>
      )}

      {/* Tabs */}
      <div className="mb-6 flex items-center gap-6 border-b border-border" role="tablist">
        {[
          { id: "kpi", label: "KPIs", count: kpis.length, hidden: countsByKind.kpi },
          { id: "process", label: "Processes", count: processes.length, hidden: countsByKind.process },
          { id: "use_case", label: "Use Cases", count: useCases.length, hidden: countsByKind.use_case },
        ].map((t) => {
          const active = tab === t.id;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`${TAB_BASE} ${active ? TAB_ACTIVE : TAB_IDLE}`}
            >
              {t.label}
              <span className={`${TAB_COUNT_BASE} ${active ? TAB_COUNT_ACTIVE : TAB_COUNT_IDLE}`}>
                {hydrated ? `${t.count} · ${t.hidden} hidden` : `${t.count}`}
              </span>
              <span
                aria-hidden
                className={`absolute inset-x-0 -bottom-px h-0.5 rounded-full transition-colors ${
                  active ? TAB_UNDERLINE_ACTIVE : TAB_UNDERLINE_IDLE
                }`}
              />
            </button>
          );
        })}
      </div>

      {/* Content */}
      {tab === "kpi" && (
        <>
          <SectionHeader
            title={`Seed KPIs (${KIND_LABEL.kpi})`}
            total={kpis.length}
            hiddenCount={countsByKind.kpi}
          />
          <KpiTable
            kpis={kpis}
            isHidden={isHidden}
            onToggle={onToggle}
            supabaseEnabled={supabaseEnabled}
            businessAreaLabel={businessAreaLabel}
          />
        </>
      )}
      {tab === "process" && (
        <>
          <SectionHeader
            title={`Seed Processes (${KIND_LABEL.process})`}
            total={processes.length}
            hiddenCount={countsByKind.process}
          />
          <ProcessTable
            processes={processes}
            isHidden={isHidden}
            onToggle={onToggle}
            supabaseEnabled={supabaseEnabled}
            businessAreaLabel={businessAreaLabel}
          />
        </>
      )}
      {tab === "use_case" && (
        <>
          <SectionHeader
            title={`Seed Use Cases (${KIND_LABEL.use_case})`}
            total={useCases.length}
            hiddenCount={countsByKind.use_case}
          />
          <UseCaseTable
            useCases={useCases}
            isHidden={isHidden}
            onToggle={onToggle}
            supabaseEnabled={supabaseEnabled}
          />
        </>
      )}
    </div>
  );
}
