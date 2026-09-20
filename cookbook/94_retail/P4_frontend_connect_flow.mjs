/**
 * #308 -- the Configure > Data sources screens, driven in a headless browser against the real
 * API, Postgres and Neo4j. Not a Python cookbook: the thing under test is the browser flow.
 *
 * Setup (all local):
 *   .venv/bin/egpt-retail api                       # the retail API on :7777, pointed at the Docker Postgres/Neo4j
 *   cd frontend && npm run dev                      # Vite on :3000
 *   psql -h localhost -p 5532 -U ai egpt_warehouse  # a throwaway schema to harvest:
 *     CREATE SCHEMA p4_ui; CREATE TABLE p4_ui.store(store_code text primary key, region text);
 *     CREATE TABLE p4_ui.store_visit(id int primary key, store_code text, visited_at timestamptz, basket_value numeric);
 *   mkdir -p /tmp/p4 && cd /tmp/p4 && npm i playwright@1.63.0 && npx playwright install chromium
 *
 * ESM resolves `import "playwright"` from the script's own directory (NODE_PATH is ignored), so
 * run a copy from that scratch directory. The SSRF guard refuses loopback, so the harvest target
 * must be reached on a NON-loopback address -- for the Docker Postgres that is this machine's
 * LAN IP (`ipconfig getifaddr en0`):
 *
 *   cp cookbook/94_retail/P4_frontend_connect_flow.mjs /tmp/p4/ && cd /tmp/p4
 *   P4_HOST=192.168.x.y P4_OUT=/tmp/p4/shots node P4_frontend_connect_flow.mjs
 *
 * `P4_BASE` (default http://localhost:3000) and `P4_API` (default http://localhost:7777) name
 * the Vite dev server and the retail API it talks to, when they run on other ports.
 *
 * Starts from an empty `GET /configure/sources`; re-runs need the sources deleted and the
 * `p4_ui` SemanticTable nodes removed from the graph first (the collision guard would otherwise
 * see the previous run's source_id on that schema and stop at needs_review). Screenshots of
 * every step land in P4_OUT.
 *
 * Pass/fail: a step's own assertions, plus two run-wide gates. Any page or console error, and
 * any retail-API response >= 400 that a step did not declare it provokes (`expectFailure`),
 * fail the run with exit code 1 -- a 500 mid-flow cannot slip past while the named `waitFor`s
 * happen to pass.
 */
import { chromium } from "playwright";

const BASE = process.env.P4_BASE || "http://localhost:3000";
const HOST = process.env.P4_HOST;
if (!HOST) {
  console.error("P4_HOST is required: a non-loopback address the local Postgres answers on (the LAN IP).");
  process.exit(2);
}
const OUT = process.env.P4_OUT || "/tmp/p4-shots";
// The retail API the frontend was started against (`VITE_API_URL`); failures are judged only
// for responses from it.
const API_ORIGIN = new URL(process.env.P4_API || "http://localhost:7777").origin;
const isApi = (url) => new URL(url).origin === API_ORIGIN;

const consoleErrors = [];
const failedApi = [];
const patches = [];
// API failures the running step provokes on purpose, as `{status, method, path}`; each must be
// seen exactly once, and anything else >= 400 is a failure.
let expectedFailures = [];
const expectFailure = (status, method, path) => expectedFailures.push({ status, method, path, seen: false });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("console", (m) => {
  if (m.type() !== "error") return;
  // The browser echoes every failed fetch as "Failed to load resource"; for the retail API
  // that is the same event `failedApi` already judges, so it is not counted twice.
  if (m.text().startsWith("Failed to load resource") && isApi(m.location().url)) return;
  consoleErrors.push(m.text());
});
page.on("pageerror", (e) => consoleErrors.push(`pageerror: ${e.message}`));
page.on("request", (r) => { if (r.method() === "PATCH") patches.push(r.url()); });
page.on("response", (r) => {
  if (r.status() < 400 || !isApi(r.url())) return;
  const { pathname } = new URL(r.url());
  const provoked = expectedFailures.find(
    (e) => !e.seen && e.status === r.status() && e.method === r.request().method() && e.path === pathname
  );
  if (provoked) provoked.seen = true;
  else failedApi.push(`${r.status()} ${r.request().method()} ${r.url()}`);
});

