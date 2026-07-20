# Consilium — Business-Realism Lens
## A believable mid-size QA organization for the analyzer-ng demo corpus

**Lens:** business realism. The corpus must read like a real team's ReportPortal instance —
recognizable cadence, human triage habits, bugs with lifecycles — not statistically flat noise.
Style is anchored in the user's existing `rp_demo_upload.py` demo generator (hawkins-lab.dev
universe, markdown step names, `[STEP]`/`[API]` log prefixes, attribute plan, post-finish triage
with human-sounding comments). Everything below extends that universe rather than inventing a new one.

---

## 1. The organization

**Hawkins Retail Group** — a mid-size e-commerce company running "Hawkins Shop"
(storefront web app, back-office admin, and a payments/services platform). One QA guild,
three delivery orgs, each with its own ReportPortal project. Shared infra:
`ci.hawkins-lab.dev` (Jenkins), `git.hawkins-lab.dev` (GitLab), `wiki.hawkins-lab.dev`,
environments `staging.hawkins-lab.dev` and `prodmirror.hawkins-lab.dev`.

**Environments**

| env attr | What it is | Quirk the data exploits |
|---|---|---|
| `staging` | nightly + per-PR target, shared, occasionally unstable | infra outage day, proxy flakiness → `si` |
| `prod-mirror` | weekly full e2e target, prod feature-flag state | flags OFF there → expected failures triaged `nd` |

**People** (test authors / triagers — reused across projects, consistent with the existing script):
Jane Hopper, Dustin Henderson, Steve Harrington (webshop-ui);
Nancy Wheeler, Robin Buckley, Max Mayfield (frontend-apps);
Jim Hopper, Joyce Byers, Lucas Sinclair, Erica Sinclair (payments-services);
Murray Bauman (SDET/infra, triages `si`/`ab` across all projects).

**Sprints:** `2026.06-S2` (Jun 15–26), `2026.07-S1` (Jun 29–Jul 10), `2026.07-S2` (Jul 13–24).
History window: **Mon Jun 22 → Fri Jul 17, 2026** (4 working weeks, weekend gaps real).

---

## 2. The three ReportPortal projects

### 2.1 `webshop-ui` — Java 17 · TestNG · Selenium 4 (back-office web UI)

The direct sibling of the existing demo generator; reuse its idioms verbatim.

- **Launch names** (one per component area, exactly like `UI Regression - Project Settings`):
  - `UI Regression - Catalog` (nightly, 48–56 items)
  - `UI Regression - Checkout` (nightly, 60–70 items)
  - `UI Regression - Customer Accounts` (nightly, 34–40 items)
  - `UI Smoke - Back Office` (per-PR, 12 items, stable list)
- **Suite structure inside a launch:** SUITE per screen (`Catalog > Product editor`,
  `Checkout > Tax & totals`, …), STEP items with markdown names:
  `**Step 3:** Select value '5' as Member <br />**Expected Result:** Value is applied…`
  plus a passed `Precondition: …` nested step first — same as the generator.
- **Test-name convention:** sentence-style, area-prefixed, stable across runs (critical for
  launch grouping/history): `Checkout. Tax & totals. Recalculate VAT when shipping country changes`.
- **Item attributes:** exactly 4 per STEP — `area` / `priority` / `author` / `jira_id`
  (`EPMRPP-…` range continues; keep the plan-guard idea).
- **Launch attributes:** `env`, `version=5.14.0→5.15.0`, `build=5.14.0-b243…`, `type=regression|smoke`,
  `component`, `browser=chrome-126`, `platform=linux`, `team=qa-platform`, `sprint`.
- **Launch description:** markdown with CI job link, commit range diff link, test-plan wiki link —
  clone `launch_description()` format.
- **Log style before failures:** 2–4 `info`/`debug` lines (`[STEP] …`, `Attempt to find element
  by By.xpath: …`), a `warn` retry line for flaky ones, then the `error` with a full Java stack
  (AssertionError with Expected/but, or Selenium exception + Session info block). Screenshot
  attachment on ~60% of UI failures (`flower.jpg`, `dog.jpeg`).

