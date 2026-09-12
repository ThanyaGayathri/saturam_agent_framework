/**
 * Shared helpers used by the per-profile automation-outputs seed files
 * (middleby-automation-outputs.js, retail-automation-outputs.js,
 * gfi-automation-outputs.js).
 *
 * The `buildStandardPdfBrief` helper renders a branded PDF from a declarative
 * spec (sections of text / tables / bullets / KPIs). jsPDF + jspdf-autotable
 * are dynamically imported so the libs only load when a viewer clicks a
 * report - main bundle stays lean.
 */

export function csvEscape(cell) {
  const s = cell == null ? "" : String(cell);
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

export function rowsToCsv(headers, rows) {
  const headerLine = headers.map((h) => csvEscape(h.label)).join(",");
  const bodyLines = rows.map((r) => headers.map((h) => csvEscape(r[h.key])).join(","));
  return [headerLine, ...bodyLines].join("\r\n");
}

export function csvBlob(headers, rows) {
  const csv = rowsToCsv(headers, rows);
  return new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
}

/**
 * Shared branded PDF brief renderer.
 *
 * @param {{
 *   eyebrow?: string,
 *   title: string,
 *   subtitle?: string,
 *   headline?: string,
 *   sections?: Array<
 *     | { type: "text", heading?: string, body: string | string[] }
 *     | { type: "table", heading?: string, head: string[], body: (string|number)[][], colWidths?: number[] }
 *     | { type: "kpis", heading?: string, items: Array<{ label: string, value: string, sub?: string }> }
 *     | { type: "bullets", heading?: string, items: string[] }
 *   >,
 *   footer?: string,
 * }} spec
 */
export async function buildStandardPdfBrief(spec) {
  const [{ jsPDF }, { default: autoTable }] = await Promise.all([
    import("jspdf"),
    import("jspdf-autotable"),
  ]);
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const pageWidth = doc.internal.pageSize.getWidth();
  const pageHeight = doc.internal.pageSize.getHeight();
  const margin = 48;
  let y = margin;

  const ensureRoom = (need) => {
    if (y + need > pageHeight - margin) {
      doc.addPage();
      y = margin;
    }
  };

  doc.setFontSize(10);
  doc.setFont(undefined, "bold");
  doc.setTextColor(105, 65, 198);
  doc.text(spec.eyebrow || "ENTERPRISE GPT", margin, y);
  y += 22;

  doc.setFontSize(22);
  doc.setTextColor(29, 41, 57);
  doc.text(spec.title, margin, y);
  y += 24;

  if (spec.subtitle) {
    doc.setFontSize(11);
    doc.setFont(undefined, "normal");
    doc.setTextColor(102, 112, 133);
    const subLines = doc.splitTextToSize(spec.subtitle, pageWidth - margin * 2);
    doc.text(subLines, margin, y);
    y += subLines.length * 14 + 14;
  }

  if (spec.headline) {
    doc.setFontSize(13);
    doc.setFont(undefined, "bold");
    doc.setTextColor(29, 41, 57);
    doc.text("Headline", margin, y);
    y += 16;
    doc.setFontSize(11);
    doc.setFont(undefined, "normal");
    doc.setTextColor(52, 64, 84);
    const lines = doc.splitTextToSize(spec.headline, pageWidth - margin * 2);
    ensureRoom(lines.length * 14 + 8);
    doc.text(lines, margin, y);
    y += lines.length * 14 + 18;
  }

  for (const section of spec.sections || []) {
    if (section.heading) {
      ensureRoom(30);
      doc.setFontSize(13);
      doc.setFont(undefined, "bold");
      doc.setTextColor(29, 41, 57);
      doc.text(section.heading, margin, y);
      y += 6;
    }

    if (section.type === "text") {
      const body = Array.isArray(section.body) ? section.body.join("\n") : section.body;
      doc.setFontSize(11);
      doc.setFont(undefined, "normal");
      doc.setTextColor(52, 64, 84);
      const lines = doc.splitTextToSize(body, pageWidth - margin * 2);
      ensureRoom(lines.length * 14 + 8);
      doc.text(lines, margin, y + 8);
      y += lines.length * 14 + 20;
    } else if (section.type === "bullets") {
      doc.setFontSize(11);
      doc.setFont(undefined, "normal");
      doc.setTextColor(52, 64, 84);
      for (const item of section.items) {
        const lines = doc.splitTextToSize(`•  ${item}`, pageWidth - margin * 2 - 12);
        ensureRoom(lines.length * 14 + 4);
        doc.text(lines, margin + 8, y + 12);
        y += lines.length * 14 + 4;
      }
      y += 12;
    } else if (section.type === "table") {
      autoTable(doc, {
        startY: y + 8,
        head: [section.head],
        body: section.body,
        margin: { left: margin, right: margin },
        styles: { fontSize: 9, cellPadding: 5 },
        headStyles: {
          fillColor: [242, 244, 247],
          textColor: [52, 64, 84],
          fontStyle: "bold",
        },
        columnStyles: section.colWidths
          ? Object.fromEntries(section.colWidths.map((w, i) => [i, { cellWidth: w }]))
          : undefined,
      });
      y = doc.lastAutoTable.finalY + 20;
    } else if (section.type === "kpis") {
      const cellW = (pageWidth - margin * 2) / Math.max(1, section.items.length);
      const boxH = 56;
      ensureRoom(boxH + 10);
      const startY = y + 4;
      section.items.forEach((kpi, i) => {
        const x = margin + i * cellW;
        doc.setDrawColor(228, 231, 236);
        doc.roundedRect(x + 4, startY, cellW - 8, boxH, 6, 6);
        doc.setFontSize(8);
        doc.setFont(undefined, "bold");
        doc.setTextColor(102, 112, 133);
        doc.text(kpi.label.toUpperCase(), x + 12, startY + 16);
        doc.setFontSize(16);
        doc.setFont(undefined, "bold");
        doc.setTextColor(29, 41, 57);
        doc.text(kpi.value, x + 12, startY + 36);
        if (kpi.sub) {
          doc.setFontSize(8);
          doc.setFont(undefined, "normal");
          doc.setTextColor(102, 112, 133);
          doc.text(kpi.sub, x + 12, startY + 50);
        }
      });
      y = startY + boxH + 18;
    }
  }

  if (spec.footer) {
    ensureRoom(20);
    doc.setFontSize(9);
    doc.setFont(undefined, "italic");
    doc.setTextColor(152, 162, 179);
    const lines = doc.splitTextToSize(spec.footer, pageWidth - margin * 2);
    doc.text(lines, margin, pageHeight - margin);
  }

  return doc.output("blob");
}

export const STANDARD_FOOTER =
  "Generated by Enterprise GPT. Sample data. In production, artifacts include full source citations and version history.";

export const WEEK_LABEL = `Week of ${new Date().toLocaleDateString(undefined, {
  month: "long",
  day: "numeric",
  year: "numeric",
})}`;
