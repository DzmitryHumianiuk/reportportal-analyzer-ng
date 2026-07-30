#!/usr/bin/env python3
"""LLM sidecar role showcase (spec 04) — make each LLM role visibly fire end-to-end.

Four cases, small (1 launch each), NEW launches only (never touches existing probe
launches / scorecards):

  coldstart : fresh project `llm-demo` (0 labels) -> abstain items get provisional
              ai_suggested labels via the cold-start rubric + llm_event role=coldstart.
  explainer : webshop-ui launch of near-twins of human-labeled history -> confident
              suggestions with llm_used=true and a persisted suggestion.explanation.
  judge     : a NPE blended between the pb cart-discount family and the ab auth-session
              family -> lands mid-band (0.45<=p*<0.75) with mixed-label candidates;
              the SUGGEST route enqueues role=judge; the async verdict reorders the
              NEXT suggest call (resultPosition promotion).
  abstain   : a clearly novel failure in webshop-ui -> abstains with NO LLM on the
              label path (LLM suggests, never auto-confirms).

Usage:
  python3 llm_demo.py --case coldstart|explainer|judge|abstain|all
  python3 llm_demo.py --verify
  python3 llm_demo.py --case judge --judge-blend 2   # iterate the blend
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from gen import config  # noqa: E402
from gen import rp as rpmod
from gen.corpus import LogRow, RenderedItem, load_corpus, render_item  # noqa: E402
from gen.schedule import H, P, PlannedItem, PlannedLaunch  # noqa: E402

LLM_PROJECT = "llm-demo"
DEMO_DATE = "2026-07-20"
PG = "deploy/analyzer-pg"
PROJ_ID = {config.WSU: 3, config.PSV: 4, config.FEA: 5}


def psql(sql):
    out = subprocess.run(
        [
            "kubectl",
            "exec",
            PG,
            "--",
            "psql",
            "-U",
            "analyzer",
            "-d",
            "analyzer",
            "-tAF|",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return out.stdout.strip()


# --------------------------------------------------------------------------- #
# item builders
# --------------------------------------------------------------------------- #
def ritem(project, name, gt, logs, archetype="LLM-DEMO", scenario="llm_demo"):
    return RenderedItem(
        archetype_id=archetype,
        variant_idx=0,
        seq=0,
        project=project,
        test_name=name,
        status="failed",
        ground_truth=gt,
        logs=[LogRow(l, m) for l, m in logs],
        scenario_refs=[scenario],
        adv_case=None,
        attachments=[],
    )


def _launch(project, name, hour, items_roles):
    la = PlannedLaunch(
        project=project,
        name=name,
        date=DEMO_DATE,
        hour=hour,
        phase=3,
        attributes=[{"key": "demo", "value": "llm"}],
    )
    la.items = [PlannedItem(it, role) for it, role in items_roles]
    return la


def _fresh_launch(client, project, name, hour, items_roles, enable_aa=True):
    """Delete any prior demo launch of this name (by exact id), then upload fresh."""
    lstart = rpmod._ms(DEMO_DATE, hour, 0)
    found = client.find_launch(project, name, lstart)
    if found and found.get("id"):
        client.delete_launch(project, found["id"])
        print(f"  (removed prior {project}/{name} id={found['id']})", flush=True)
    if enable_aa:
        client.set_analyzer(project, enabled=True)
    la = _launch(project, name, hour, items_roles)
    res = client.report_launch(la)
    lid = res.get("lid") or client.numeric_launch(project, res["luuid"])
    print(
        f"  uploaded {project}/{name} luuid={res['luuid']} lid={lid} ({len(la.items)} items)",
        flush=True,
    )
    return res, lid


# --------------------------------------------------------------------------- #
# case 1: cold-start
# --------------------------------------------------------------------------- #
def case_coldstart(client):
    print("\n===== CASE 1: COLD-START (fresh project llm-demo) =====", flush=True)
    created = client.ensure_project(LLM_PROJECT)
    print(f"  project {LLM_PROJECT}: {'created' if created else 'exists'}", flush=True)
    client.set_analyzer(LLM_PROJECT, enabled=True)
    arcs = load_corpus(os.path.join(HERE, "corpus"))
    # 8 items with clear, distinct failure classes drawn from the corpus
    picks = [
        "JAVA-SEL-01",
        "JAVA-SEL-02",
        "JAVA-SEL-04",
        "JAVA-SEL-08",
        "JAVA-SEL-23",
        "NET-XUN-05",
        "NET-XUN-08",
        "TS-PW-06",
    ]
    items = []
    for aid in picks:
        arc = arcs.get(aid)
        if not arc:
            continue
        it = render_item(arc, 0, 0, LLM_PROJECT)
        it.scenario_refs = ["llm_coldstart"]
        items.append((it, P))
    res, lid = _fresh_launch(client, LLM_PROJECT, "LLM Cold-Start Showcase", 12, items)
    if lid:
        client.analyze(LLM_PROJECT, lid)
        print(f"  analyze(lid={lid}) triggered — coldstart is async", flush=True)
    return {"project": LLM_PROJECT, "lid": lid, "n": len(items)}


# --------------------------------------------------------------------------- #
# case 2: explainer
# --------------------------------------------------------------------------- #
def case_explainer(client):
    print("\n===== CASE 2: EXPLAINER (webshop-ui near-twins of human history) =====", flush=True)
    arcs = load_corpus(os.path.join(HERE, "corpus"))
    items = []
    for aid in ["JAVA-SEL-01", "JAVA-SEL-23", "JAVA-SEL-02"]:  # pb / si / ab chronic modes
        arc = arcs[aid]
        it = render_item(arc, 0, 0, config.WSU)
        it.test_name = it.test_name + " [llm-explainer-demo]"  # unique, avoids probe overlap
        it.scenario_refs = ["llm_explainer"]
        items.append((it, P))
    res, lid = _fresh_launch(client, config.WSU, "LLM Explainer Showcase", 13, items)
    if lid:
        client.analyze(config.WSU, lid)
        print(f"  analyze(lid={lid}) triggered — explainer fills explanation async", flush=True)
    return {"project": config.WSU, "lid": lid, "n": len(items)}


# --------------------------------------------------------------------------- #
# case 3: judge (the engineering part)
# --------------------------------------------------------------------------- #
# Blends of the cart-discount NPE (pb) and the auth-session NPE (ab); each variant
# shifts the lexical mix so we can walk p* into the mid-band empirically.
JUDGE_BLENDS = {
    1: (
        'java.lang.NullPointerException: Cannot invoke "com.hawkins.shop.model.Money.amount()" '
        'because "session" is null\n'
        "\tat com.hawkins.shop.checkout.CheckoutSessionService.resolveCartTotals(CheckoutSessionService.java:88)\n"
        "\tat com.hawkins.shop.checkout.CheckoutSessionTest.resolveTotals(CheckoutSessionTest.java:57)\n"
        "\tat java.base/java.lang.reflect.Method.invoke(Method.java:580)\n"
        "\tat org.testng.internal.invokers.TestInvoker.invokeMethod(TestInvoker.java:677)"
    ),
    2: (
        'java.lang.NullPointerException: Cannot invoke "com.hawkins.shop.model.Money.amount()" '
        'because the return value of "com.hawkins.shop.auth.Session.userId()" is null\n'
        "\tat com.hawkins.shop.checkout.CheckoutSessionService.applyDiscount(CheckoutSessionService.java:142)\n"
        "\tat com.hawkins.shop.checkout.CheckoutSessionTest.resolveTotals(CheckoutSessionTest.java:57)\n"
        "\tat java.base/java.lang.reflect.Method.invoke(Method.java:580)\n"
        "\tat org.testng.internal.invokers.TestInvoker.invokeMethod(TestInvoker.java:677)"
    ),
    3: (
        'java.lang.NullPointerException: Cannot invoke "com.hawkins.shop.cart.CartLine.getDiscount()" '
        'because "session" is null\n'
        "\tat com.hawkins.shop.checkout.CheckoutSessionService.resolve(CheckoutSessionService.java:96)\n"
        "\tat com.hawkins.shop.auth.SessionFilter.doFilter(SessionFilter.java:96)\n"
        "\tat com.hawkins.shop.checkout.CheckoutSessionTest.resolveTotals(CheckoutSessionTest.java:57)"
    ),
    # 4-6: near-twin of the cart-discount pb trace (high cosine -> conf up) with an
    # auth-session token/frame mixed in to keep the ab family in the candidate set.
    4: (
        'java.lang.NullPointerException: Cannot invoke "com.hawkins.shop.model.Money.amount()" '
        'because the return value of "com.hawkins.shop.cart.CartLine.getDiscount()" is null\n'
        "\tat com.hawkins.shop.checkout.CartService.applyDiscount(CartService.java:188)\n"
        "\tat com.hawkins.shop.checkout.CartService.recalculate(CartService.java:142)\n"
        "\tat com.hawkins.shop.auth.SessionFilter.doFilter(SessionFilter.java:96)\n"
        "\tat com.hawkins.shop.checkout.CartDiscountTest.applyPercentageDiscount(CartDiscountTest.java:73)\n"
        "\tat java.base/java.lang.reflect.Method.invoke(Method.java:580)\n"
        "\tat org.testng.internal.invokers.TestInvoker.invokeMethod(TestInvoker.java:677)"
    ),
    5: (
        'java.lang.NullPointerException: Cannot invoke "com.hawkins.shop.auth.Session.userId()" '
        'because the return value of "com.hawkins.shop.cart.CartLine.getDiscount()" is null\n'
        "\tat com.hawkins.shop.checkout.CartService.applyDiscount(CartService.java:188)\n"
        "\tat com.hawkins.shop.checkout.CartService.recalculate(CartService.java:142)\n"
        "\tat com.hawkins.shop.checkout.CartDiscountTest.applyPercentageDiscount(CartDiscountTest.java:73)\n"
        "\tat java.base/java.lang.reflect.Method.invoke(Method.java:580)\n"
        "\tat org.testng.internal.invokers.TestInvoker.invokeMethod(TestInvoker.java:677)"
    ),
    6: (
        'java.lang.NullPointerException: Cannot invoke "com.hawkins.shop.model.Money.amount()" '
        'because "session" is null\n'
        "\tat com.hawkins.shop.checkout.CartService.applyDiscount(CartService.java:188)\n"
        "\tat com.hawkins.shop.auth.SessionFilter.doFilter(SessionFilter.java:96)\n"
        "\tat com.hawkins.shop.auth.SessionFilterTest.rejectExpiredCookie(SessionFilterTest.java:61)\n"
        "\tat java.base/java.lang.reflect.Method.invoke(Method.java:580)\n"
        "\tat org.testng.internal.invokers.TestInvoker.invokeMethod(TestInvoker.java:677)"
    ),
}


# The locator discriminant pair (JAVA-SEL-09 / S18.B) naturally lands mid-band with
# MIXED pb/ab candidates: same test (retrieves both families) + a NOVEL selector
# lexically between #checkout-submit-btn (pb, button really missing) and
# #chk-submit-button-v2 (ab, selector never shipped). No exact-hash match -> GBM
# decides ~0.57, and the top-K carries both labels -> judge is enqueued on suggest.
LOCATOR_SELECTOR = "#checkout-submit-btn-v3"
_LOCATOR_TRACE = (
    "org.openqa.selenium.NoSuchElementException: no such element: Unable to locate element: "
    '{"method":"css selector","selector":"' + LOCATOR_SELECTOR + '"}\n'
    "  (Session info: chrome=126.0.6478.126)\n"
    "Build info: version: '4.21.0', revision: '4a2ae60b1e'\n"
    "Driver info: org.openqa.selenium.chrome.ChromeDriver\n"
    "Command: [d3a71b0c92, findElement {using: css selector, value: " + LOCATOR_SELECTOR + "}]\n"
    "\tat org.openqa.selenium.remote.RemoteWebDriver.findElement(RemoteWebDriver.java:388)\n"
    "\tat com.hawkins.shop.checkout.PaymentSubmitTest.submitWithSavedCard(PaymentSubmitTest.java:115)\n"
    "\tat java.base/java.lang.reflect.Method.invoke(Method.java:580)\n"
    "\tat org.testng.internal.invokers.TestInvoker.invokeMethod(TestInvoker.java:677)"
)


def case_judge_anchored(client, wait_s=45):
    """Judge showcase with in-launch labeled anchors.

    The suggest route scopes candidate retrieval by analyzerMode, so a lone item in
    a fresh unique-named launch finds no cross-launch neighbours and abstains. We
    instead put a LABELED pb anchor + LABELED ab anchor (the JAVA-SEL-09 / S18.B
    locator pair) in the SAME launch as the probe — same-launch labels are in scope
    under every mode. The probe (novel selector) then retrieves BOTH -> mixed-label
    top-K -> GBM lands mid-band -> the suggest route enqueues role=judge.
    """
    print("\n===== CASE 3: JUDGE (in-launch labeled anchors, mixed pb/ab) =====", flush=True)
    arcs = load_corpus(os.path.join(HERE, "corpus"))
    pb = render_item(arcs["JAVA-SEL-09"], 0, 0, config.WSU)  # #checkout-submit-btn -> pb
    pb.test_name = "Checkout. Payment. Submit order (anchor pb) [llm-judge]"
    pb.ground_truth = "pb"
    ab = render_item(arcs["JAVA-SEL-09"], 2, 0, config.WSU)  # #chk-submit-button-v2 -> ab
    ab.test_name = "Checkout. Payment. Submit order (anchor ab) [llm-judge]"
    ab.ground_truth = "ab"
    probe = render_item(arcs["JAVA-SEL-09"], 2, 0, config.WSU)
    probe.test_name = "Checkout. Payment. Submit order (probe) [llm-judge]"
    probe.ground_truth = "ti"
    # novel selector so the probe is not an exact hash of either anchor
    probe.logs = [
        LogRow(l.level, l.message.replace("#chk-submit-button-v2", "#chk-submit-btn-v3"))
        for l in probe.logs
    ]
    for it in (pb, ab, probe):
        it.scenario_refs = ["llm_judge"]
        it.archetype_id = "LLM-JUDGE"
    res, lid = _fresh_launch(
        client, config.WSU, "LLM Judge Showcase", 14, [(pb, H), (ab, H), (probe, P)]
    )
    if not lid:
        return {}
    # label the two anchors so the probe retrieves mixed labelled candidates
    labels = client.replay_history_labels(config.WSU, res["items"], roles=("H",))
    print(f"  labelled {labels} anchors (pb + ab) in-launch", flush=True)
    time.sleep(5)
    client.analyze(config.WSU, lid)
    probe_uuid = res["items"][2][0]
    nid = client.numeric_item(config.WSU, probe_uuid)
    print(f"  probe nid={nid}; waiting {wait_s}s for analyze decision...", flush=True)
    time.sleep(wait_s)
    conf = psql(
        f"select round(confidence::numeric,3) from suggestion where project_id=3 "
        f"and item_id={nid} order by created_at desc limit 1;"
    )
    print(f"  probe analyze confidence={conf}", flush=True)
    before = _suggest_order(client, nid)
    print(f"  suggest #1 (pre-judge): {before}", flush=True)
    print(f"  waiting {wait_s}s for async judge...", flush=True)
    time.sleep(wait_s)
    after = _suggest_order(client, nid)
    print(f"  suggest #2 (post-judge): {after}", flush=True)
    judged = psql(
        f"select outcome from llm_event where role='judge' and item_id={nid} "
        f"order by created_at desc limit 1;"
    )
    print(f"  llm_event role=judge outcome={judged or '(none)'}", flush=True)
    return {
        "project": config.WSU,
        "lid": lid,
        "probe_nid": nid,
        "conf": conf,
        "order_before": before,
        "order_after": after,
        "judge_outcome": judged,
    }


def case_judge(client, blend=1, wait_s=45):
    print(f"\n===== CASE 3: JUDGE (mid-band locator discriminant, blend={blend}) =====", flush=True)
    if blend in JUDGE_BLENDS:  # legacy NPE blends (kept for the record)
        name = "Checkout. Session. Resolve cart session totals [llm-judge-demo]"
        info = [
            ("info", "[STEP] Resolve cart totals for authenticated checkout session"),
            ("debug", "[API] POST /v2/checkout/CART-88231/session -> 500 in 61 ms"),
            ("error", JUDGE_BLENDS[blend]),
        ]
    if blend not in JUDGE_BLENDS:
        # default (blend 7+): render the REAL JAVA-SEL-09 locator archetype so the
        # error_hash / exception_fp / test_case_hash match the labeled pb & ab family
        # (that exact match + mixed-label neighbours is what the GBM scores ~0.57).
        # variant 7->0 (#checkout-submit-btn, pb), 8->2 (#chk-submit-button-v2, ab).
        arcs = load_corpus(os.path.join(HERE, "corpus"))
        vidx = 0 if blend == 7 else 2
        it = render_item(arcs["JAVA-SEL-09"], vidx, 0, config.WSU)
        it.scenario_refs = ["llm_judge"]
        it.archetype_id = "LLM-JUDGE"
    else:
        it = ritem(config.WSU, name, "pb", info, archetype="LLM-JUDGE", scenario="llm_judge")
    res, lid = _fresh_launch(client, config.WSU, "LLM Judge Showcase", 14, [(it, P)])
    if not lid:
        print("  ERROR: no launch id")
        return {}
    client.analyze(config.WSU, lid)
    print(
        f"  analyze(lid={lid}); waiting {wait_s}s for the analyze suggestion to land...", flush=True
    )
    time.sleep(wait_s)
    nid = client.numeric_item(config.WSU, res["items"][0][0])
    conf = psql(
        f"select round(confidence::numeric,3) from suggestion where project_id=3 "
        f"and item_id={nid} order by created_at desc limit 1;"
    )
    pl = psql(
        f"select predicted_label from suggestion where project_id=3 and item_id={nid} "
        f"order by created_at desc limit 1;"
    )
    print(
        f"  item nid={nid} decision: predicted={pl} confidence={conf} "
        f"(judge fires iff 0.45<=conf<0.75)",
        flush=True,
    )
    band_ok = False
    try:
        band_ok = 0.45 <= float(conf) < 0.75
    except ValueError:
        pass
    if not band_ok:
        print(
            f"  >> conf {conf} NOT in mid-band; try a different --judge-blend "
            f"(available: {sorted(JUDGE_BLENDS)})",
            flush=True,
        )
        return {"project": config.WSU, "lid": lid, "nid": nid, "conf": conf, "band_ok": False}

    # suggest call #1 -> enqueues the judge (async); capture ordering BEFORE verdict
    before = _suggest_order(client, nid)
    print(f"  suggest #1 order (pre-judge): {before}", flush=True)
    print(f"  waiting {wait_s}s for async judge verdict...", flush=True)
    time.sleep(wait_s)
    after = _suggest_order(client, nid)
    print(f"  suggest #2 order (post-judge): {after}", flush=True)
    judged = psql(
        f"select outcome from llm_event where role='judge' and project_id=3 "
        f"and item_id={nid} order by created_at desc limit 1;"
    )
    print(f"  llm_event role=judge outcome={judged or '(none yet)'}", flush=True)
    return {
        "project": config.WSU,
        "lid": lid,
        "nid": nid,
        "conf": conf,
        "band_ok": True,
        "order_before": before,
        "order_after": after,
        "judge_outcome": judged,
    }


def _suggest_order(client, nid):
    r = client.suggest(config.WSU, nid)
    out = []
    for s in r or []:
        out.append(
            {
                "pos": s.get("resultPosition"),
                "rel": s.get("relevantItem"),
                "type": s.get("issueType"),
                "score": s.get("matchScore"),
            }
        )
    return sorted(out, key=lambda x: x["pos"] if x["pos"] is not None else 99)


# --------------------------------------------------------------------------- #
# case 4: honest abstain (no LLM on the label path)
# --------------------------------------------------------------------------- #
def case_abstain(client):
    print("\n===== CASE 4: HONEST ABSTAIN (novel failure, no LLM label path) =====", flush=True)
    it = ritem(
        config.WSU,
        "Ledger. Reconciliation. Verify quantum ledger sync [llm-abstain-demo]",
        "ti",
        [
            ("info", "[STEP] Reconcile distributed ledger shards"),
            (
                "error",
                "com.hawkins.shop.ledger.QuantumLedgerDesyncError: shard vector "
                "clock divergence detected across 7 replicas; no coherent snapshot\n"
                "\tat com.hawkins.shop.ledger.QuantumReconciler.verify(QuantumReconciler.java:412)",
            ),
        ],
        archetype="LLM-ABSTAIN",
        scenario="llm_abstain",
    )
    res, lid = _fresh_launch(client, config.WSU, "LLM Honest-Abstain Showcase", 15, [(it, P)])
    if lid:
        client.analyze(config.WSU, lid)
    nid = client.numeric_item(config.WSU, res["items"][0][0]) if res["items"] else None
    print(
        f"  item nid={nid}: expect abstain (ti); webshop-ui is not cold so coldstart "
        f"is skipped -> no LLM on the label path",
        flush=True,
    )
    return {"project": config.WSU, "lid": lid, "nid": nid}


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #
def verify():
    print("\n===== VERIFY (analyzer DB evidence) =====", flush=True)
    print("-- llm_event by role/outcome --")
    print(psql("select role,outcome,count(*) from llm_event group by role,outcome order by role;"))
    print("-- llm_cache entries by role --")
    print(psql("select role,count(*),sum(hits) from llm_cache group by role order by role;"))
    print("-- coldstart suggestions in llm-demo (llm_used, method) --")
    print(
        psql(
            "select s.item_id, s.predicted_label, round(s.confidence::numeric,2), "
            "s.model_ver from suggestion s join project p on p.project_id=s.project_id "
            "where s.model_ver like 'rubric+%' order by s.created_at desc limit 10;"
        )
    )
    print("-- explainer: non-empty explanations (recent) --")
    print(
        psql(
            "select item_id, left(explanation,70) from suggestion "
            "where explanation is not null and explanation<>'' "
            "order by created_at desc limit 6;"
        )
    )
    print("-- judge events --")
    print(
        psql(
            "select project_id,item_id,outcome,latency_ms from llm_event "
            "where role='judge' order by created_at desc limit 6;"
        )
    )


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--case", choices=["coldstart", "explainer", "judge", "abstain", "all"])
    ap.add_argument("--judge-blend", type=int, default=7)
    ap.add_argument("--wait", type=int, default=45)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--rp-api", default=config.API_BASE)
    ap.add_argument("--rp-uat", default=config.UAT_BASE)
    args = ap.parse_args()

    if args.verify and not args.case:
        verify()
        return

    client = rpmod.RPClient(uat_base=args.rp_uat, api_base=args.rp_api)
    print("login ...", flush=True)
    client.login()
    results = {}
    if args.case in ("coldstart", "all"):
        results["coldstart"] = case_coldstart(client)
    if args.case in ("explainer", "all"):
        results["explainer"] = case_explainer(client)
    if args.case in ("judge", "all"):
        if args.judge_blend == 0:
            results["judge"] = case_judge_anchored(client, wait_s=args.wait)
        else:
            results["judge"] = case_judge(client, blend=args.judge_blend, wait_s=args.wait)
    if args.case in ("abstain", "all"):
        results["abstain"] = case_abstain(client)
    print("\n=== RESULTS ===")
    print(json.dumps(results, indent=2, default=str))
    if args.verify:
        time.sleep(args.wait)
        verify()


if __name__ == "__main__":
    main()