const step = async (name, fn) => {
  console.log(`-- ${name}`);
  expectedFailures = [];
  try {
    await fn();
    const missing = expectedFailures.filter((e) => !e.seen);
    if (missing.length) {
      throw new Error(`${name}: expected API failure never happened: ${JSON.stringify(missing)}`);
    }
  } finally {
    await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  }
};

let warehouseId = null;

const fillNew = async ({ name, host, db = "egpt_warehouse", port = "5532", schema = "", user = "ai", password = "ai", ssl = "prefer" }) => {
  await page.getByPlaceholder("e.g. Production DB").fill(name);
  await page.getByPlaceholder("warehouse.internal").fill(host);
  await page.getByPlaceholder("postgres").fill(db);
  await page.getByPlaceholder("5432").fill(port);
  if (schema) await page.getByPlaceholder("public").fill(schema);
  // The local Docker Postgres has no SSL; a real warehouse keeps the "Require" default.
  await page.locator("select").nth(1).selectOption(ssl);
  await page.getByPlaceholder("egpt_reader").fill(user);
  if (password) await page.getByPlaceholder("••••••••").fill(password);
};

await step("01-empty-list", async () => {
  // Armed before the navigation: on a warm server the list response can land before `goto`
  // resolves, and a wait registered afterwards would never see it.
  const listed = page.waitForResponse((r) => r.url().endsWith("/configure/sources") && r.request().method() === "GET");
  await page.goto(`${BASE}/configure/internal-sources`);
  await listed;
  await page.getByText("No sources connected").waitFor();
});

await step("02-form-filled", async () => {
  await page.getByRole("button", { name: "Add database" }).click();
  await fillNew({ name: "P4 UI warehouse", host: HOST, schema: "p4_ui" });
});

await step("03-test-connection-ok", async () => {
  const [created] = await Promise.all([
    page.waitForResponse((r) => r.url().endsWith("/configure/sources") && r.request().method() === "POST"),
    page.getByRole("button", { name: "Test Connection" }).click(),
  ]);
  console.log("   POST /configure/sources ->", created.status());
  warehouseId = (await created.json()).id;
  const testRes = await page.waitForResponse((r) => r.url().endsWith("/test") && r.request().method() === "POST");
  console.log("   POST .../test ->", testRes.status(), await testRes.text());
  await page.getByText("Connected", { exact: true }).waitFor();
  console.log("   form url now:", page.url());
});

await step("04-saved-building", async () => {
  const [run] = await Promise.all([
    page.waitForResponse((r) => r.url().includes("/workflows/harvest-source/runs")),
    page.getByRole("button", { name: "Save Changes" }).click(),
  ]);
  console.log("   POST /workflows/harvest-source/runs ->", run.status(), await run.text());
  await page.waitForURL(`${BASE}/configure/internal-sources`);
  // startInternalHarvest marks the card `building` before the redirect and the first poll is
  // 2s later, so this is not a race: a card that never shows it is a wiring bug.
  await page.getByText("Building model").first().waitFor({ timeout: 5000 });
});

await step("05-modelled", async () => {
  await page.getByText("Modelled", { exact: true }).first().waitFor({ timeout: 90_000 });
  // The banner fires on the polled building -> modelled transition, which step 04 guarantees
  // this run observed.
  await page.getByText("P4 UI warehouse is modelled", { exact: true }).waitFor({ timeout: 5000 });
  console.log("   last sync:", await page.locator("text=Last sync").locator("..").locator("p").nth(1).innerText());
});

await step("06-semantic-tables", async () => {
  // Two entry points once a build completes: the completion banner's and the card's.
  await page.getByRole("button", { name: "View semantic layer" }).last().click();
  const listing = await page.waitForResponse((r) => r.url().includes("/semantic/tables?source_id="));
  console.log("   GET /semantic/tables?source_id= ->", listing.status(), await listing.text());
  await page.getByRole("button", { name: /^Tables/ }).click();
  await page.getByText("store_visit").waitFor();
});

await step("07-loopback-refused", async () => {
  await page.goto(`${BASE}/configure/internal-sources/new`);
  await fillNew({ name: "P4 UI loopback probe", host: "127.0.0.1" });
  await page.getByRole("button", { name: "Test Connection" }).click();
  await page.getByText("Connection failed").waitFor();
  console.log("   detail:", await page.locator('[role="status"] p').nth(1).innerText());
});

