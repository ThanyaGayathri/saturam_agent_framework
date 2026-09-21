// P7 -- a source stuck in needs_review has a way out in the UI, and deleting a source frees its
// schema. Drives the real Configure > Data sources screen in a headless browser against a running
// API, Postgres and Neo4j (the API-level twin is P7_delete_source_cleanup.py):
//
//   1. a source on schema A is modelled; a second source on A stops in needs_review (a real
//      holder is respected) and its card offers "Retry modelling" beside "Review 1 item"
//   2. the reason says what is true (another connected source or the deployment's own
//      warehouse), points at controls that exist, and never says "reconnect"
//   3. way out 1: edit the rival's schema and save -- the harvest re-runs and it is modelled
//   4. way out 2: remove the holder in the UI (the confirm dialog says its tables go with it),
//      then "Retry modelling" on another stuck rival -- modelled
//
// Needs two scratch warehouse schemas with a table or two each, and a Postgres the API host can
// reach on a non-loopback address (the SSRF guard refuses localhost by design):
//
//   CREATE SCHEMA p7_a; CREATE TABLE p7_a.shop (id int primary key, v text);
//   CREATE SCHEMA p7_b; CREATE TABLE p7_b.shop (id int primary key, v text);
//
// with the API on :7777 and the Vite dev server on :3000. Run from a directory with playwright
// installed (ESM ignores NODE_PATH):
//
//   P7_HOST=$(ipconfig getifaddr en0) node P7_needs_review_way_out.mjs
//
// It registers "P7 holder", "P7 rival" and "P7 rival two" itself, removes them by id at the end,
// and asserts that every other registered source is unchanged.

import { chromium } from "playwright";

const BASE = process.env.P7_BASE || "http://localhost:3000";
const API = process.env.P7_API || "http://127.0.0.1:7777";
const HOST = process.env.P7_HOST;
const OUT = process.env.P7_OUT || "./shots7";
if (!HOST) throw new Error("P7_HOST is required (a non-loopback address of the local Postgres).");
const SCHEMA_A = "p7_a";
const SCHEMA_B = "p7_b";

