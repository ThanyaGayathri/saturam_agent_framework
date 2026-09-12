// Client-side export of pinned blocks to PPTX, PDF, or plain-text email.
//
// Each block type has a serializer that knows how to render itself into each
// destination format. The public entry points return a Blob (or trigger a
// download) so the caller can decide how to deliver it.

import PptxGenJS from "pptxgenjs";
import { jsPDF } from "jspdf";
import autoTable from "jspdf-autotable";
import { BLOCK_TYPES } from "./schema";

const REPORT_TITLE = "Enterprise GPT — Middleby report";

// ---------- helpers -------------------------------------------------------

function stringifyBlockLead(block) {
  switch (block.type) {
    case BLOCK_TYPES.INSIGHT:
      return { title: "Highlight", body: [block.headline, block.subtext].filter(Boolean).join("\n") };
    case BLOCK_TYPES.FINDINGS:
      return {
        title: block.title || "Key numbers",
        body: (block.items || [])
          .map((it) => `${it.title}: ${it.value}${it.change ? ` (${it.change})` : ""}${it.desc ? ` — ${it.desc}` : ""}`)
          .join("\n"),
      };
    case BLOCK_TYPES.CHART:
      return {
        title: block.title || "Chart",
        body: `${block.description || "Chart"}\n(${(block.data || []).length} data points, kind: ${block.chartKind || "chart"})`,
      };
    case BLOCK_TYPES.TABLE:
      return { title: block.title || "Data", body: `${(block.rows || []).length} rows` };
    case BLOCK_TYPES.RECOMMENDATION:
      return {
        title: block.title || "What to try next",
        body: (block.items || [])
          .map((it, i) => {
            const t = typeof it === "string" ? it : it.text || "";
            return `${i + 1}. ${t}`;
          })
          .join("\n"),
      };
    case BLOCK_TYPES.BRIEF:
      return {
        title: "Quick summary",
        body: [block.headline, ...(block.bullets || []).map((b) => `• ${b}`)].filter(Boolean).join("\n"),
      };
    case BLOCK_TYPES.CITATION:
      return {
        title: "Where this came from",
        body: (block.sources || [])
          .map((s) => `${s.system} — ${s.scope} (${s.isReal ? "Real data" : "Example data"})`)
          .join("\n"),
      };
    case BLOCK_TYPES.JOURNEY:
      return {
        title: block.title || "Customer journey",
        body: (block.events || []).map((e) => `[${e.lane}] ${e.label}`).join("\n"),
      };
    case BLOCK_TYPES.ACTION:
      return { title: "Actions", body: (block.items || []).map((it) => `• ${it.label}`).join("\n") };
    default:
      return { title: block.type || "Block", body: JSON.stringify(block).slice(0, 200) };
  }
}

// ---------- PPTX ----------------------------------------------------------

function pptxAddTable(slide, block) {
  const cols = block.columns || [];
  const rows = block.rows || [];
  if (!cols.length || !rows.length) return;
  const headerRow = cols.map((c) => ({
    text: c.label,
    options: { bold: true, fill: { color: "F2F4F7" }, color: "344054" },
  }));
  const bodyRows = rows.slice(0, 12).map((r) =>
    cols.map((c) => ({
      text: String(r[c.key] ?? "—"),
      options: { color: "1D2939" },
    }))
  );
  slide.addTable([headerRow, ...bodyRows], {
    x: 0.5, y: 1.5, w: 9, colW: cols.length ? Array(cols.length).fill(9 / cols.length) : undefined,
    fontSize: 10, border: { pt: 0.5, color: "E4E7EC" },
  });
}

function pptxAddChart(slide, block) {
  const kind = block.chartKind;
  const data = block.data || [];
  if (!data.length) return;
  const labels = data.map((r) => String(r[block.config?.xKey || "label"] ?? r.stage ?? ""));
  const values = data.map((r) => Number(r[block.config?.yKey || "value"] ?? 0));
  const series = [{ name: block.title || "Value", labels, values }];
  const chartType = kind === "pie" ? "pie" : kind === "line" || kind === "spark" ? "line" : "bar";
  slide.addChart(chartType, series, {
    x: 0.5, y: 1.5, w: 9, h: 4.5,
    showLegend: chartType === "pie",
    catAxisLabelFontSize: 9,
    valAxisLabelFontSize: 9,
  });
}

