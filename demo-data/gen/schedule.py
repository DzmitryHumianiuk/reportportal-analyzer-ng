"""Compile the corpus + SCENARIOS.md timeline into a concrete upload plan.

A Plan is an ordered list of PlannedLaunch objects, each tagged with a phase
(0..5), a project, a simulated date, and a list of PlannedItem objects. Items
carry a role: H (history -> replay a defect_update to its ground-truth label),
P (probe -> stays To-Investigate, gets analyzed), DECOY (passes).

Placement policy per archetype lives in POLICY below and is derived from
SCENARIOS.md §2/§3/§4. Where the spec's per-day counts (§3.1) contradict its
roll-up (§3.2), volume is corpus-driven (see README "Deviations").
"""
from __future__ import annotations

import datetime as dt
import random
import zlib
from dataclasses import dataclass, field
from typing import Optional

from . import config
from .corpus import Archetype, LogRow, RenderedItem, render_item

# roles
H = "H"       # history item -> defect_update replay to ground truth
P = "P"       # probe item -> analyzed, stays ti until analyzer decides
DECOY = "D"   # passing decoy (status passed)


@dataclass
class PlannedItem:
    item: RenderedItem
    role: str


@dataclass
class PlannedLaunch:
    project: str
    name: str
    date: str          # YYYY-MM-DD (simulated)
    hour: int
    phase: int
    attributes: list[dict]
    items: list[PlannedItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-archetype placement policy.
#   target   : total concrete items to emit (variants are cycled to reach it)
#   probes   : how many of those are probe (P) items on probe days
#   backdate : how many are emitted into the Phase-0 pre-history pack
#   outage   : force history items onto the Jul 1 outage day
#   cold     : FEA archetype whose history is analyzed cold in Phase 1 (Jun 29)
#   also_fea : also emit a byte-identical long-tail copy into frontend-apps (S07)
# Anything omitted defaults to target=len(variants), probes=0.
# ---------------------------------------------------------------------------
POLICY: dict[str, dict] = {
    # --- webshop-ui (java) -------------------------------------------------
    "JAVA-SEL-01": dict(target=9, probes=3),                 # S09 chronic VAT
    "JAVA-SEL-02": dict(target=18, probes=4),                # S12 stale pair
    "JAVA-SEL-03": dict(target=17, probes=12),               # S05 param variants
    "JAVA-SEL-04": dict(target=60, probes=0, outage=True),   # S34 grid outage
    "JAVA-SEL-05": dict(target=12, probes=3),                # S16.A slow-query pb
    "JAVA-SEL-06": dict(target=12, probes=3),                # S16.B pool si
    "JAVA-SEL-07": dict(target=3, probes=3),                 # S16.C bare -> abstain
    "JAVA-SEL-08": dict(target=22, probes=6),                # S15 wrapper chain
    "JAVA-SEL-09": dict(target=20, probes=4),                # S18.B locator pair
    "JAVA-SEL-10": dict(target=16, probes=4),                # S19 div0
    "JAVA-SEL-11": dict(target=10, probes=3),                # S19 tax NPE
    "JAVA-SEL-12": dict(target=20, probes=12),               # S37 npe look-alikes
    "JAVA-SEL-13": dict(target=10, probes=5),                # S03 filth twin
    "JAVA-SEL-14": dict(target=6, probes=3),                 # S01 noise-heavy
    "JAVA-SEL-15": dict(target=3, probes=3),                 # S04 pathological
    "JAVA-SEL-16": dict(target=14, probes=4),                # S23.A spam coupon
    "JAVA-SEL-17": dict(target=8, probes=3),                 # S23.B spam db
    "JAVA-SEL-18": dict(target=24, probes=6),                # S24 causal chains
    "JAVA-SEL-19": dict(target=32, probes=8),                # S20 multilingual
    "JAVA-SEL-20": dict(target=10, probes=4),                # S21.B homoglyph
    "JAVA-SEL-21": dict(target=4, probes=2),                 # S21.C rtl/emoji
    "JAVA-SEL-22": dict(target=14, probes=4),                # S22 injection ui
    "JAVA-SEL-23": dict(target=18, probes=4),                # S29 proxy 502
    "JAVA-SEL-24": dict(target=14, probes=3),                # S13 fp drift
    "JAVA-SEL-25": dict(target=10, probes=2),                # S14 declined swap
    "JAVA-SEL-26": dict(target=10, probes=2),                # S17 assertion val
    "JAVA-SEL-27": dict(target=18, probes=2),                # S11c/S32 conflict
    "JAVA-SEL-28": dict(target=3, probes=1, backdate=2),     # S11b stale guard
    "JAVA-SEL-29": dict(target=5, probes=2),                 # S26a rare token
    "JAVA-SEL-30": dict(target=60, probes=10, backdate=8, also_fea=True),  # S07 misc/long-tail
    "JAVA-SEL-31": dict(target=10, probes=4),                # S45.A healthcheck (WSU half)
    # --- payments-services (dotnet) ----------------------------------------
    "NET-XUN-01": dict(target=9, probes=2, backdate=2),      # S10 fx-rates episodes
    "NET-XUN-02": dict(target=10, probes=3),                 # S18.A 500 npe
    "NET-XUN-03": dict(target=10, probes=3),                 # S18.A 503 unavailable
    "NET-XUN-04": dict(target=9, probes=3),                  # S25 idempotency
    "NET-XUN-05": dict(target=6, probes=2),                  # S33 conn refused
    "NET-XUN-06": dict(target=3, probes=1),                  # S33 db deadlock
    "NET-XUN-07": dict(target=3, probes=1),                  # S33 429 throttle
    "NET-XUN-08": dict(target=9, probes=3),                  # S41 flag off nd
    "NET-XUN-09": dict(target=6, probes=0, outage=True),     # S34 gateway-adjacent
    "NET-XUN-10": dict(target=11, probes=3),                 # S30 kb candidate
    "NET-XUN-11": dict(target=5, probes=2),                  # S22.C injection
    "NET-XUN-12": dict(target=5, probes=2),                  # S22.D injection
    "NET-XUN-13": dict(target=10, probes=4),                 # S45.A healthcheck (PSV half)
    "NET-XUN-14": dict(target=6, probes=6),                  # S45.B kb-borrow probe
    "NET-XUN-15": dict(target=2, probes=2),                  # S39c novel nullref
    "NET-XUN-16": dict(target=15, probes=6),                 # S27/S38 scope+mixed
    # --- frontend-apps (playwright + cypress) ------------------------------
    "TS-PW-01": dict(target=20, probes=6),                   # S40 flaky week
    "TS-PW-02": dict(target=20, probes=0, outage=True),      # S34 conn refused
    "TS-PW-03": dict(target=2, probes=2, cold=True),         # S18.C locale price
    "TS-PW-04": dict(target=2, probes=1, cold=True),         # S33 dns
    "TS-PW-05": dict(target=2, probes=1, cold=True),         # S33 tls
    "TS-PW-06": dict(target=2, probes=1, cold=True),         # S33 disk full
    "TS-PW-07": dict(target=3, probes=1, cold=True),         # S33 401
    "TS-PW-08": dict(target=4, probes=2, cold=True),         # S33 conn timeout tie
    "TS-PW-09": dict(target=10, probes=4, cold=True),        # S33 negatives
    "TS-PW-10": dict(target=4, probes=2, cold=True),         # S33/S40 retry passed
    "TS-PW-11": dict(target=3, probes=3, cold=True),         # S16 bare timeout cold
    "TS-PW-12": dict(target=2, probes=2, cold=True),         # S39d feature-default
    "TS-PW-13": dict(target=2, probes=2, cold=True),         # S02 zero-error
    "TS-CY-01": dict(target=6, probes=0, outage=True),       # S34 admin assertion
    "TS-CY-02": dict(target=3, probes=1),                    # daily noise pb
}


def _policy(arc: Archetype) -> dict:
    p = dict(target=len(arc.variants), probes=0, backdate=0,
             outage=False, cold=False, also_fea=False)
    p.update(POLICY.get(arc.archetype_id, {}))
    return p


def _dt_ms(date: str, hour: int, second_offset: int) -> int:
    y, m, d = (int(x) for x in date.split("-"))
    base = dt.datetime(y, m, d, hour, 0, 0, tzinfo=dt.timezone.utc)
    return int(base.timestamp() * 1000) + second_offset * 1000


class Scheduler:
    def __init__(self, archetypes: dict[str, Archetype], *, smoke: bool = False):
        self.arcs = archetypes
        self.smoke = smoke
        self.rng = random.Random(config.SEED)
        self._launch_index: dict[tuple, PlannedLaunch] = {}
        self.launches: list[PlannedLaunch] = []

    # -- launch skeleton ---------------------------------------------------
    def _get_launch(self, project, name, date, hour, phase, attrs=None):
        key = (project, name, date)
        la = self._launch_index.get(key)
        if la is None:
            la = PlannedLaunch(project, name, date, hour, phase, attrs or [])
            self._launch_index[key] = la
            self.launches.append(la)
        return la

    def _build_skeleton(self):
        # Phase 0 backdated pre-history launches
        for date, project in config.BACKDATED_DAYS:
            name = {
                config.WSU: "UI Regression - Backdated",
                config.PSV: "API Regression - Backdated",
                config.FEA: "PW E2E - Warmup",
            }[project]
            self._get_launch(project, f"{name} {date}", date, 2, 0)
        # Phase 1..3 working-day launches
        for date, _label in config.TIMELINE:
            for project in config.PROJECTS:
                for lname, _hint in config.day_launches(project, date):
                    phase = self._phase_for(project, date)
                    self._get_launch(project, lname, date, self._hour_for(lname), phase)
        # dedicated probe launches on the demo day
        for project in config.PROJECTS:
            self._get_launch(project, "Probe Launch - Demo Day", config.DEMO_DAY, 20, 3)

    @staticmethod
    def _phase_for(project, date):
        if date in config.PROBE_DAYS:
            return 3
        if project == config.FEA and date == "2026-06-29":
            return 1  # FEA cold analysis while install-wide events < 50
        return 2

    @staticmethod
    def _hour_for(name):
        if "Nightly" in name or "E2E" in name or "Regression" in name:
            return 2
        if "Weekly" in name:
            return 3
        return 11  # smokes cluster mid-day

    # -- item placement ----------------------------------------------------
    def build(self) -> list[PlannedLaunch]:
        self._build_skeleton()
        for aid in sorted(self.arcs):
            self._place_archetype(self.arcs[aid])
        if not self.smoke:
            self._add_filler()
        # order launches by (date, hour, phase) for a coherent upload sequence
        self.launches.sort(key=lambda l: (l.date, l.hour, l.phase, l.name))
        return self.launches

    # generic "background noise" failures/passes so nightly launches look real,
    # fill the launch skeleton, and approach the SCENARIOS §3.2 volume roll-up.
    # Tagged scenario=filler so scoring ignores them.
    def _add_filler(self):
        for la in self.launches:
            if la.phase not in (1, 2):
                continue
            ft, pt = self._filler_targets(la.name)
            failed_now = sum(1 for pi in la.items if pi.item.status != "passed")
            add_failed = max(0, ft - failed_now)
            for _ in range(add_failed):
                la.items.append(PlannedItem(self._filler_item(la, failed=True), H))
            for _ in range(pt):
                la.items.append(PlannedItem(self._filler_item(la, failed=False), DECOY))

    @staticmethod
    def _filler_targets(name):
        if "Smoke" in name:
            return 1, 4
        if "Weekly" in name:
            return 4, 3
        if "Contract" in name or "Integration" in name:
            return 3, 5
        return 5, 8  # nightly / regression / e2e

    def _filler_item(self, la, failed):
        project = la.project
        gen = _FILLER[project]
        idx = self.rng.randrange(len(gen["fail"]))
        name = gen["names"][self.rng.randrange(len(gen["names"]))] + \
            f" #{self.rng.randrange(1000)}"
        if failed:
            # ~35% get a real label (H), rest stay ti (unlabeled tail)
            gt = self.rng.choice(["pb", "ab", "si", "nd"] + ["ti"] * 6)
            tmpl = gen["fail"][idx]
            logs = [LogRow("info", gen["info"][self.rng.randrange(len(gen["info"]))]),
                    LogRow("error", tmpl)]
            status = "failed"
        else:
            gt = None
            logs = [LogRow("info", gen["info"][self.rng.randrange(len(gen["info"]))]),
                    LogRow("info", gen["pass"][self.rng.randrange(len(gen["pass"]))])]
            status = "passed"
        return RenderedItem(
            archetype_id="FILLER", variant_idx=0, seq=self.rng.randrange(1 << 30),
            project=project, test_name=name, status=status, ground_truth=gt,
            logs=logs, scenario_refs=["filler"], adv_case=None, attachments=[])

    def _project_launches(self, project, *, phase=None, date=None, name_contains=None):
        out = []
        for la in self.launches:
            if la.project != project:
                continue
            if phase is not None and la.phase != phase:
                continue
            if date is not None and la.date != date:
                continue
            if name_contains and name_contains not in la.name:
                continue
            out.append(la)
        return out

    def _place_archetype(self, arc: Archetype):
        pol = _policy(arc)
        project = arc.project
        target = pol["target"]
        if self.smoke:
            target = min(target, 3)
        n_probe = min(pol["probes"], target) if not self.smoke else 0
        n_back = min(pol["backdate"], target - n_probe) if not self.smoke else 0
        n_hist = target - n_probe - n_back

        spam_cap = 40 if self.smoke else None
        seq = 0
        nvar = len(arc.variants)

        # DECOY variants (status passed) always render as decoys regardless of role
        def emit(role, launch):
            nonlocal seq
            vidx = seq % nvar
            it = render_item(arc, vidx, seq, launch.project, spam_cap=spam_cap)
            r = role
            if it.status == "passed":
                r = DECOY
            launch.items.append(PlannedItem(it, r))
            seq += 1

        # history placement -- start at a per-archetype offset so different
        # archetypes spread across different launches/days (avoids clustering
        # every archetype onto the first few launches).
        hist_launches = self._history_targets(arc, pol)
        # STABLE hash (Python's hash() is per-process randomized -> would break
        # the determinism guarantee and cross-run item ordering).
        off = (zlib.crc32(arc.archetype_id.encode()) & 0x7FFFFFFF) % len(hist_launches)
        for i in range(n_hist):
            emit(H, hist_launches[(off + i) % len(hist_launches)])
        # backdated placement
        if n_back:
            back_launches = self._project_launches(project, phase=0)
            for i in range(n_back):
                emit(H, back_launches[i % len(back_launches)])
        # probe placement
        if n_probe:
            probe_launches = self._probe_targets(arc)
            for i in range(n_probe):
                emit(P, probe_launches[i % len(probe_launches)])

        # S07: byte-identical long-tail copies into FEA
        if pol["also_fea"] and not self.smoke:
            fea_hist = self._project_launches(config.FEA, phase=2) or \
                self._project_launches(config.FEA, phase=1)
            copies = max(6, min(nvar, 12))
            for i in range(copies):
                vidx = i % nvar
                it = render_item(arc, vidx, 10_000 + i, config.FEA, spam_cap=spam_cap)
                it.scenario_refs = sorted(set(it.scenario_refs) | {"S07"})
                fea_hist[i % len(fea_hist)].items.append(PlannedItem(it, H))

    def _history_targets(self, arc, pol) -> list[PlannedLaunch]:
        project = arc.project
        if pol["outage"]:
            outs = self._project_launches(project, date=config.OUTAGE_DAY)
            if outs:
                return outs
        if project == config.FEA and pol["cold"]:
            cold = self._project_launches(config.FEA, phase=1)
            if cold:
                return cold
        hist = self._project_launches(project, phase=2)
        if not hist:
            hist = self._project_launches(project, phase=1)
        if not hist:
            hist = self._project_launches(project, phase=0)
        return hist

    def _probe_targets(self, arc) -> list[PlannedLaunch]:
        project = arc.project
        demo = self._project_launches(project, name_contains="Probe Launch")
        return demo or self._project_launches(project, phase=3)


_FILLER = {
    config.WSU: {
        "names": ["Checkout. Cart. Update quantity", "Catalog. Search. Filter by brand",
                  "Account. Profile. Update address", "Checkout. Shipping. Select method"],
        "info": ["[STEP] Open page and wait for load",
                 "[API] GET /v2/session -> 200 in 84 ms"],
        "fail": [
            "java.lang.AssertionError: expected [true] but found [false]\n\tat com.hawkins.shop.checkout.MiscTest.run(MiscTest.java:41)",
            "org.openqa.selenium.NoSuchElementException: no such element: Unable to locate element: {\"method\":\"css selector\",\"selector\":\".add-to-cart\"}\n\tat com.hawkins.shop.catalog.MiscTest.run(MiscTest.java:52)",
        ],
        "pass": ["[STEP] Assertion passed: element visible", "Test finished OK in 1.2 s"],
    },
    config.PSV: {
        "names": ["TransferApiTests.Post_Transfer_Returns201",
                  "LedgerApiTests.Get_Balance_Ok", "RefundApiTests.Post_Refund_Ok"],
        "info": ["[API] POST /v1/transfers -> 201 in 63 ms",
                 "[STEP] Build RestSharp request with idempotency key"],
        "fail": [
            "Xunit.Sdk.EqualException: Assert.Equal() Failure\nExpected: 200\nActual:   500\n   at Hawkins.Payments.Tests.MiscTests.Run()",
            "System.Net.Http.HttpRequestException: Response status code does not indicate success: 502 (Bad Gateway).\n   at Hawkins.Payments.Client.ApiClient.SendAsync()",
        ],
        "pass": ["[STEP] Assert 201 Created -> OK", "Request completed in 58 ms"],
    },
    config.FEA: {
        "names": ["storefront/cart.spec.ts", "storefront/search.spec.ts",
                  "storefront/pdp.spec.ts", "Legacy Admin > Dashboard loads"],
        "info": ["[STEP] page.goto('/cart')", "cy:command  ✔ visit  /admin"],
        "fail": [
            "Error: expect(received).toBeVisible()\nCall log:\n  - waiting for locator('[data-test=cart]')\n    at cart.spec.ts:22:18",
            "AssertionError: expected 'Loading' to equal 'Ready'\n    at Context.eval (search.spec.ts:31:10)",
        ],
        "pass": ["[STEP] expect(locator).toBeVisible() passed", "spec passed in 0.9 s"],
    },
}
