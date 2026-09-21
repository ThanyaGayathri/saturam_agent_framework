// P6 -- KPI suggestions after a harvest, kept between visits, removable; the harvested tables in the
// Data catalog. Drives the real flow in a headless browser against a running API and the Vite dev
// server: connect a source -> its harvest ends -> KPI suggestions are proposed automatically -> the
// KPIs page shows them (saying so while the run works; there is no "Suggest KPIs" button and no
// AI-generated Add KPI to start one) -> they survive a reload ->
// a dismissed one stays gone, also after a new run proposes it again -> the harvested tables and
// their columns are in the Data catalog.
//
// Needs a Postgres source the API host can reach (not localhost: the SSRF guard refuses it, by
// design) holding a schema named p4_ui with two tables, store and store_visit (the local
// warehouse from P4's setup), and -- because a model is not assumed -- the API started in the
// suggestion replay mode with the fixture next to this file:
//
//   EGPT_RETAIL_SUGGESTION_REPLAY=cookbook/94_retail/P6_replay_suggestions.json \
//     .venv/bin/egpt-retail api --host 127.0.0.1 --port 7777        (plus the usual local env)
//   npm --prefix frontend run dev                                    (http://localhost:3000)
//
// Run from a directory with playwright installed (ESM ignores NODE_PATH):
//
//   P6_HOST=$(ipconfig getifaddr en0) node P6_kpi_suggestions_after_harvest.mjs
//
// Start from a clean slate (no registered sources, no p4_ui graph nodes, no dismissals): the script
// registers "P6 e2e warehouse" itself.

import { chromium } from "playwright";

const BASE = process.env.P6_BASE || "http://localhost:3000";
const API = process.env.P6_API || "http://127.0.0.1:7777";
const HOST = process.env.P6_HOST;
if (!HOST) throw new Error("P6_HOST is required: an address of the local Postgres that the SSRF guard allows.");
const OUT = process.env.P6_OUT || "./shots6";
const SOURCE_NAME = "P6 e2e warehouse";
const OWN = ["Store Count", "Store Visit Count", "Visits Per Store", "Locations Covered"];

const consoleErrors = [];
const failedApi = [];
const apiCalls = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
page.on("request", (r) => { if (r.url().includes(":7777")) apiCalls.push(`${r.method()} ${new URL(r.url()).pathname}`); });
page.on("response", (r) => {
  if (r.status() >= 400 && r.url().includes(":7777")) failedApi.push(`${r.status()} ${r.request().method()} ${r.url()}`);
});

let failures = 0;
const check = (ok, message) => {
  console.log(`   ${ok ? "PASS" : "FAIL"}: ${message}`);
  if (!ok) failures += 1;
};
const step = async (name, fn) => {
  console.log(`-- ${name}`);
  try { await fn(); } finally { await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }); }
};
const api = async (path, init) => (await fetch(`${API}${path}`, init)).json();
const current = () => api("/kpis/suggestions/current");
const ownIds = (c) => c.suggestions.filter((s) => s.source_id).map((s) => s.name).sort();
const rowCount = async (name) => page.getByText(name, { exact: true }).count();
const openAllGroups = async () => {
  // Business Area / Subject Area groups start collapsed; open whatever is closed.
  for (let i = 0; i < 6; i += 1) {
    const closed = page.locator('button[aria-expanded="false"]');
    if ((await closed.count()) === 0) break;
    await closed.first().click().catch(() => {});
  }
};

