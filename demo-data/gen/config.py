"""Static configuration for the analyzer-ng demo-data generator.

Everything here is derived from demo-data/SCENARIOS.md. Where SCENARIOS.md is
ambiguous the choice that maximises analyzer-mechanism coverage is taken; those
choices are catalogued in README.md "Deviations".
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
SEED = 20260717  # fixed seed constant -> re-runs reproduce the same corpus

# ---------------------------------------------------------------------------
# RP endpoints (overridable on the CLI)
# ---------------------------------------------------------------------------
UAT_BASE = "http://localhost:9999"
API_BASE = "http://localhost:8585"
# password grant, basic auth client "ui:uiman" (see deploy/.../_lib.sh)
OAUTH_BASIC = "dWk6dWltYW4="
SUPERADMIN = "superadmin"
SUPERPW = "superadmin"

# ---------------------------------------------------------------------------
# Projects (SCENARIOS.md §1). RP lowercases/normalises project names into the
# URL slug; we keep names already slug-safe.
# ---------------------------------------------------------------------------
WSU = "webshop-ui"
PSV = "payments-services"
FEA = "frontend-apps"
PROJECTS = [WSU, PSV, FEA]

FRAMEWORK_PROJECT = {
    "java-selenium-testng": WSU,
    "dotnet-xunit-restsharp": PSV,
    "playwright-ts": FEA,
    "cypress": FEA,
}

# RP built-in defect sub-type locators. The five ground-truth labels map onto
# the five default groups; no scenario needs a custom sub-type (see README).
ISSUE_LOCATOR = {
    "pb": "pb001",  # product_bug
    "ab": "ab001",  # automation_bug
    "si": "si001",  # system_issue
    "nd": "nd001",  # no_defect
    "ti": "ti001",  # to_investigate  (== abstain / not yet triaged)
}

# ---------------------------------------------------------------------------
# Timeline (SCENARIOS.md §3). Simulated window Jun 29 -> Jul 17 2026 plus a
# backdated pre-history pack. Dates are (year, month, day). Cron hour is when
# the launch "runs" (used only to order timestamps sensibly).
# ---------------------------------------------------------------------------

# Phase 0 backdated pre-history (§3.0)
BACKDATED_DAYS = [
    ("2025-12-18", WSU),
    ("2026-01-15", WSU),
    ("2026-02-15", WSU),
    ("2026-02-20", PSV),
    ("2026-03-15", WSU),
    ("2026-04-15", WSU),
    ("2026-04-20", PSV),
    ("2026-05-15", WSU),
    ("2026-05-20", FEA),
]


# Working timeline (§3.1). Each day lists, per project, the launches that run
# and the nightly failed-count hint from the table (used as a soft floor for
# filler; the real volume is corpus-driven -- see README Deviations).
#   day -> {project: [(launch_name, failed_hint), ...]}
def _wsu_nightlies():
    return [
        ("Nightly - Checkout", 0),
        ("Nightly - Catalog", 0),
        ("Nightly - Account & Payments", 0),
    ]


def _wsu_smokes(n):
    return [(f"PR Smoke #{i + 1}", 0) for i in range(n)]


TIMELINE = [
    # (date, weekday_label, phase-of-history)
    ("2026-06-29", "Mon Jun 29"),
    ("2026-06-30", "Tue Jun 30"),
    ("2026-07-01", "Wed Jul 1"),
    ("2026-07-02", "Thu Jul 2"),
    ("2026-07-03", "Fri Jul 3"),
    ("2026-07-04", "Sat Jul 4"),
    ("2026-07-05", "Sun Jul 5"),
    ("2026-07-06", "Mon Jul 6"),
    ("2026-07-07", "Tue Jul 7"),
    ("2026-07-08", "Wed Jul 8"),
    ("2026-07-09", "Thu Jul 9"),
    ("2026-07-10", "Fri Jul 10"),
    ("2026-07-11", "Sat Jul 11"),
    ("2026-07-12", "Sun Jul 12"),
    ("2026-07-13", "Mon Jul 13"),
    ("2026-07-14", "Tue Jul 14"),
    ("2026-07-15", "Wed Jul 15"),
    ("2026-07-16", "Thu Jul 16"),
    ("2026-07-17", "Fri Jul 17"),
]

# History window = Jun 29 .. Jul 14 (Phase 2). Probe window = Jul 15..17 (Phase 3).
HISTORY_DAYS = [d for d, _ in TIMELINE if "2026-06-29" <= d <= "2026-07-14"]
PROBE_DAYS = ["2026-07-15", "2026-07-16", "2026-07-17"]
OUTAGE_DAY = "2026-07-01"
DEMO_DAY = "2026-07-17"
SWEEP_DAY = "2026-07-16"


# Per-project nightly launch templates used to build the launch skeleton on a
# given working day.
def day_launches(project: str, date: str):
    """Return [(launch_name, failed_hint)] for a project on a working day."""
    dow = _dow(date)
    weekend = dow in (5, 6)  # Sat/Sun
    if project == WSU:
        if date in ("2026-07-04", "2026-07-11"):
            return []  # Sat: no WSU
        if date in ("2026-07-05", "2026-07-12"):
            return [("Nightly - Checkout", 0)]  # Sun: Checkout only
        launches = _wsu_nightlies() + _wsu_smokes(2)
        return launches
    if project == PSV:
        base = [("API Regression - Nightly", 0)]
        if weekend:
            return base
        base += [("Contract - Ledger Service", 0)]
        if date not in ("2026-06-29",):
            base += [("Contract - Transfers Service", 0)]
        if date in ("2026-07-03", "2026-07-10", "2026-07-17"):
            base += [("Integration E2E - Money Movement", 0)]
        return base
    if project == FEA:
        base = [("PW E2E - Storefront Nightly", 0)]
        if weekend:
            return base
        base += [("PW Smoke - Storefront", 0)]
        if date in ("2026-07-01", "2026-07-08"):
            base += [("Cypress Weekly - Legacy Admin", 0)]
        return base
    return []


def _dow(date: str) -> int:
    import datetime as _dt

    y, m, d = (int(x) for x in date.split("-"))
    return _dt.date(y, m, d).weekday()
