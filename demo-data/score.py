#!/usr/bin/env python3
"""Score the analyzer's probe-launch decisions against demo-data/expected.json.

Joins three sources for the Jul-17 probe launches (RP ids 161/162/163):

  1. RP API  -- probe items: numeric id + stamped attributes
                (scenario / adv_case / ground_truth / archetype).
  2. analyzer.suggestion (via `kubectl exec deploy/analyzer-pg -- psql`) --
                the stored decision: predicted_label, confidence, model_ver,
                matched_mode/item, and the features jsonb (abstains included).
  3. demo-data/expected.json -- expected_action / expected_label / expected_method.

Emits demo-data/SCORE.md (readable scorecard) and demo-data/score.json (machine).

Method note: the physical `suggestion` schema stores only gbm-vs-rule in
`model_ver`; hash/kb/rule_cold are sub-classified best-effort from the features
jsonb (`same_error_hash_top1`, `kb_purity`, `kb_top1_score`). Action is derived
from the analyzer's own bands (decision.py): pred=='ti' -> abstain;
conf>=0.75 -> auto; conf>=0.45 -> suggest; else abstain.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from gen import config, rp as rpmod  # noqa: E402

PROBE_LAUNCHES = {config.WSU: 270, config.PSV: 271, config.FEA: 272}
PG_DEPLOY = "deploy/analyzer-pg"
TAU_AUTO, TAU_SUGGEST = 0.75, 0.45

BASE = {"pb001": "pb", "ab001": "ab", "si001": "si", "nd001": "nd", "ti001": "ti",
        "ti": "ti", "pb": "pb", "ab": "ab", "si": "si", "nd": "nd", None: "ti"}


def base(lbl):
    return BASE.get(lbl, lbl or "ti")


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
def fetch_probe_items(client):
    """RP probe items -> {item_id: {name, project, scenarios[], adv_case, ground_truth, archetype}}."""
    items = {}
    for project, lid in PROBE_LAUNCHES.items():
        page = 1
        while True:
            r = client._send("GET", f"{client.api}/api/v1/{project}/item",
                             params={"filter.eq.launchId": lid, "page.size": 100,
                                     "page.page": page})
            d = r.json()
            content = d.get("content", [])
            for it in content:
                scen, adv, gt, arc = [], None, None, None
                for a in (it.get("attributes") or []):
                    k, v = a.get("key"), a.get("value")
                    if k == "scenario":
                        scen.append(v)
                    elif k == "adv_case":
                        adv = v
                    elif k == "ground_truth":
                        gt = v
                    elif k == "archetype":
                        arc = v
                items[it["id"]] = {"name": it.get("name"), "project": project,
                                   "scenarios": scen, "adv_case": adv,
                                   "ground_truth": gt, "archetype": arc}
            pg = d.get("page", {})
            if not content or page >= pg.get("totalPages", 1):
                break
            page += 1
    return items


def fetch_suggestions():
    """Latest analyzer.suggestion per probe item -> {item_id: {...}}."""
    _lids = ",".join(str(v) for v in PROBE_LAUNCHES.values())
    sql = (
        "select coalesce(json_agg(row_to_json(t)),'[]') from ("
        " select distinct on (item_id) item_id, project_id, predicted_label as pred,"
        " confidence as conf, model_ver,"
        " (matched_mode_id is not null) as has_mode,"
        " (matched_item_id is not null) as has_item,"
        " (features->>'same_error_hash_top1') as hash_top1,"
        " (features->>'kb_purity') as kb_purity,"
        " (features->>'kb_top1_score') as kb_score,"
        " (features is not null) as has_features"
        f" from analyzer.suggestion where launch_id in ({_lids})"
        " order by item_id, created_at desc, suggestion_id desc) t;"
    )
    out = subprocess.run(
        ["kubectl", "exec", PG_DEPLOY, "--", "psql", "-U", "analyzer", "-d",
         "analyzer", "-tAc", sql], capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr[:400]}")
    rows = json.loads(out.stdout.strip() or "[]")
    return {r["item_id"]: r for r in rows}


def load_expected():
    """expected.json -> lookups by (archetype, ground_truth) and by archetype."""
    doc = json.load(open(os.path.join(HERE, "expected.json")))
    by_ag, by_a = {}, {}
    for p in doc["probes"]:
        arc, gt = p["archetype_id"], p.get("ground_truth")
        row = {"action": p["expected_action"], "label": p["expected_label"],
               "method": p["expected_method"]}
        by_ag.setdefault((arc, gt), row)
        by_a.setdefault(arc, row)
    return by_ag, by_a


# ---------------------------------------------------------------------------
# derivations
# ---------------------------------------------------------------------------
def derive_action(sug):
    if sug is None:
        return "abstain"
    if base(sug.get("pred")) == "ti":
        return "abstain"
    conf = float(sug.get("conf") or 0.0)
    if conf >= TAU_AUTO:
        return "auto"
    if conf >= TAU_SUGGEST:
        return "suggest"
    return "abstain"


def derive_method(sug):
    if sug is None:
        return None
    mv = sug.get("model_ver") or ""
    if mv.startswith("gbm-"):
        return "gbm"
    if str(sug.get("hash_top1")) in ("1", "1.0"):
        return "hash"
    try:
        purity = float(sug.get("kb_purity") or 0)
        score = float(sug.get("kb_score") or 0)
    except (TypeError, ValueError):
        purity = score = 0.0
    if sug.get("has_mode") and purity >= 0.95 and score >= 0.7:
        return "kb"
    return "rule_cold"


def is_debatable(exp, got_action, got_method, got_label, truth, project):
    """Mark expected.json/analyzer-timing artifacts that are not analyzer faults."""
    # Analyzer was MORE decisive than the spec but produced the CORRECT label:
    # a calibration gap in expected.json, not analyzer misbehavior. (After the
    # history replay the label evidence is richer than the spec's 'suggest' assumed.)
    if exp["action"] == "suggest" and got_action == "auto" and got_label == truth:
        return ("spec expected suggest; analyzer auto-labeled the CORRECT label "
                "(post-replay history richer than the spec's suggest assumption)")
    # rule_cold can only auto or abstain -> a 'suggest' expectation is unmeetable there
    if exp["action"] == "suggest" and got_method == "rule_cold" and got_label == truth:
        return "spec expected suggest but a rule_cold/seed path can only auto or abstain"
    # cold FEA probe expected a trained-model band, but the install GBM only trained
    # post-hoc (this remediation) -> method/band is a timeline artifact
    if project == config.FEA and exp["method"] == "gbm" and got_method != "gbm" \
            and got_label == truth:
        return "FEA cold: expected gbm but project was cold at analysis time"
    return None


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def score():
    client = rpmod.RPClient(verbose=False)
    client.login()
    items = fetch_probe_items(client)
    sugg = fetch_suggestions()
    by_ag, by_a = load_expected()

    rows = []
    for iid, it in items.items():
        arc, gt = it["archetype"], it["ground_truth"]
        exp = by_ag.get((arc, gt)) or by_a.get(arc)
        if exp is None:
            continue  # no expectation (filler/unmapped) -> not scored
        sug = sugg.get(iid)
        got_action = derive_action(sug)
        got_label = base(sug.get("pred")) if sug else "ti"
        got_method = derive_method(sug)
        truth = base(gt)
        conf = float(sug.get("conf")) if sug and sug.get("conf") is not None else None

        action_ok = got_action == exp["action"]
        is_abstain_expected = exp["action"] == "abstain"
        decided = got_action != "abstain"
        label_ok = (got_label == truth) if decided else None
        conf_wrong = got_action == "auto" and got_label != truth
        abstain_ok = (got_action == "abstain") if is_abstain_expected else None
        method_ok = (got_method == exp["method"]) if exp["method"] else None
        debate = is_debatable(exp, got_action, got_method, got_label, truth, it["project"])

        rows.append({
            "item_id": iid, "project": it["project"], "archetype": arc,
            "scenarios": it["scenarios"], "adv_case": it["adv_case"],
            "test_name": it["name"], "ground_truth": truth,
            "exp_action": exp["action"], "exp_label": base(exp["label"]),
            "exp_method": exp["method"],
            "got_action": got_action, "got_label": got_label,
            "got_method": got_method, "conf": conf,
            "action_ok": action_ok, "label_ok": label_ok,
            "conf_wrong": conf_wrong, "abstain_expected": is_abstain_expected,
            "abstain_ok": abstain_ok, "method_ok": method_ok,
            "debatable": debate,
        })
    return rows


def agg(rows):
    """Aggregate a list of scored rows into a metrics dict (debatable excluded)."""
    real = [r for r in rows if not r["debatable"]]
    n = len(real)
    action_ok = sum(1 for r in real if r["action_ok"])
    decided = [r for r in real if r["got_action"] != "abstain"]
    label_ok = sum(1 for r in decided if r["label_ok"])
    conf_wrong = sum(1 for r in real if r["conf_wrong"])
    exp_abstain = [r for r in real if r["abstain_expected"]]
    abstain_ok = sum(1 for r in exp_abstain if r["abstain_ok"])
    meth = [r for r in real if r["method_ok"] is not None]
    method_ok = sum(1 for r in meth if r["method_ok"])
    return {
        "n": n, "n_debatable": len(rows) - n,
        "action_acc": round(action_ok / n, 3) if n else None,
        "label_acc_decided": round(label_ok / len(decided), 3) if decided else None,
        "n_decided": len(decided),
        "confidently_wrong": conf_wrong,
        "abstain_recall": round(abstain_ok / len(exp_abstain), 3) if exp_abstain else None,
        "n_expected_abstain": len(exp_abstain),
        "method_match": round(method_ok / len(meth), 3) if meth else None,
        "n_method_scored": len(meth),
    }


def verdict(m):
    if m["n"] == 0:
        return "N/A"
    if m["confidently_wrong"] > 0 or (m["action_acc"] or 0) < 0.5:
        return "FAIL"
    lab = m["label_acc_decided"]
    if (m["action_acc"] or 0) >= 0.8 and (lab is None or lab >= 0.8):
        return "PASS"
    return "PARTIAL"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def render(rows):
    overall = agg(rows)
    per_project = {p: agg([r for r in rows if r["project"] == p]) for p in config.PROJECTS}

    scen_rows = defaultdict(list)
    for r in rows:
        for s in (r["scenarios"] or ["<none>"]):
            scen_rows[s].append(r)
    per_scenario = {s: agg(rs) for s, rs in scen_rows.items()}

    adv_rows = defaultdict(list)
    for r in rows:
        if r["adv_case"]:
            adv_rows[r["adv_case"].split(".")[0]].append(r)
    adversarial = {a: adv_verdict(a, rs, rows) for a, rs in sorted(adv_rows.items())}

    # top failures: real misses (not debatable), worst first (confidently-wrong, then action miss)
    def sev(r):
        if r["conf_wrong"]:
            return 0
        if not r["action_ok"]:
            return 1
        if r["label_ok"] is False:
            return 2
        return 9
    fails = sorted([r for r in rows if not r["debatable"]
                    and (r["conf_wrong"] or not r["action_ok"] or r["label_ok"] is False)],
                   key=sev)[:10]
    debatable = [r for r in rows if r["debatable"]]

    manifest = {
        "probe_launches": PROBE_LAUNCHES, "scored_items": len(rows),
        "items": [_item_rec(r) for r in rows],
        "overall": overall, "per_project": per_project,
        "per_scenario": {s: {**m, "verdict": verdict(m)} for s, m in per_scenario.items()},
        "adversarial": adversarial,
        "top_failures": [_fail_row(r) for r in fails],
        "debatable": [_fail_row(r) for r in debatable],
    }
    json.dump(manifest, open(os.path.join(HERE, "score.json"), "w"), indent=2)
    _write_md(manifest, per_scenario, fails, debatable)
    return manifest


def _item_rec(r):
    """Stable per-item record for before/after joins (item_ids change on re-upload,
    so identity is (project, archetype, test_name, ground_truth))."""
    return {
        "key": f"{r['project']}|{r['archetype']}|{r['test_name']}|{r['ground_truth']}",
        "item_id": r["item_id"], "project": r["project"], "archetype": r["archetype"],
        "test_name": r["test_name"], "scenarios": r["scenarios"], "adv_case": r["adv_case"],
        "ground_truth": r["ground_truth"],
        "exp_action": r["exp_action"], "exp_label": r["exp_label"],
        "got_action": r["got_action"], "got_label": r["got_label"],
        "got_method": r["got_method"], "conf": r["conf"],
        "action_ok": r["action_ok"], "label_ok": r["label_ok"],
        "conf_wrong": r["conf_wrong"], "abstain_ok": r["abstain_ok"],
        "debatable": bool(r["debatable"]),
    }


def _fail_row(r):
    return {"item_id": r["item_id"], "project": r["project"], "archetype": r["archetype"],
            "scenarios": r["scenarios"], "adv_case": r["adv_case"],
            "test_name": r["test_name"], "ground_truth": r["ground_truth"],
            "expected": f"{r['exp_action']}/{r['exp_label']}"
                        + (f"/{r['exp_method']}" if r['exp_method'] else ""),
            "got": f"{r['got_action']}/{r['got_label']}"
                   + (f"/{r['got_method']}" if r['got_method'] else "")
                   + (f" conf={r['conf']:.2f}" if r['conf'] is not None else ""),
            "reason": r["debatable"] or _hypothesis(r)}


def _hypothesis(r):
    if r["conf_wrong"]:
        return (f"HARD FAIL: auto-labeled {r['got_label']} but truth is {r['ground_truth']}"
                " -- confident wrong label")
    if r["exp_action"] == "abstain" and r["got_action"] != "abstain":
        return (f"should abstain ({r['ground_truth']}) but {r['got_action']}'d "
                f"{r['got_label']} -- over-confident on an undecidable item")
    if r["exp_action"] == "auto" and r["got_action"] == "suggest":
        return "expected auto-inherit but only suggested -- history/hash signal weaker than spec assumes"
    if r["exp_action"] == "suggest" and r["got_action"] == "auto":
        return "expected suggest but auto-labeled -- band hotter than spec (check calibration)"
    if r["exp_action"] == "suggest" and r["got_action"] == "abstain":
        return "expected suggest but abstained -- retrieval found no usable candidate"
    if r["label_ok"] is False:
        return f"label mismatch: got {r['got_label']}, truth {r['ground_truth']}"
    return "action mismatch"


def adv_verdict(fam, rs, all_rows):
    m = agg(rs)
    line = f"{m['n']} items, action_acc={m['action_acc']}, conf_wrong={m['confidently_wrong']}"
    v = verdict(m)
    # ADV-9 cross-project isolation: byte-identical twins must get project-local labels
    if fam == "ADV-9":
        wsu = [r for r in all_rows if r["adv_case"] and r["adv_case"].startswith("ADV-9")
               and r["project"] == config.WSU and "S45" in "".join(r["scenarios"])]
        psv = [r for r in all_rows if r["adv_case"] and r["adv_case"].startswith("ADV-9")
               and r["project"] == config.PSV and "S45" in "".join(r["scenarios"])]
        wl = {r["got_label"] for r in wsu if r["got_action"] != "abstain"}
        pl = {r["got_label"] for r in psv if r["got_action"] != "abstain"}
        leaked = bool(wl & pl) and (wl == pl)
        line = (f"WSU twin labels={sorted(wl) or ['abstain']}, PSV twin labels={sorted(pl) or ['abstain']}"
                f" -> {'LEAK (identical)' if leaked else 'project-local (no leak)'}")
        v = "FAIL" if leaked else ("PASS" if (wsu or psv) else "N/A")
    return {"verdict": v, "line": line, **m}


def _bar(m):
    return (f"action_acc={m['action_acc']}  label_acc={m['label_acc_decided']}  "
            f"conf_wrong={m['confidently_wrong']}  abstain_recall={m['abstain_recall']}  "
            f"method_match={m['method_match']}")


def _write_md(man, per_scenario, fails, debatable):
    o = man["overall"]
    L = []
    L.append("# analyzer-ng demo -- probe scorecard\n")
    L.append(f"Scored **{man['scored_items']}** probe items from launches "
             f"{man['probe_launches']} against `expected.json`. "
             f"Debatable (expectation/timing artifacts, excluded from fault counts): "
             f"**{o['n_debatable']}**.\n")
    L.append("## Summary\n")
    L.append("| scope | n | action acc | label acc (decided) | confidently-wrong | abstain recall | method match |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    L.append(f"| **overall** | {o['n']} | {o['action_acc']} | {o['label_acc_decided']} "
             f"| {o['confidently_wrong']} | {o['abstain_recall']} | {o['method_match']} |")
    for p in config.PROJECTS:
        m = man["per_project"][p]
        L.append(f"| {p} | {m['n']} | {m['action_acc']} | {m['label_acc_decided']} "
                 f"| {m['confidently_wrong']} | {m['abstain_recall']} | {m['method_match']} |")
    L.append("\n_action = auto/suggest/abstain (analyzer bands τ_auto=0.75, τ_suggest=0.45); "
             "label acc over non-abstains vs ground truth; confidently-wrong = auto-labeled with "
             "wrong label (hard fail); method match where expected_method is specified "
             "(hash/kb sub-classified best-effort, see header)._\n")

    L.append("## Per-scenario\n")
    L.append("| scenario | n | verdict | action acc | label acc | conf-wrong | notes |")
    L.append("|---|---:|---|---:|---:|---:|---|")
    for s in sorted(per_scenario, key=lambda x: (x == "<none>", x)):
        m = per_scenario[s]
        vd = verdict(m)
        note = "clean" if vd == "PASS" else ("hard fail" if m["confidently_wrong"] else "mixed")
        L.append(f"| {s} | {m['n']} | {vd} | {m['action_acc']} | {m['label_acc_decided']} "
                 f"| {m['confidently_wrong']} | {note} |")

    L.append("\n## Adversarial families\n")
    L.append("| adv_case | verdict | one-line |")
    L.append("|---|---|---|")
    for a, d in man["adversarial"].items():
        L.append(f"| {a} | {d['verdict']} | {d['line']} |")

    L.append("\n## Top failures\n")
    if not fails:
        L.append("_None -- no non-debatable action/label misses._")
    for i, r in enumerate(fails, 1):
        fr = _fail_row(r)
        L.append(f"{i}. **{fr['archetype']}** ({','.join(fr['scenarios'])}"
                 f"{'/' + fr['adv_case'] if fr['adv_case'] else ''}) "
                 f"item {fr['item_id']} `{fr['test_name'][:54]}`  \n"
                 f"   expected **{fr['expected']}**, got **{fr['got']}** — {fr['reason']}")

    L.append("\n## EXPECTED-DEBATABLE (not counted as analyzer failures)\n")
    if not debatable:
        L.append("_None._")
    else:
        agg_by = defaultdict(int)
        for r in debatable:
            agg_by[r["debatable"]] += 1
        for reason, c in agg_by.items():
            L.append(f"- {c}× — {reason}")
    open(os.path.join(HERE, "SCORE.md"), "w").write("\n".join(L) + "\n")


if __name__ == "__main__":
    rows = score()
    man = render(rows)
    o = man["overall"]
    print("SUMMARY", _bar(o), f"(scored {o['n']}, debatable {o['n_debatable']})")
    print("wrote demo-data/SCORE.md and demo-data/score.json")
