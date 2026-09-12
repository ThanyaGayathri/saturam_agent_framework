import { useCallback, useMemo, useRef, useState } from "react";
import { CheckCircle, Download, FileText, UploadCloud, XCircle } from "lucide-react";
import { SlidePanel } from "./ui";
import { useKpiRegistry } from "../hooks/useKpiRegistry";
import { useBusinessAreas } from "../hooks/useBusinessAreas";

/**
 * Import KPIs from a CSV file. New adds go through the same
 * addCustomKpi path as the Add KPI panel — writes to Supabase when
 * enabled, falls back to localStorage otherwise. See docs/pendingItems.md.
 *
 * Template columns (all human-readable):
 *   name, description, businessArea, subjectArea, level, unit, industryBenchmark
 *
 * Required: name, businessArea, subjectArea.
 * businessArea accepts either the display label ("Store Ops") or the internal
 * id ("store-ops") — both map to the same registry entry.
 */

// Validation set (order-independent).
const REQUIRED_COLUMNS = ["name", "businessArea", "subjectArea"];
const OPTIONAL_COLUMNS = ["description", "level", "unit", "industryBenchmark"];
// Display order in the downloaded template — reads left-to-right the way
// a human writes a spec: what is it, what does it do, where does it live.
const ALL_COLUMNS = [
  "name",
  "description",
  "businessArea",
  "subjectArea",
  "level",
  "unit",
  "industryBenchmark",
];

// Map a user-supplied businessArea cell (either display label like "Store Ops"
// or internal id like "store-ops") to the internal id. Case-insensitive.
function resolveBusinessAreaId(raw, areas) {
  const v = String(raw || "").trim().toLowerCase();
  if (!v) return "";
  const hit = areas.find(
    (b) => b.id.toLowerCase() === v || b.label.toLowerCase() === v
  );
  return hit ? hit.id : v; // fall through to raw so warning fires
}

function labelForBusinessArea(id, areas) {
  const hit = areas.find((b) => b.id === id);
  return hit ? hit.label : id;
}

function subjectAreasFor(businessAreaId, areas) {
  const hit = areas.find((b) => b.id === businessAreaId);
  return hit?.subjectAreas || [];
}

const KNOWN_LEVELS = ["Executive", "Operational", "Tactical"];
const KNOWN_UNITS = ["Count", "Percentage", "Currency", "Time"];