export async function exportBlocksAsPptx(items, opts = {}) {
  if (!items?.length) return null;
  const pptx = new PptxGenJS();
  pptx.title = opts.title || REPORT_TITLE;
  pptx.company = "Middleby";
  pptx.author = "Enterprise GPT";
  pptx.subject = REPORT_TITLE;

  // Cover slide
  const cover = pptx.addSlide();
  cover.addText(opts.title || REPORT_TITLE, {
    x: 0.5, y: 2.2, w: 9, h: 1, fontSize: 32, bold: true, color: "6941C6",
  });
  cover.addText(new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }), {
    x: 0.5, y: 3.4, w: 9, h: 0.5, fontSize: 14, color: "667085",
  });
  cover.addText(`${items.length} pinned insight${items.length === 1 ? "" : "s"}`, {
    x: 0.5, y: 4.0, w: 9, h: 0.5, fontSize: 14, color: "667085",
  });

  for (const item of items) {
    const block = item.block;
    const slide = pptx.addSlide();
    const lead = stringifyBlockLead(block);
    slide.addText(lead.title, {
      x: 0.5, y: 0.3, w: 9, h: 0.6, fontSize: 22, bold: true, color: "1D2939",
    });
    if (item.sourceQuestion) {
      slide.addText(`In response to: “${item.sourceQuestion}”`, {
        x: 0.5, y: 0.95, w: 9, h: 0.35, fontSize: 10, italic: true, color: "667085",
      });
    }

    if (block.type === BLOCK_TYPES.CHART) {
      pptxAddChart(slide, block);
    } else if (block.type === BLOCK_TYPES.TABLE) {
      pptxAddTable(slide, block);
    } else {
      slide.addText(lead.body, {
        x: 0.5, y: 1.5, w: 9, h: 4.5, fontSize: 14, color: "344054", valign: "top",
      });
    }
  }

  return await pptx.write({ outputType: "blob" });
}

// ---------- PDF -----------------------------------------------------------

export function exportBlocksAsPdf(items, opts = {}) {
  if (!items?.length) return null;
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

  doc.setFontSize(22);
  doc.setFont(undefined, "bold");
  doc.setTextColor(105, 65, 198);
  doc.text(opts.title || REPORT_TITLE, margin, y);
  y += 26;

  doc.setFontSize(11);
  doc.setFont(undefined, "normal");
  doc.setTextColor(102, 112, 133);
  doc.text(new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }), margin, y);
  y += 30;

  for (const item of items) {
    const block = item.block;
    const lead = stringifyBlockLead(block);
    ensureRoom(60);

    doc.setFontSize(15);
    doc.setFont(undefined, "bold");
    doc.setTextColor(29, 41, 57);
    doc.text(lead.title, margin, y);
    y += 18;

    if (item.sourceQuestion) {
      doc.setFontSize(9);
      doc.setFont(undefined, "italic");
      doc.setTextColor(102, 112, 133);
      const lines = doc.splitTextToSize(`In response to: "${item.sourceQuestion}"`, pageWidth - margin * 2);
      doc.text(lines, margin, y);
      y += lines.length * 12 + 6;
    }

    if (block.type === BLOCK_TYPES.TABLE) {
      const cols = block.columns || [];
      const rows = block.rows || [];
      if (cols.length && rows.length) {
        autoTable(doc, {
          startY: y,
          head: [cols.map((c) => c.label)],
          body: rows.slice(0, 20).map((r) => cols.map((c) => String(r[c.key] ?? "—"))),
          margin: { left: margin, right: margin },
          styles: { fontSize: 9, cellPadding: 4 },
          headStyles: { fillColor: [242, 244, 247], textColor: [52, 64, 84] },
        });
        y = doc.lastAutoTable.finalY + 20;
        continue;
      }
    }

    doc.setFontSize(11);
    doc.setFont(undefined, "normal");
    doc.setTextColor(52, 64, 84);
    const body = lead.body || "";
    const bodyLines = doc.splitTextToSize(body, pageWidth - margin * 2);
    ensureRoom(bodyLines.length * 14);
    doc.text(bodyLines, margin, y);
    y += bodyLines.length * 14 + 16;
  }

  return doc.output("blob");
}

// ---------- Email-ready plain text ----------------------------------------

export function exportBlocksAsEmailText(items, opts = {}) {
  if (!items?.length) return "";
  const lines = [];
  lines.push(opts.title || REPORT_TITLE);
  lines.push("=".repeat((opts.title || REPORT_TITLE).length));
  lines.push("");
  lines.push(new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" }));
  lines.push("");
  for (const item of items) {
    const lead = stringifyBlockLead(item.block);
    lines.push(lead.title);
    lines.push("-".repeat(lead.title.length));
    if (item.sourceQuestion) {
      lines.push(`In response to: "${item.sourceQuestion}"`);
      lines.push("");
    }
    lines.push(lead.body);
    lines.push("");
  }
  return lines.join("\n");
}

// ---------- File download helper ------------------------------------------

export function downloadBlob(blob, filename) {
  if (!blob) return;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