await step("08-harvest-failed", async () => {
  await page.getByRole("button", { name: "Save Changes" }).click();
  await page.waitForURL(`${BASE}/configure/internal-sources`);
  await page.getByText("Failed", { exact: true }).waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: "Retry modelling" }).waitFor();
});

await step("09-validation-422", async () => {
  expectFailure(422, "POST", "/configure/sources");
  await page.goto(`${BASE}/configure/internal-sources/new`);
  await fillNew({ name: "No password", host: HOST, password: "" });
  await page.getByRole("button", { name: "Create Datasource" }).click();
  await page.getByRole("alert").waitFor();
  console.log("   alert:", await page.getByRole("alert").innerText());
});

await step("10-remove", async () => {
  await page.goto(`${BASE}/configure/internal-sources`);
  await page.getByText("P4 UI loopback probe").waitFor();
  page.once("dialog", (d) => d.accept());
  const card = page.locator("div.rounded-2xl", { hasText: "P4 UI loopback probe" });
  await card.getByLabel("More options").click();
  const [del] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === "DELETE"),
    card.getByRole("button", { name: "Remove" }).click(),
  ]);
  console.log("   DELETE ->", del.status());
  await page.getByText("P4 UI loopback probe").waitFor({ state: "detached" });
});

// A saved source edited through "Save Changes": the PATCH path the create-side steps never
// touch. Each edit is judged by re-opening the form, which reads `GET /configure/sources`.
await step("11-edit-existing", async () => {
  const editUrl = `${BASE}/configure/internal-sources/edit/${encodeURIComponent(warehouseId)}`;
  const stored = "Stored — enter a new one to replace it";
  const openEdit = async () => {
    await page.goto(editUrl);
    await page.getByPlaceholder("e.g. Production DB").waitFor();
  };
  const save = async () => {
    const [res] = await Promise.all([
      page.waitForResponse((r) => r.request().method() === "PATCH"),
      page.getByRole("button", { name: "Save Changes" }).click(),
    ]);
    await page.waitForURL(`${BASE}/configure/internal-sources`);
    return res;
  };
  const expectField = async (placeholder, value) => {
    const actual = await page.getByPlaceholder(placeholder).inputValue();
    if (actual !== value) throw new Error(`edit did not persist: ${placeholder} is "${actual}", expected "${value}"`);
  };

  // (a) A plain field edit needs no secret: rename, PATCH 200, still there after a re-fetch.
  await openEdit();
  await page.getByPlaceholder("e.g. Production DB").fill("P4 UI warehouse (renamed)");
  console.log("   rename -> PATCH", (await save()).status());
  await openEdit();
  await expectField("e.g. Production DB", "P4 UI warehouse (renamed)");

  // (b) A username edit with no new password cannot be a PATCH (the API keeps the username
  // inside the credential). The form must say so and send nothing, not save a no-op.
  await page.getByPlaceholder("egpt_reader").fill("ai_p4_renamed");
  const before = patches.length;
  await page.getByRole("button", { name: "Save Changes" }).click();
  const alert = await page.getByRole("alert").innerText();
  console.log("   username only ->", alert);
  if (!alert.includes("replaces the stored credential")) throw new Error(`unexpected alert: ${alert}`);
  if (patches.length !== before) throw new Error("a username-only save sent a PATCH");

  // (c) Username plus the password again rotates the credential, and the username persists.
  await page.getByPlaceholder(stored).fill("ai");
  console.log("   username + password -> PATCH", (await save()).status());
  await openEdit();
  await expectField("egpt_reader", "ai_p4_renamed");

  // Put the fixture back so a re-run of the harvest steps sees the credentials that work.
  await page.getByPlaceholder("egpt_reader").fill("ai");
  await page.getByPlaceholder(stored).fill("ai");
  await page.getByPlaceholder("e.g. Production DB").fill("P4 UI warehouse");
  await save();
  await openEdit();
  await expectField("egpt_reader", "ai");
});

await browser.close();

console.log("console errors:", JSON.stringify(consoleErrors, null, 1));
console.log("unexpected API responses >= 400:", JSON.stringify(failedApi, null, 1));
if (consoleErrors.length || failedApi.length) {
  console.error("FAILED: the flow passed its steps but the browser or API reported errors.");
  process.exitCode = 1;
} else {
  console.log("PASSED");
}