### 2.2 `frontend-apps` — Playwright TS (storefront SPA) + Cypress (legacy admin)

A different org with different reporting DNA — this is what makes the corpus feel multi-team.

- **Launches:**
  - `PW E2E - Storefront - nightly` (38–44 items; projects chromium+webkit as suites)
  - `PW Smoke - Storefront` (per-PR, 10 items)
  - `Cypress Regression - Legacy Admin` (weekly Wed, 24–28 items)
- **Test-name convention:** spec-path style, deliberately unlike webshop-ui:
  `checkout.spec.ts > guest checkout > pays with saved card`,
  `admin/orders.cy.js > bulk refund > shows confirmation toast`.
- **Failure texture:** Playwright expect timeouts
  (`Timed out 5000ms waiting for expect(locator).toBeVisible()` + locator + call log),
  `page.goto: net::ERR_CONNECTION_REFUSED`, trace-viewer link line in an `info` log,
  screenshot + `trace.zip`-named attachment. Cypress: `AssertionError: expected '…' to contain '…'`
  + command log excerpt. JS stacks, not Java — the analyzer must not lean on one stack dialect.
- **Attributes:** `team=web-storefront`, `browser=chromium-137|webkit`, `release=2.31.x`,
  `flaky-quarantine=true` on quarantined specs (a real-team habit).
- **Authors sign specs** via item attribute `author`; Robin owns checkout, Nancy owns catalog/search,
  Max owns admin Cypress.

### 2.3 `payments-services` — .NET 8 · xUnit · RestSharp (API/microservices)

- **Launches:**
  - `API Regression - Payments Services` (nightly, 72–80 items — the big one)
  - `API Contract - payment-gateway` (per-PR, 14–16 items)
  - `Integration E2E - Money Movement` (weekly Fri on `prod-mirror`, 18–22 items)
- **Test-name convention:** namespace style, again distinct:
  `Hawkins.Payments.Ledger.Tests.TransferApiTests.Post_Transfer_WithIdempotencyKey_Returns201`.
- **Failure texture:** `Xunit.Sdk.EqualException` / `Assert.Equal() Failure` diffs with JSON bodies,
  `System.Net.Http.HttpRequestException: Connection refused (fx-rates:8443)`,
  .NET stack frames (`at Hawkins.Payments.Ledger.Tests…`), request/response `debug` logs
  (`POST /v1/transfers -> 500 in 1240 ms`, correlation-id lines). Attachments: response JSON files.
- **Attributes:** `team=payments-core`, `service=ledger|payment-gateway|fx-rates`, `dotnet=8.0`,
  `version=8.3.x`.

---

## 3. Run cadence (what a week looks like)

| Series | Trigger | Days | Items | Typical outcome |
|---|---|---|---|---|
| webshop-ui 3 nightlies | 02:00 cron | Mon–Fri (+Sun for Checkout) | 34–70 | 88–96% pass |
| `UI Smoke - Back Office` | per-PR merge | ~3/working day | 12 | ~85% of launches fully green |
| `PW E2E - Storefront - nightly` | 03:00 cron | Mon–Sat | 38–44 | 90–97% pass (worse in flaky week) |
| `PW Smoke - Storefront` | per-PR | ~4/working day | 10 | ~90% fully green |
| `Cypress Regression - Legacy Admin` | Wed 05:00 | weekly | 24–28 | 85–92% pass |
| `API Regression - Payments Services` | 01:30 cron | Mon–Sat | 72–80 | 93–98% pass |
| `API Contract - payment-gateway` | per-PR | ~2/working day | 14–16 | ~92% fully green |
| `Integration E2E - Money Movement` | Fri 06:00 | weekly, `prod-mirror` | 18–22 | 80–90% pass; `nd` heavy |

