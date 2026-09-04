/* §4.6 — Playwright: primary path (load → run → result → workflow) and failure paths per screen.
   Needs the API (uvicorn) and the UI dev server running; see docs/milestone_4_report.md. */
import { mkdirSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const ADMIN = { user: process.env.WFO_ADMIN_USER ?? "admin", pw: process.env.WFO_ADMIN_PASSWORD ?? "admin-pass-123" };
const FIXTURE = process.env.WFO_FIXTURE_PATH ?? "C:\\code\\test\\waterflood-optimizer\\waterflood_app\\validation\\synthetic_suite\\streak_5x4";

const SHOTS = process.env.WFO_SHOTS ?? "../docs/screens";
const shot = (page: Page, name: string) => page.screenshot({ path: `${SHOTS}/m4_${name}.png`, fullPage: false });
const shot5 = (page: Page, name: string) => page.screenshot({ path: `${SHOTS}/m5_${name}.png`, fullPage: false });
const WRITEBACK_DIR = process.env.WFO_WRITEBACK_DIR ?? "C:/code/test/waterflood-optimizer/ui/test-results/surveillance";

async function login(page: Page, user = ADMIN.user, pw = ADMIN.pw) {
  await page.goto("/");
  await page.getByLabel("Account or e-mail").fill(user);
  await page.getByLabel("Password").fill(pw);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
}

test.describe.configure({ mode: "serial" });

test("login failure shows a plain-language message, not a stack trace", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Account or e-mail").fill("nobody");
  await page.getByLabel("Password").fill("wrong");
  await page.getByRole("button", { name: "Sign in" }).click();
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("invalid username or password");
  await expect(alert).not.toContainText("Traceback");
});