let failures = 0;
const check = (ok, message) => { console.log(`   ${ok ? "PASS" : "FAIL"}: ${message}`); if (!ok) failures += 1; };
const call = async (method, path, body) => {
  const res = await fetch(`${API}${path}`, { method, headers: body ? { "content-type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const text = await res.text();
  return { status: res.status, body: text ? JSON.parse(text) : null };
};
const listed = async () => (await call("GET", "/configure/sources")).body;
const byName = async (name) => (await listed()).find((s) => s.name === name);
const stateOf = async (name) => (await byName(name))?.harvest_state;
const waitState = async (name, want, ms = 90_000) => {
  const end = Date.now() + ms;
  while (Date.now() < end) { const s = await stateOf(name); if (s && s !== "building" && s !== "not_started") return s; await new Promise((r) => setTimeout(r, 1000)); }
  return "timeout";
};
const othersBefore = (await listed()).map((s) => s.id).sort();

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const consoleErrors = []; const dialogs = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
page.on("dialog", async (d) => { dialogs.push(d.message()); await d.accept(); });
const card = (name) => page.locator("div.rounded-2xl", { has: page.getByRole("heading", { name, exact: true }) }).last();
const shot = (n) => page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true });

const fillNew = async ({ name, schema }) => {
  await page.getByPlaceholder("e.g. Production DB").fill(name);
  await page.getByPlaceholder("warehouse.internal").fill(HOST);
  await page.getByPlaceholder("postgres").fill("egpt_warehouse");
  await page.getByPlaceholder("5432").fill("5532");
  await page.getByPlaceholder("public").fill(schema);
  await page.locator("select").nth(1).selectOption("prefer");
  await page.getByPlaceholder("egpt_reader").fill("ai");
  await page.getByPlaceholder("••••••••").fill("ai");
};
const addSource = async (name, schema) => {
  await page.goto(`${BASE}/configure/internal-sources`);
  await page.getByRole("button", { name: "Add database" }).click();
  await fillNew({ name, schema });
  await Promise.all([
    page.waitForResponse((r) => r.url().includes("/workflows/harvest-source/runs")),
    page.getByRole("button", { name: "Create Datasource" }).click(),
  ]);
  await page.waitForURL(`${BASE}/configure/internal-sources`);
};

try {
  console.log("-- a holder of the schema is registered and modelled");
  await page.goto(`${BASE}/configure/internal-sources`);
  await addSource("P7 holder", SCHEMA_A);
  check((await waitState("P7 holder", "modelled")) === "modelled", "P7 holder is modelled");
  await page.reload();
  check((await card("P7 holder").getByRole("button", { name: "Retry modelling" }).count()) === 0, "a modelled card offers no 'Retry modelling'");

  console.log("-- a second source on the same schema stops in needs_review, and the card offers a way out");
  await addSource("P7 rival", SCHEMA_A);
  check((await waitState("P7 rival")) === "needs_review", "P7 rival is needs_review (a real holder is respected)");
  await page.goto(`${BASE}/configure/internal-sources`);
  await card("P7 rival").getByText("Needs review").first().waitFor({ timeout: 15_000 });
  const retry = card("P7 rival").getByRole("button", { name: "Retry modelling" });
  check((await retry.count()) === 1, "the needs_review card shows 'Retry modelling'");
  check((await card("P7 rival").getByRole("button", { name: /Review 1 item/ }).count()) === 1, "and still 'Review 1 item'");
  await shot("01-needs-review-card");
  await card("P7 rival").getByRole("button", { name: /Review 1 item/ }).click();
  const reason = await page.getByText(/schema name is already used/).first().innerText();
  check(/another connected source or by the deployment's own warehouse/.test(reason), `the reason names the real cases: "${reason.slice(0, 90)}..."`);
  check(!/reconnect/i.test(reason) && /Edit the source/.test(reason) && /retry modelling/i.test(reason), "it points at controls that exist, and never says 'reconnect'");
  check((await page.getByRole("button", { name: "Retry modelling" }).count()) >= 1, "the semantic-layer screen says 'Retry modelling' (not 'Build model')");
  await shot("02-needs-review-reason");

  console.log("-- way out 1: edit the schema and save re-runs the harvest");
  const rival = await byName("P7 rival");
  await page.goto(`${BASE}/configure/internal-sources/edit/${encodeURIComponent(rival.id)}`);
  await page.getByPlaceholder("public").fill(SCHEMA_B);
  const [run] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/workflows/harvest-source/runs"), { timeout: 15_000 }).catch(() => null),
    page.getByRole("button", { name: "Save Changes" }).click(),
  ]);
  check(!!run && run.status() === 202, `saving the edited schema started a harvest (${run ? run.status() : "no request"})`);
  check((await waitState("P7 rival")) === "modelled", "P7 rival is modelled on its new schema");
  await page.goto(`${BASE}/configure/internal-sources`);
  await shot("03-after-edit-modelled");

  console.log("-- way out 2: remove the holder in the UI, then Retry modelling frees a source that was stuck");
  await addSource("P7 rival two", SCHEMA_A);
  check((await waitState("P7 rival two")) === "needs_review", "P7 rival two is needs_review");
  await page.goto(`${BASE}/configure/internal-sources`);
  await card("P7 holder").getByRole("button", { name: "More options" }).click();
  await card("P7 holder").getByRole("button", { name: "Remove" }).click();
  await page.waitForTimeout(1500);
  check(dialogs.some((d) => /Its tables are removed from the semantic layer/.test(d)), `the confirm dialog says what removal does: "${dialogs.at(-1)}"`);
  check(!(await byName("P7 holder")), "the holder is gone");
  const [retryRun] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/workflows/harvest-source/runs")),
    card("P7 rival two").getByRole("button", { name: "Retry modelling" }).click(),
  ]);
  check(retryRun.status() === 202, "Retry modelling started a harvest");
  check((await waitState("P7 rival two")) === "modelled", "P7 rival two is modelled once the deleted holder's claim is gone");
  await shot("04-retry-modelled");
} finally {
  for (const name of ["P7 rival two", "P7 rival", "P7 holder"]) {
    const s = await byName(name);
    if (s) console.log(`   cleanup DELETE ${s.id} ->`, (await call("DELETE", `/configure/sources/${s.id}`)).status);
  }
}
const othersAfter = (await listed()).map((s) => s.id).sort();
check(JSON.stringify(othersBefore) === JSON.stringify(othersAfter), `every other registered source is untouched (${othersBefore.length})`);
console.log("\nconsole errors:", consoleErrors.length ? consoleErrors : "none");
console.log(failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`);
await browser.close();
process.exit(failures === 0 ? 0 : 1);