**Totals over 4 weeks:** ≈ 105 scheduled launches + ≈ 150 per-PR smokes ≈ **255 launches,
~7,500 test items, ~700 failed items**. Weekends: only Sat nightlies for PW/API, nothing Sunday
except the Checkout nightly — real gaps, not a uniform grid. Per-PR smokes cluster 10:00–18:00
with a lunchtime dip; nightlies keep fixed cron times ±3 min.

---

## 4. The 4-week history arc (the part that must look human)

### Week 1 — Jun 22–26 · baseline with chronic pain (`2026.06-S2` closing)
- **Chronic PB #1:** `Checkout. Tax & totals. Recalculate VAT when shipping country changes` —
  fails every Checkout nightly with a stable AssertionError (`Expected: 21.00 but: 21.01`).
  Triaged `pb` on Tue with comment "Rounding at line-item level, EPMRPP-91204, fix in 5.14.1",
  then re-triaged in ~30s each following morning (same person, same comment style) —
  chronic failures get fast, low-effort triage; this is the analyzer's bread and butter.
- **Chronic AB pair:** two Catalog tests share one `StaleElementReferenceException` signature
  (product list re-render race) — reuse the generator's `AB_FAILURE` pattern verbatim, `ab`,
  comment "Tracked as AUTO-1231".
- Payments: 2–4 scattered fresh failures/night, triaged next morning (`pb` or `ab`),
  1–2 always left `ti` (real teams never triage 100%).

### Week 2 — Jun 29–Jul 3 · fix lands + **infra outage day** (`2026.07-S1`)
- **The fix:** VAT rounding fixed in `5.14.0-b252` (Mon Jun 29). Failure *stops cold* — the test
  passes for the rest of history. A believable "bug disappears from the stream" event.