await step("01-connect-source", async () => {
  await page.goto(`${BASE}/configure/internal-sources`);
  await page.getByRole("button", { name: "Add database" }).click();
  await page.getByPlaceholder("e.g. Production DB").fill(SOURCE_NAME);
  await page.getByPlaceholder("warehouse.internal").fill(HOST);
  await page.getByPlaceholder("postgres").fill("egpt_warehouse");
  await page.getByPlaceholder("5432").fill("5532");
  await page.getByPlaceholder("public").fill("p4_ui");
  await page.locator("select").nth(1).selectOption("prefer");
  await page.getByPlaceholder("egpt_reader").fill("ai");
  await page.getByPlaceholder("••••••••").fill("ai");
  await page.getByRole("button", { name: "Test Connection" }).click();
  await page.getByText("Connected", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Save Changes" }).click();
  await page.waitForURL(`${BASE}/configure/internal-sources`);
});

await step("02-harvest-modelled", async () => {
  await page.getByText("Modelled", { exact: true }).first().waitFor({ timeout: 90_000 });
  check(true, "the source reached Modelled");
});

await step("02b-page-attaches-to-the-run-still-generating", async () => {
  // The harvest is over; the suggest-kpis step is what is running now. Opening the KPIs page at
  // this moment must show that run's progress -- without anyone clicking "Suggest KPIs" -- and
  // then the suggestions themselves.
  apiCalls.length = 0;
  await page.goto(BASE + "/kpis/discover");
  const generating = page.getByTestId("suggestions-generating");
  await generating.waitFor({ timeout: 8_000 }).then(
    () => check(true, "the page says a run is still generating"),
    () => check(false, "the page says a run is still generating")
  );
  check(/Suggesting KPIs for P6 e2e warehouse/.test((await generating.innerText().catch(() => "")) || ""), "and names the source");
  await page.getByText("Store Visit Count", { exact: true }).first().waitFor({ state: "attached", timeout: 40_000 }).then(
    () => check(true, "the suggestions appear on their own when the run lands"),
    () => check(false, "the suggestions appear on their own when the run lands")
  );
  check(apiCalls.includes("GET /kpis/suggestions/current"), "the page asked for the standing suggestions on open");
  check(!apiCalls.some((c) => c === "POST /kpis/suggestions"), "nobody had to start a run");
  await page.getByTestId("suggestions-generating").waitFor({ state: "detached", timeout: 40_000 }).then(
    () => check(true, "the generating notice is gone once the run has landed"),
    () => check(false, "the generating notice is gone once the run has landed")
  );
  check((await page.getByRole("button", { name: /Suggest KPIs/i }).count()) === 0, 'there is no "Suggest KPIs" button');
  check((await page.getByText(/Ask the model what else/).count()) === 0, "and no idle bar inviting one");
  const pageText = await page.locator("body").innerText();
  check(!pageText.includes("*/") && !pageText.includes("{/*"), "no stray source comment text is rendered on the page");
});

let sourceId;
await step("03-suggestions-generate-after-harvest", async () => {
  // The suggest-kpis step is what runs next; it is the workflow's own, nobody clicks anything.
  const sources = await api("/configure/sources");
  sourceId = sources.find((s) => s.name === SOURCE_NAME).id;
  const deadline = Date.now() + 60_000;
  let c = await current();
  while (Date.now() < deadline && !(ownIds(c).length === OWN.length && !c.generating)) {
    await new Promise((r) => setTimeout(r, 1000));
    c = await current();
  }
  check(JSON.stringify(ownIds(c)) === JSON.stringify([...OWN].sort()), `the source's suggestions were proposed: ${ownIds(c).join(", ")}`);
  check(c.suggestions.filter((s) => s.source_id).every((s) => s.source_id === sourceId), "each is stamped with the source it came from");
  check(c.generating === null, "nothing is still generating");
});

await step("04-kpis-page-shows-them", async () => {
  await page.goto(`${BASE}/kpis/discover`);
  await page.getByText("Store Visit Count", { exact: true }).first().waitFor({ state: "attached", timeout: 30_000 }).catch(() => {});
  await openAllGroups();
  for (const name of OWN) check((await rowCount(name)) >= 1, `"${name}" is on the KPIs page`);
});

await step("04b-add-kpi-is-the-manual-form-only", async () => {
  await page.goto(`${BASE}/kpis/discover?add=1`);
  await page.getByLabel("Name").waitFor({ timeout: 15_000 });
  check(true, "Add KPI opens straight onto the form that posts to the registry");
  check((await page.getByText(/AI generated KPI/).count()) === 0, "there is no AI-generated option to pick");
  check((await page.getByText(/Change how/).count()) === 0, "and no mode picker to go back to");
  await page.goto(`${BASE}/kpis/discover`);
  await page.getByText("Store Visit Count", { exact: true }).first().waitFor({ state: "attached", timeout: 30_000 }).catch(() => {});
});

await step("05-survives-a-reload", async () => {
  apiCalls.length = 0;
  await page.reload();
  await page.getByText("Store Visit Count", { exact: true }).first().waitFor({ state: "attached", timeout: 30_000 }).catch(() => {});
  await openAllGroups();
  for (const name of OWN) check((await rowCount(name)) >= 1, `"${name}" is still there after a reload`);
  check(apiCalls.includes("GET /kpis/suggestions/current"), "the reload read the standing suggestions (the request log sees API calls)");
  check(!apiCalls.some((c) => c === "POST /kpis/suggestions"), "the reload started no new suggestion run");
});

await step("06-dismiss-one", async () => {
  const before = await page.getByTestId("dismiss-suggestion").count();
  const row = page.locator("tr", { has: page.getByText("Locations Covered", { exact: true }) });
  await row.getByTestId("dismiss-suggestion").click();
  const [dismissal] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith("/kpis/suggestions/dismissals") && r.request().method() === "POST"),
    page.getByTestId("confirm-dismiss-suggestion").click(),
  ]);
  check(dismissal.status() === 200, `POST /kpis/suggestions/dismissals -> ${dismissal.status()}`);
  await page.waitForTimeout(500);
  check((await rowCount("Locations Covered")) === 0, '"Locations Covered" left the page');
  check((await page.getByTestId("dismiss-suggestion").count()) === before - 1, "the other suggestions are untouched");
  const c = await current();
  check(!c.suggestions.some((s) => s.name === "Locations Covered"), "the backend no longer lists it");
});

