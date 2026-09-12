// Simple sortable data table. Columns come from the block spec so response
// generators control what shows without touching the component.

import { useMemo, useState } from "react";
import { ChevronUp, ChevronDown } from "lucide-react";
import BlockShell from "../BlockShell";

const NUMBER_FORMATS = new Set(["number", "percent", "currency"]);

function formatCell(value, format) {
  if (value == null || value === "") return " - ";
  if (format === "percent") {
    if (typeof value === "number") return `${Math.round(value * 100) / 100}%`;
    if (typeof value === "string" && !value.includes("%")) {
      const n = Number(value);
      if (Number.isFinite(n)) return `${n}%`;
    }
    return String(value);
  }
  if (format === "currency") {
    const n = typeof value === "number" ? value : Number(String(value).replace(/[^0-9.-]/g, ""));
    if (Number.isFinite(n)) return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(n);
    return String(value);
  }
  if (format === "number" && typeof value === "number") {
    return new Intl.NumberFormat().format(value);
  }
  return String(value);
}

function extractSortValue(row, col) {
  const raw = row[col.key];
  if (raw == null) return -Infinity;
  if (NUMBER_FORMATS.has(col.format) || typeof raw === "number") {
    const n = typeof raw === "number" ? raw : Number(String(raw).replace(/[^0-9.-]/g, ""));
    return Number.isFinite(n) ? n : -Infinity;
  }
  return String(raw).toLowerCase();
}

export default function TableBlock({ block, onPinToggle }) {
  const [sortKey, setSortKey] = useState(null);
  const [sortDir, setSortDir] = useState("desc");
  const columns = block.columns || [];
  const rows = block.rows || [];
  const maxRows = block.maxRows || 12;

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const col = columns.find((c) => c.key === sortKey);
    if (!col) return rows;
    const copy = [...rows];
    copy.sort((a, b) => {
      const va = extractSortValue(a, col);
      const vb = extractSortValue(b, col);
      if (va < vb) return sortDir === "asc" ? -1 : 1;
      if (va > vb) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return copy;
  }, [rows, columns, sortKey, sortDir]);

  const visible = sorted.slice(0, maxRows);

  const toggleSort = (key) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  return (
    <BlockShell
      block={block}
      title={block.title || "Details"}
      subtitle={rows.length > maxRows ? `Showing first ${maxRows} of ${rows.length}` : undefined}
      onPinToggle={onPinToggle}
    >
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="min-w-full divide-y divide-border text-left text-sm">
          <thead className="bg-surface-secondary">
            <tr>
              {columns.map((col) => (
                <th key={col.key} className="px-3 py-2 font-semibold text-text-secondary">
                  <button
                    type="button"
                    onClick={() => toggleSort(col.key)}
                    className="inline-flex items-center gap-1 hover:text-text-primary"
                    aria-label={`Sort by ${col.label}`}
                  >
                    {col.label}
                    {sortKey === col.key ? (
                      sortDir === "asc" ? <ChevronUp size={12} /> : <ChevronDown size={12} />
                    ) : null}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border bg-surface-default">
            {visible.map((row, i) => (
              <tr key={i}>
                {columns.map((col) => (
                  <td key={col.key} className="px-3 py-2 text-text-primary">
                    {formatCell(row[col.key], col.format)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </BlockShell>
  );
}