- **INFRA OUTAGE — Wed Jul 1:** staging ingress + Selenium Grid node pool down 02:00–09:40
  (`INFRA-4412`, echoing the generator's ticket). Effects, all same morning:
  - webshop-ui nightlies: 40–60% of items fail with `HttpHostConnectException`/
    `SessionNotCreatedException` — mass-triaged `si` in ONE batch at 09:55 by Murray, identical
    comment "Staging outage 07-01, INFRA-4412" (bulk defect_update burst, one actor, one minute).
  - PW nightly: `net::ERR_CONNECTION_REFUSED` on ~50% of specs → `si`.
  - API nightly ran at 01:30 *before* the outage window → only 6 gateway-adjacent tests fail
    (partial blast radius — outages are never perfectly correlated).
  - 3–4 items of the outage are *left* `ti` (missed in the sweep) and 2 are mistriaged `ab`
    then corrected to `si` on Thu — genuine relabel feedback events.
- Cypress weekly (Wed 05:00) lands mid-outage: 70% fail, launch later force-marked, `si` batch.

### Week 3 — Jul 6–10 · **flaky period** + **repeat regressor**
- **Flaky period (PW):** Vite upgrade in storefront `release/2.31.0` introduces a dev-server
  warm-up race. Jul 6–10, 4–6 random PW specs/night fail with
  `Timed out 5000ms waiting for expect(locator).toBeVisible()` on *different* specs each night;
  retries rescue about half (retry items, like the generator's `retry_planned`). Triage drifts:
  first day `ti`→`ab` individually, by Thu Robin quarantines 3 specs
  (`flaky-quarantine=true`) and comments "Vite warm-up race, AUTO-1290". Ends Fri with the
  pinned-server fix — flakiness stops.
- **Repeat regressor (payments):** `…FxRates…Get_Rate_ForHistoricalDate_UsesClosingRate` —
  failed and was fixed pre-history, **regresses Tue Jul 7** (same `EqualException`, off-by-one-day
  date), triaged `pb` "REGRESSION of EPMRPP-90731, reopened"; fixed Thu Jul 9; **regresses again
  Wed Jul 15** with the *same signature* — left `ti` in the final days (perfect showcase:
  history says pb with high confidence).
- Weekly `Integration E2E` on prod-mirror: 3 stable failures because `instant-payouts` flag is OFF
  there — triaged `nd`, comment "Flag off on prod-mirror by design". Repeats every Friday all month
  (a consistent nd failure-mode cluster).

### Week 4 — Jul 13–17 · fresh blood, left for the demo (`2026.07-S2`)
- Everything chronic continues (AB stale pair still failing — now left `ti` in the last 2 days,
  as the generator does for its live demo).
- **Fresh regressions, untriaged (`ti`) in the last 1–2 launches of each project:**
  - webshop-ui: new `NoSuchElementException` after a DOM refactor of the order-status badge
    (history contains the *analogous* badge-refactor `ab` from Week 1 → retrieval should suggest ab).
  - PW: new checkout failure `expected '€ 41,90' to be '€ 41.90'` — *lexically close* to the
    fixed Week-1 VAT `pb` but a locale bug, not rounding: probes discrimination limits;
    a correct system should abstain or suggest pb with modest confidence.
  - payments: the Jul 15 fx-rates re-regression (strong pb prior) + one genuinely novel
    `NullReferenceException` never seen before (correct answer: abstain).
- Thu Jul 16 morning: a triage sweep by Jane/Jim/Nancy handles ~70% of the week's `ti` backlog
  in a 20-minute window each (defect_update events cluster into human-shaped bursts, not uniform).

---

## 5. Defect-label economics (end-state over the whole corpus)

| Label | Share of failed items | Comes from |
|---|---|---|
| `pb` | ~30% | chronic VAT bug, repeat regressor, scattered real bugs |
| `ab` | ~25% | stale-element pair, locator breaks, flaky-week PW specs |
| `si` | ~20% | outage day (bulk), proxy timeouts, grid session errors |
| `nd` | ~5% | prod-mirror feature-flag failures, one "test data expired" case |
| `ti` (never triaged) | ~20% | tail of every busy day + all of the final-two-days failures |

Triage comments are short, reference tickets (`EPMRPP-…`, `AUTO-…`, `INFRA-…`), and are
near-identical for repeat triage of the same failure — copy the generator's comment voice
("Known flaky locator: stale element after settings re-render. Tracked as AUTO-1231.").

---

## 6. Realism guardrails (things that would give the demo away)

1. **Never 100% triage.** Real backlogs leak; keep the ~20% `ti` tail and a few
   outage items forgotten forever.
2. **Stable test names across launches** — grouping/history breaks on renamed items; only the
   two deliberate "refactor renamed the test" events (1 per project, mid-history) may rename.
3. **Human-shaped timestamps:** cron nightlies at fixed times, triage in weekday-morning bursts,
   bulk `si` sweep in one minute, no activity Sat/Sun except scheduled runs.
4. **Failures stop when fixed** — a fixed bug must vanish from the stream the same build the
   comment promised ("fix in 5.14.1" must actually land).
5. **Pass/fail drift, not jitter:** nightly failed-counts should walk (12→10→9→7→8) like the
   generator's `RUNS`, not bounce randomly.
6. **Three log dialects** (Java/JS/.NET) with correct stack shapes, ports, service names
   consistent per project — one shared vocabulary would make template mining trivially easy.
7. **Every failure is preceded by 2–4 plausible `info`/`debug` lines** and often a `warn`
   retry — never a bare error log (this is the generator's strongest realism habit).
8. **Versions/builds monotonically increase** per project and appear in both launch attributes
   and descriptions; commit-range links change every run.

---

## 7. What this lens hands the other consilium seats

- Per-launch failure *content* (exact stack corpora, template variants, near-miss pairs) —
  signal-design lens.
- Exact per-day launch manifest + item-level status matrices — generator-implementation lens
  (recommend extending `rp_demo_upload.py`'s `RUNS`/`_failing()` scheme to a
  date-keyed manifest per project).
- Ground-truth labels for scoring analyzer suggestions (which `ti` items have a "correct"
  historical answer vs. genuine abstain cases) — evaluation lens.
