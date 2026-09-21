// P6 -- a suggestion run that failed after a harvest is reported on the KPIs page and can be retried
// for that source; and a suggestion proposed from a connected source says why it cannot be added to
// the registry yet. Run after P6_kpi_suggestions_after_harvest.mjs, against the same API (the source
// it registered must still be there).
//
// Leaves a failed run for that source in the database (as a failure after a harvest would), then
// drives the KPIs page: the banner names the source and the recorded reason, "Try again" posts
// source_id, and the banner clears once the run succeeds.
//
//   P6_PY=.venv/bin/python P6_REPO=$PWD node P6_kpi_suggestion_failure.mjs

import { chromium } from "playwright";
import { execFileSync } from "node:child_process";

const BASE = "http://localhost:3000";
const API = "http://127.0.0.1:7777";
const OUT = process.env.P6_OUT || "./shots6";
const PY = process.env.P6_PY;
const ERROR = "The suggestion run failed (RuntimeError); the api log has the traceback.";

let failures = 0;
const check = (ok, message) => { console.log(`   ${ok ? "PASS" : "FAIL"}: ${message}`); if (!ok) failures += 1; };
const sources = await (await fetch(`${API}/configure/sources`)).json();
const source = sources.find((s) => s.harvest_state === "modelled");
if (!source) throw new Error("needs a modelled source");

// Leave a failed run newer than the source's last success, as a failure after a harvest would.
execFileSync(PY, ["-c", `
import psycopg
from egpt_retail.suggestions.runs import SuggestionRunStore
c = psycopg.connect("postgresql://ai:ai@localhost:5532/ai")
s = SuggestionRunStore(c)
r = s.create(None, (), None, "${source.id}")
s.mark_running(r.identifier)
s.mark_failed(r.identifier, "${ERROR}")
`], { env: { ...process.env, PYTHONPATH: "product" }, cwd: process.env.P6_REPO });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const consoleErrors = []; const apiCalls = []; const bodies = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
page.on("request", (r) => { if (r.url().includes(":7777") && r.method() !== "GET") { apiCalls.push(`${r.method()} ${new URL(r.url()).pathname}`); bodies.push(r.postData()); } });

console.log("-- the failure is reported");
await page.goto(`${BASE}/kpis/discover`);
const banner = page.getByTestId("suggestion-run-failure");
await banner.waitFor({ timeout: 20_000 }).then(() => check(true, "a banner reports the failed run"), () => check(false, "a banner reports the failed run"));
const text = (await banner.innerText().catch(() => "")) || "";
check(text.includes(source.name), `it names the source: ${source.name}`);
check(text.includes("RuntimeError"), "it gives the reason the run recorded");
await page.getByText("Store Visit Count", { exact: true }).first().waitFor({ state: "attached", timeout: 20_000 }).then(
  () => check(true, "the standing suggestions are still on the page beside it"), () => check(false, "the standing suggestions are still on the page beside it"));
await page.screenshot({ path: `${OUT}/10-failure-banner.png`, fullPage: false });

console.log("-- retry runs it again for that source");
await Promise.all([
  page.waitForResponse((r) => r.url().endsWith("/kpis/suggestions") && r.request().method() === "POST"),
  banner.getByRole("button", { name: "Try again" }).click(),
]);
const start = bodies.find((b) => b && b.includes("source_id"));
check(!!start && JSON.parse(start).source_id === source.id, `POST /kpis/suggestions carried source_id ${source.id}`);
// The run is generating (the notice), then lands: the banner and the notice both go, the list stays.
await page.getByTestId("suggestions-generating").waitFor({ timeout: 15_000 }).catch(() => {});
await page.getByTestId("suggestions-generating").waitFor({ state: "detached", timeout: 60_000 });
// Another connected source's own failure may still be reported (one at a time, newest first) --
// what must be gone is this source's.
const bannerText = (await banner.count()) ? await banner.innerText() : "";
check(!bannerText.includes(source.name), "this source's failure banner is gone once its run has succeeded");
await page.getByText("Store Visit Count", { exact: true }).first().waitFor({ state: "attached", timeout: 20_000 });
const after = await (await fetch(`${API}/kpis/suggestions/current`)).json();
check(after.failed === null || after.failed.source_id !== source.id, "the backend no longer reports this source's failure");
check(after.suggestions.some((s) => s.source_id === source.id), "the source's suggestions are current");

console.log("-- a suggestion from a connected source cannot be added yet, and says why");
for (let i = 0; i < 6; i += 1) { const c = page.locator('button[aria-expanded="false"]'); if (!(await c.count())) break; await c.first().click().catch(() => {}); }
const row = page.locator("tr", { has: page.getByText("Store Visit Count", { exact: true }) });
check((await row.locator('button[title="Add to my metrics"]').count()) === 0, "the row offers no 'Add to my metrics'");
check((await row.getByTestId("dismiss-suggestion").count()) === 1, "but it can still be dismissed");
await row.getByRole("button", { name: /Details for/ }).click();
await page.getByTestId("source-suggestion-note").waitFor({ timeout: 10_000 }).then(() => check(true, "the detail panel explains why"), () => check(false, "the detail panel explains why"));
const panel = page.locator("div", { has: page.getByTestId("source-suggestion-note") }).last();
check((await panel.getByRole("button", { name: /Add to my metrics/ }).count()) === 0, "the panel offers no 'Add to my metrics'");
check((await page.getByTestId("panel-dismiss-suggestion").count()) === 1, "the panel offers 'Dismiss this suggestion'");
await page.screenshot({ path: `${OUT}/11-source-suggestion-panel.png`, fullPage: false });

console.log("\nconsole errors:", consoleErrors.length ? consoleErrors : "none");
console.log(failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`);
await browser.close();
process.exit(failures === 0 ? 0 : 1);