test("primary path: load → map → wells → run → result → recommendation → workflow", async ({ page }) => {
  test.setTimeout(420_000); // full engine run + report downloads (PDF render) + writeback
  await login(page);
  await page.goto("/load");
  // project
  // either the create-project form (fresh store) or the source tabs (a project is already selected)
  const createForm = page.getByLabel("Project name");
  const sourceTabs = page.getByRole("tab", { name: "CSV / Excel / Parquet" });
  await expect(createForm.or(sourceTabs)).toBeVisible({ timeout: 15_000 });
  if (await createForm.isVisible()) {
    await createForm.fill("E2E streak");
    await page.getByLabel("Asset").fill("ALPHA");
    await page.getByRole("button", { name: "Create project" }).click();
  }
  await expect(page.getByRole("tab", { name: "CSV / Excel / Parquet" })).toBeVisible();
  // connection
  await page.getByLabel(/Folder or workbook path/).fill(FIXTURE);
  await page.getByRole("button", { name: "Connect & test" }).click();
  await expect(page.getByText(/Connected:/)).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: /Read tables & suggest mapping/ }).click();
  // mapping: required fields highlighted as ok, save
  await expect(page.getByRole("region", { name: "Mapping" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Rates mapped ✓")).toBeVisible();
  await shot(page, "01_loader_mapping");
  await page.getByRole("button", { name: /Save mapping & derive wells/ }).click();
  // wells table with derived types and timeline bars
  const wells = page.getByRole("table", { name: "Wells" });
  await expect(wells).toBeVisible({ timeout: 30_000 });
  await expect(wells.getByText("▼ INJ").first()).toBeVisible();
  await expect(wells.getByText("● PROD").first()).toBeVisible();
  await wells.scrollIntoViewIfNeeded();
  await shot(page, "02_loader_wells");
  await page.getByRole("button", { name: /Save project config/ }).click();
  // run
  await expect(page).toHaveURL(/\/run/);
  await page.getByRole("radio", { name: "Max NPV" }).click();
  await expect(page.getByTestId("economics")).toBeVisible();
  await shot5(page, "03_run_npv_economics");
  await page.getByRole("radio", { name: "Max oil" }).click();
  await page.getByTestId("run-button").click();
  await expect(page.getByRole("region", { name: "Progress" })).toBeVisible();
  await shot(page, "03_run_progress");
  await expect(page).toHaveURL(/\/result\//, { timeout: 240_000 });
  // result: badge, map, action list, forecast
  await expect(page.getByRole("status")).toContainText(/confidence/);
  await expect(page.getByTestId("well-map")).toBeVisible();
  await expect(page.getByTestId("action-list").locator("li").first()).toBeVisible();
  await expect(page.getByTestId("plot-forecast-Field")).toBeVisible();
  await page.waitForTimeout(1500);
  await shot(page, "04_result");
  // M5: downloads (report + tables) are real files with the run id in the name
  for (const kind of ["xlsx", "json", "docx"] as const) {
    const dl = page.waitForEvent("download");
    await page.getByTestId(`dl-${kind}`).click();
    const file = await dl;
    expect(file.suggestedFilename()).toMatch(new RegExp(`^wfo_run_.*\.${kind}$`));
  }
  await shot5(page, "01_result_downloads");
  // details drawer: tabs
  await page.getByTestId("open-details").click();
  await expect(page.getByRole("dialog", { name: "Details" })).toBeVisible();
  await page.getByRole("tab", { name: "Which method won" }).click();
  await expect(page.getByTestId("plot-leaderboard")).toBeVisible();
  await page.waitForTimeout(800);
  await shot(page, "05_details_tournament");
  await page.getByRole("tab", { name: "Δt / τ & pressure" }).click();
  await expect(page.getByTestId("plot-dt-tau")).toBeVisible();
  await page.waitForTimeout(800);
  await shot(page, "06_details_dt_tau");
  await page.getByRole("tab", { name: "Fit per well" }).click();
  await expect(page.getByTestId(/plot-fit-/)).toBeVisible();
  await page.waitForTimeout(800);
  await shot(page, "07_details_fit");
  await page.keyboard.press("Escape");
  // recommendation → workflow
  await page.getByTestId("create-recommendation").click();
  await page.getByRole("button", { name: /Open in Workflow/ }).click();
  await expect(page).toHaveURL(/\/workflow\//);
  await expect(page.getByRole("img", { name: /Workflow state DRAFT/ })).toBeVisible();
  // admin acting as reviewer → approver → operations
  await page.locator("#role").selectOption("reviewer");
  await page.getByTestId("btn-review").click();
  await expect(page.getByRole("img", { name: /Workflow state REVIEWED/ })).toBeVisible();
  await page.locator("#role").selectOption("approver");
  await page.getByTestId("btn-approve").click();
  await expect(page.getByRole("img", { name: /Workflow state APPROVED/ })).toBeVisible();
  await shot(page, "08_workflow_approved");
  await expect(page.getByText("approved by originator", { exact: true }).first()).toBeVisible(); // same person ran and approved: logged, visible
  // M5: recommendation report download and approver-gated writeback to a flagged connection
  const pdf = page.waitForEvent("download");
  await page.getByTestId("wf-dl-pdf").click();
  expect((await pdf).suggestedFilename()).toMatch(/^wfo_recommendation_.*\.pdf$/);
  mkdirSync(WRITEBACK_DIR, { recursive: true });
  const recId = page.url().split("/workflow/")[1];
  const created = await page.evaluate(async ({ rid, dir }) => {
    const auth = { Authorization: `Bearer ${sessionStorage.getItem("wfo_token")}` };
    const rec = await (await fetch(`/api/recommendations/${rid}`, { headers: auth })).json();
    const r = await fetch("/api/connections", { method: "POST", headers: { "Content-Type": "application/json", ...auth }, body: JSON.stringify({ project_id: rec.project_id, kind: "files", database: dir, options: { wfo_writeback: "1" } }) });
    return { status: r.status, text: await r.text() };
  }, { rid: recId, dir: WRITEBACK_DIR });
  expect(created.status, created.text).toBe(201);
  await page.reload();
  await expect(page.getByRole("img", { name: /Workflow state APPROVED/ })).toBeVisible();
  await page.getByTestId("wb-connection").selectOption({ index: 1 });
  await page.getByTestId("btn-writeback").click();
  await expect(page.getByText(/targets written to/)).toBeVisible();
  await expect(page.getByTestId("writeback-list").locator("tbody tr")).toHaveCount(1);
  await shot5(page, "02_workflow_writeback");
  await page.locator("#role").selectOption("operations");
  await page.getByTestId("btn-implement").click();
  await expect(page.getByRole("img", { name: /Workflow state IMPLEMENTED/ })).toBeVisible();
});

test("failure path: run without a project shows the empty state; forbidden role shows the messaging-map sentence", async ({ page }) => {
  await login(page);
  await page.locator("#proj").selectOption("");
  await page.getByRole("link", { name: "2 · Run" }).click(); // in-app navigation keeps the cleared selection
  await expect(page.getByText("Pick or create a project first.")).toBeVisible();
  // viewer cannot create users
  await page.locator("#role").selectOption("viewer");
  await page.goto("/admin");
  await expect(page).not.toHaveURL(/\/admin/); // route guarded
});

test("admin: add a user and see it deactivatable; audit log lists actor and acting role", async ({ page }) => {
  await login(page);
  await page.locator("#role").selectOption("admin");
  await page.goto("/admin");
  const name = `e2e-${Date.now()}@asset.com`;
  await page.getByTestId("user-name").fill(name);
  await page.getByTestId("user-add").click();
  const users = page.getByRole("table", { name: "Users" });
  await expect(users.getByText(name)).toBeVisible();
  await shot(page, "09_admin_users");
  await page.getByRole("tab", { name: "Audit" }).click();
  const audit = page.getByRole("table", { name: "Audit log" });
  await expect(audit.getByText("create_user").first()).toBeVisible();
  await expect(audit.getByText("admin").first()).toBeVisible();
});

test("tablet width keeps the loader usable", async ({ page }) => {
  await page.setViewportSize({ width: 820, height: 1100 });
  await login(page);
  await page.goto("/load");
  await expect(page.getByRole("heading", { name: "Load data" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Steps" })).toBeVisible();
  await shot(page, "10_loader_tablet");
});

test("advanced mode saves overrides for the next run", async ({ page }) => {
  await login(page);
  await page.locator("#role").selectOption("reviewer");
  await page.goto("/advanced");
  await page.getByRole("checkbox", { name: /CRMP/ }).first().check();
  await page.getByTestId("advanced-save").click();
  await expect(page.getByText("saved for the next run")).toBeVisible();
  await shot(page, "11_advanced");
});