// Minimal CSV parser: handles double-quoted fields and CRLF/LF line endings.
// Not RFC 4180 exhaustive — enough for a demo template.
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  const src = text.replace(/\r\n?/g, "\n");
  for (let i = 0; i < src.length; i++) {
    const ch = src[i];
    if (inQuotes) {
      if (ch === '"') {
        if (src[i + 1] === '"') { field += '"'; i++; }
        else { inQuotes = false; }
      } else {
        field += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(field);
      field = "";
    } else if (ch === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += ch;
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter((r) => r.length > 1 || (r[0] && r[0].trim().length > 0));
}

// Normalize header names so "Business Area ID" == "businessAreaId" == "business_area_id".
function normalizeHeader(h) {
  return String(h || "")
    .replace(/[\s_-]+/g, "")
    .toLowerCase();
}

function matchColumn(header, targetKey) {
  return normalizeHeader(header) === normalizeHeader(targetKey);
}

function rowsToRecords(rows, areas) {
  if (rows.length === 0) return { records: [], errors: ["Empty file"] };
  const headers = rows[0].map((h) => (h || "").trim());
  const idxByCol = {};
  for (const col of ALL_COLUMNS) {
    idxByCol[col] = headers.findIndex((h) => matchColumn(h, col));
  }
  const missing = REQUIRED_COLUMNS.filter((c) => idxByCol[c] === -1);
  if (missing.length) {
    return { records: [], errors: [`Missing required column(s): ${missing.join(", ")}`] };
  }
  const validBusinessAreaIds = new Set(areas.map((b) => b.id));
  const records = [];
  const warnings = [];
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    const cell = (col) => (idxByCol[col] === -1 ? "" : (row[idxByCol[col]] || "").trim());
    const name = cell("name");
    if (!name) continue; // skip blank rows silently
    const rawBusinessArea = cell("businessArea");
    const subjectArea = cell("subjectArea");
    if (!rawBusinessArea || !subjectArea) {
      warnings.push(`Row ${r + 1} "${name}": missing businessArea or subjectArea — skipped`);
      continue;
    }
    const businessAreaId = resolveBusinessAreaId(rawBusinessArea, areas);
    if (!validBusinessAreaIds.has(businessAreaId)) {
      warnings.push(
        `Row ${r + 1} "${name}": unknown businessArea "${rawBusinessArea}" — imported anyway`
      );
    }
    // subjectArea: warn if it isn't one of the standard subjects under that
    // business area. Import anyway — sales-friendly, not blocking.
    const knownSubjects = subjectAreasFor(businessAreaId, areas);
    if (
      knownSubjects.length &&
      !knownSubjects.some((s) => s.toLowerCase() === subjectArea.toLowerCase())
    ) {
      const baLabel = labelForBusinessArea(businessAreaId, areas) || rawBusinessArea;
      warnings.push(
        `Row ${r + 1} "${name}": "${subjectArea}" isn't a standard subject area for ${baLabel} — imported anyway`
      );
    }
    // level: warn if non-standard, coerce to Operational so the row still has
    // a valid value in the registry.
    const rawLevel = cell("level");
    let level = "Operational";
    if (rawLevel) {
      if (KNOWN_LEVELS.includes(rawLevel)) {
        level = rawLevel;
      } else {
        warnings.push(
          `Row ${r + 1} "${name}": level "${rawLevel}" isn't standard (Executive / Operational / Tactical) — set to Operational`
        );
      }
    }
    // unit: warn if non-standard, but keep the user-typed value so the row
    // still surfaces however they intended.
    const rawUnit = cell("unit");
    let unit = "Count";
    if (rawUnit) {
      if (!KNOWN_UNITS.includes(rawUnit)) {
        warnings.push(
          `Row ${r + 1} "${name}": unit "${rawUnit}" isn't standard (Count / Percentage / Currency / Time) — imported as-is`
        );
      }
      unit = rawUnit;
    }
    records.push({
      name,
      desc: cell("description"),
      businessAreaId,
      subjectArea,
      level,
      unit,
      industryBenchmark: cell("industryBenchmark"),
      datasetIds: [],
      processMapping: [],
      extras: { active: true },
    });
  }
  return { records, errors: [], warnings };
}

function buildTemplateCsv() {
  const header = ALL_COLUMNS.join(",");
  // Values are positioned to match ALL_COLUMNS order exactly:
  //   name, description, businessArea, subjectArea, level, unit, industryBenchmark
  const example1 = [
    "Weekly Same-Store Sales",
    "Growth vs last week for like-for-like stores",
    "Store Ops",
    "KPI Monitoring",
    "Operational",
    "Percentage",
    "3-6% healthy",
  ];
  const example2 = [
    "Stockout Rate",
    "Percent of SKUs out of stock across active stores",
    "Inventory",
    "Stock Health",
    "Operational",
    "Percentage",
    "Below 2%",
  ];
  const quote = (v) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  return [header, example1.map(quote).join(","), example2.map(quote).join(",")].join("\n");
}

function downloadBlob(filename, content, mime = "text/csv;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function ImportKpisFromCsvPanel({ open, onClose }) {
  const { addCustomKpi, setData, supabaseEnabled } = useKpiRegistry();
  const { areas: BUSINESS_AREAS } = useBusinessAreas();
  const inputRef = useRef(null);
  const [fileName, setFileName] = useState("");
  const [parsed, setParsed] = useState(null); // { records, errors, warnings } | null
  const [status, setStatus] = useState("idle"); // 'idle' | 'importing' | 'done' | 'error'
  const [result, setResult] = useState(null); // { imported, failed }

  const businessAreaHint = useMemo(
    () => BUSINESS_AREAS.map((b) => b.label).join(", "),
    [BUSINESS_AREAS]
  );

  const reset = useCallback(() => {
    setFileName("");
    setParsed(null);
    setStatus("idle");
    setResult(null);
    if (inputRef.current) inputRef.current.value = "";
  }, []);

  const handleFileChosen = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    try {
      const text = await file.text();
      const rows = parseCsv(text);
      const outcome = rowsToRecords(rows, BUSINESS_AREAS);
      setParsed(outcome);
      setStatus(outcome.errors.length ? "error" : "idle");
    } catch (err) {
      console.error(err);
      setParsed({ records: [], errors: [`Failed to read file: ${err.message || err}`], warnings: [] });
      setStatus("error");
    }
  };

  const handleImport = async () => {
    if (!parsed || parsed.errors.length || parsed.records.length === 0) return;
    setStatus("importing");
    let imported = 0;
    let failed = 0;
    for (const rec of parsed.records) {
      try {
        if (supabaseEnabled) {
          const r = await addCustomKpi(rec);
          if (r?.ok) imported++;
          else failed++;
        } else {
          // Local fallback: append to the "Custom KPIs" category so users
          // still see the imported rows without Supabase configured.
          setData((prev) => {
            const next = JSON.parse(JSON.stringify(prev));
            let cat = (next.categories || []).find((c) => c.category === "Custom KPIs");
            if (!cat) {
              cat = { category: "Custom KPIs", sections: [] };
              next.categories = [...(next.categories || []), cat];
            }
            const secKey = `${rec.businessAreaId}\0${rec.subjectArea}`;
            let sec = cat.sections.find(
              (s) => `${s.businessAreaId || ""}\0${s.name || ""}` === secKey
            );
            if (!sec) {
              sec = { name: rec.subjectArea, businessAreaId: rec.businessAreaId, kpis: [] };
              cat.sections.push(sec);
            }
            sec.kpis.push({
              name: rec.name,
              desc: rec.desc,
              businessAreaId: rec.businessAreaId,
              subjectArea: rec.subjectArea,
              level: rec.level,
              unit: rec.unit,
              industryBenchmark: rec.industryBenchmark,
              active: true,
            });
            return next;
          });
          imported++;
        }
      } catch (err) {
        console.warn("[ImportKpisFromCsvPanel] row failed", err);
        failed++;
      }
    }
    setResult({ imported, failed });
    setStatus("done");
  };

  if (!open) return null;

  const headerIcon = (
    <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-border-light bg-primary-light text-primary">
      <UploadCloud size={20} strokeWidth={1.75} aria-hidden />
    </div>
  );

  const canImport =
    parsed && parsed.records.length > 0 && parsed.errors.length === 0 && status !== "importing";

  const footer = (
    <>
      <button
        type="button"
        onClick={onClose}
        className="rounded-lg border border-border bg-surface px-4 py-2.5 text-sm font-semibold text-text-tertiary transition hover:bg-surface-secondary"
      >
        Close
      </button>
      {status === "done" ? (
        <button
          type="button"
          onClick={reset}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-primary-hover"
        >
          Import another
        </button>
      ) : (
        <button
          type="button"
          onClick={handleImport}
          disabled={!canImport}
          className={`inline-flex items-center gap-1.5 rounded-lg px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors ${
            canImport ? "bg-primary hover:bg-primary-hover" : "cursor-not-allowed bg-primary/40"
          }`}
        >
          <UploadCloud size={16} strokeWidth={2} aria-hidden />
          {status === "importing"
            ? "Importing…"
            : `Import ${parsed?.records?.length || 0} KPI${parsed?.records?.length === 1 ? "" : "s"}`}
        </button>
      )}
    </>
  );

  return (
    <SlidePanel
      title="Import KPIs from CSV"
      subtitle="Upload a CSV to add multiple KPIs at once. Follows the same shape as Add KPI."
      onClose={onClose}
      icon={headerIcon}
      widthClass="max-w-[520px]"
      footer={footer}
    >
      <div className="space-y-5">
        {/* Template */}
        <div className="rounded-xl border border-primary/20 bg-primary-light/40 p-4">
          <div className="flex items-start gap-3">
            <FileText size={20} className="mt-0.5 shrink-0 text-primary" strokeWidth={1.75} aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-text-primary">Follow the template</p>
              <p className="mt-1 text-xs leading-relaxed text-text-secondary">
                Required columns: <span className="font-mono text-text-primary">name</span>,{" "}
                <span className="font-mono text-text-primary">businessArea</span>,{" "}
                <span className="font-mono text-text-primary">subjectArea</span>. Optional:{" "}
                <span className="font-mono text-text-primary">description</span>,{" "}
                <span className="font-mono text-text-primary">level</span>,{" "}
                <span className="font-mono text-text-primary">unit</span>,{" "}
                <span className="font-mono text-text-primary">industryBenchmark</span>.
              </p>
              <p className="mt-2 text-[11px] leading-relaxed text-text-muted">
                Valid businessArea values: {businessAreaHint}
              </p>
              <button
                type="button"
                onClick={() => downloadBlob("kpi-import-template.csv", buildTemplateCsv())}
                className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-primary/40 bg-surface px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary-light"
              >
                <Download size={13} strokeWidth={2} aria-hidden />
                Download template
              </button>
            </div>
          </div>
        </div>

        {/* File picker */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-text-primary">Choose a CSV file</label>
          <div className="flex items-center gap-2">
            <input
              ref={inputRef}
              type="file"
              accept=".csv,text/csv"
              onChange={handleFileChosen}
              className="block w-full text-sm text-text-secondary file:mr-3 file:rounded-lg file:border-0 file:bg-primary file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white hover:file:bg-primary-hover"
            />
          </div>
          {fileName && (
            <p className="mt-1.5 text-xs text-text-muted">
              Selected: <span className="font-mono">{fileName}</span>
            </p>
          )}
        </div>

        {/* Preview / feedback */}
        {parsed && (
          <div className="space-y-2">
            {parsed.errors.length > 0 ? (
              <div className="flex items-start gap-2 rounded-lg border border-status-danger/40 bg-status-danger-light/40 p-3">
                <XCircle size={18} className="mt-0.5 shrink-0 text-status-danger" strokeWidth={2} aria-hidden />
                <div className="min-w-0 text-sm">
                  <p className="font-semibold text-status-danger">Can't import this file</p>
                  <ul className="mt-1 list-disc space-y-0.5 pl-4 text-text-secondary">
                    {parsed.errors.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </div>
              </div>
            ) : (
              <div className="flex items-start gap-2 rounded-lg border border-primary/25 bg-primary-light/40 p-3">
                <CheckCircle size={18} className="mt-0.5 shrink-0 text-primary" strokeWidth={2} aria-hidden />
                <div className="min-w-0 text-sm">
                  <p className="font-semibold text-text-primary">
                    Ready to import {parsed.records.length} KPI{parsed.records.length === 1 ? "" : "s"}
                  </p>
                  {parsed.warnings?.length > 0 && (
                    <ul className="mt-1 list-disc space-y-0.5 pl-4 text-text-secondary">
                      {parsed.warnings.slice(0, 6).map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                      {parsed.warnings.length > 6 && (
                        <li>and {parsed.warnings.length - 6} more…</li>
                      )}
                    </ul>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Result */}
        {status === "done" && result && (
          <div className="flex items-start gap-2 rounded-lg border border-status-success/40 bg-status-success-light/40 p-3">
            <CheckCircle size={18} className="mt-0.5 shrink-0 text-status-success" strokeWidth={2} aria-hidden />
            <div className="min-w-0 text-sm">
              <p className="font-semibold text-text-primary">
                Imported {result.imported} KPI{result.imported === 1 ? "" : "s"}
                {result.failed > 0 && ` — ${result.failed} failed`}
              </p>
            </div>
          </div>
        )}
      </div>
    </SlidePanel>
  );
}