await step("07-dismissed-stays-gone-after-reload", async () => {
  await page.reload();
  await page.getByText("Store Visit Count", { exact: true }).first().waitFor({ state: "attached", timeout: 30_000 }).catch(() => {});
  await openAllGroups();
  check((await rowCount("Locations Covered")) === 0, '"Locations Covered" is not shown after a reload');
  for (const name of OWN.filter((n) => n !== "Locations Covered")) check((await rowCount(name)) >= 1, `"${name}" is still shown`);
});

await step("08-dismissed-stays-gone-after-a-new-run", async () => {
  // Harvest again: the agent (here, the replay file) proposes all four once more.
  const body = new URLSearchParams({ message: "harvest", stream: "false", background: "true", additional_data: JSON.stringify({ source_id: sourceId }) });
  const res = await fetch(`${API}/workflows/harvest-source/runs`, { method: "POST", body });
  check(res.status === 202, `POST /workflows/harvest-source/runs -> ${res.status}`);
  await new Promise((r) => setTimeout(r, 1500));
  const deadline = Date.now() + 90_000;
  let c = await current();
  while (Date.now() < deadline && (c.generating || !c.suggestions.length)) {
    await new Promise((r) => setTimeout(r, 1000));
    c = await current();
  }
  const runs = await fetch(`${API}/kpis/suggestions/${c.suggestions[0]?.source_id ? "" : ""}`).catch(() => null);
  check(!c.suggestions.some((s) => s.name === "Locations Covered"), "the dismissed suggestion did not come back with the new run");
  check(ownIds(c).length === 3, `the others are listed once each: ${ownIds(c).join(", ")}`);
  await page.reload();
  await page.getByText("Store Visit Count", { exact: true }).first().waitFor({ state: "attached", timeout: 30_000 }).catch(() => {});
  await openAllGroups();
  check((await rowCount("Locations Covered")) === 0, "and the page agrees");
});

await step("09-data-catalog-has-the-tables", async () => {
  await page.goto(`${BASE}/kpis/gallery`);
  // Other connected sources are listed too (the catalog lists every modelled source); pick ours.
  const ours = page.getByTestId("source-sync").filter({ hasText: SOURCE_NAME });
  await ours.waitFor({ timeout: 30_000 });
  const sync = await ours.first().innerText();
  console.log("   sync line:", sync);
  check(sync.includes(SOURCE_NAME) && sync.includes("2 tables"), "the catalog says the source contributed 2 tables");
  const chips = await page.getByTestId("dataset-source").allInnerTexts();
  check(chips.filter((t) => t === SOURCE_NAME).length === 2, `two cards are marked as coming from ${SOURCE_NAME}`);
  for (const table of ["store", "store_visit"]) {
    const card = page.locator("div", { has: page.getByRole("heading", { name: table, exact: true }) }).filter({ has: page.getByTestId("dataset-source") }).last();
    check((await card.count()) >= 1, `a "${table}" dataset card is shown`);
  }
  const attrs = await page.locator("text=Attributes").first().locator("..").innerText().catch(() => "");
  const cardText = await page.locator("div", { has: page.getByRole("heading", { name: "store_visit", exact: true }) }).last().innerText();
  check(/attributes\s*\n?\s*[1-9]/i.test(cardText), "its columns were read back (attribute count > 0)");
});

console.log("\nconsole errors:", consoleErrors.length ? consoleErrors : "none");
console.log("failed API calls:", failedApi.length ? failedApi : "none");
console.log(failures === 0 ? "\nALL CHECKS PASSED" : `\n${failures} CHECK(S) FAILED`);
await browser.close();
process.exit(failures === 0 ? 0 : 1);
