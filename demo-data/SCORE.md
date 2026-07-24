# analyzer-ng demo -- probe scorecard

Scored **200** probe items from launches {'webshop-ui': 270, 'payments-services': 271, 'frontend-apps': 272} against `expected.json`. Debatable (expectation/timing artifacts, excluded from fault counts): **71**.

## Summary

| scope | n | action acc | label acc (decided) | confidently-wrong | abstain recall | method match |
|---|---:|---:|---:|---:|---:|---:|
| **overall** | 129 | 0.736 | 0.84 | 5 | 0.632 | 0.33 |
| webshop-ui | 83 | 0.699 | 0.877 | 3 | 0.154 | 0.342 |
| payments-services | 19 | 1.0 | 0.9 | 0 | 1.0 | 0.6 |
| frontend-apps | 27 | 0.667 | 0.545 | 2 | 0.812 | 0.071 |

_action = auto/suggest/abstain (analyzer bands τ_auto=0.75, τ_suggest=0.45); label acc over non-abstains vs ground truth; confidently-wrong = auto-labeled with wrong label (hard fail); method match where expected_method is specified (hash/kb sub-classified best-effort, see header)._

## Per-scenario

| scenario | n | verdict | action acc | label acc | conf-wrong | notes |
|---|---:|---|---:|---:|---:|---|
| S01 | 3 | PASS | 1.0 | 1.0 | 0 | clean |
| S02 | 12 | FAIL | 0.333 | 0.875 | 1 | hard fail |
| S03 | 5 | PASS | 1.0 | 1.0 | 0 | clean |
| S04 | 3 | FAIL | 0.0 | None | 0 | mixed |
| S05 | 12 | PASS | 1.0 | 1.0 | 0 | clean |
| S06 | 10 | FAIL | 0.2 | 0.875 | 1 | hard fail |
| S07 | 10 | FAIL | 0.2 | 0.875 | 1 | hard fail |
| S08 | 10 | FAIL | 0.2 | 0.875 | 1 | hard fail |
| S09 | 3 | PASS | 1.0 | 1.0 | 0 | clean |
| S10 | 2 | PASS | 1.0 | 1.0 | 0 | clean |
| S11 | 1 | FAIL | 0.0 | None | 0 | mixed |
| S12 | 4 | PASS | 1.0 | 1.0 | 0 | clean |
| S13 | 0 | N/A | None | None | 0 | mixed |
| S14 | 0 | N/A | None | None | 0 | mixed |
| S15 | 6 | FAIL | 1.0 | 0.667 | 2 | hard fail |
| S16 | 12 | FAIL | 0.083 | 0.273 | 0 | mixed |
| S17 | 0 | N/A | None | None | 0 | mixed |
| S18 | 8 | PASS | 1.0 | 0.833 | 0 | clean |
| S19 | 7 | PASS | 1.0 | 1.0 | 0 | clean |
| S20 | 0 | N/A | None | None | 0 | mixed |
| S21 | 4 | PASS | 1.0 | 1.0 | 0 | clean |
| S22 | 0 | N/A | None | None | 0 | mixed |
| S23 | 2 | FAIL | 0.0 | None | 0 | mixed |
| S24 | 2 | FAIL | 0.0 | None | 0 | mixed |
| S25 | 0 | N/A | None | None | 0 | mixed |
| S26 | 0 | N/A | None | None | 0 | mixed |
| S27 | 0 | N/A | None | None | 0 | mixed |
| S29 | 4 | PASS | 1.0 | 1.0 | 0 | clean |
| S30 | 0 | N/A | None | None | 0 | mixed |
| S32 | 0 | N/A | None | None | 0 | mixed |
| S33 | 15 | PARTIAL | 0.733 | 0.833 | 0 | mixed |
| S35 | 10 | FAIL | 0.2 | 0.875 | 1 | hard fail |
| S36 | 10 | FAIL | 0.2 | 0.875 | 1 | hard fail |
| S37 | 0 | N/A | None | None | 0 | mixed |
| S38 | 0 | N/A | None | None | 0 | mixed |
| S39 | 15 | FAIL | 0.4 | 0.875 | 1 | hard fail |
| S40 | 7 | FAIL | 0.429 | 0.6 | 2 | hard fail |
| S41 | 0 | N/A | None | None | 0 | mixed |
| S45 | 14 | PASS | 1.0 | 1.0 | 0 | clean |

## Adversarial families

| adv_case | verdict | one-line |
|---|---|---|
| ADV-1 | FAIL | 13 items, action_acc=0.077, conf_wrong=0 |
| ADV-2 | FAIL | 6 items, action_acc=1.0, conf_wrong=2 |
| ADV-3 | PASS | 8 items, action_acc=1.0, conf_wrong=0 |
| ADV-4 | PASS | 12 items, action_acc=1.0, conf_wrong=0 |
| ADV-5 | N/A | 0 items, action_acc=None, conf_wrong=0 |
| ADV-6 | PASS | 4 items, action_acc=1.0, conf_wrong=0 |
| ADV-7 | FAIL | 8 items, action_acc=0.375, conf_wrong=0 |
| ADV-8 | FAIL | 12 items, action_acc=0.167, conf_wrong=1 |
| ADV-9 | PASS | WSU twin labels=['pb'], PSV twin labels=['si'] -> project-local (no leak) |
| ADV-D1 | N/A | 0 items, action_acc=None, conf_wrong=0 |
| ADV-D4 | N/A | 0 items, action_acc=None, conf_wrong=0 |
| tie | PASS | 2 items, action_acc=1.0, conf_wrong=0 |

## Top failures

1. **JAVA-SEL-08** (S15/ADV-2) item 5110 `Checkout. Stock reservation. Reserve stock from invent`  
   expected **auto/pb/kb**, got **auto/ab/gbm conf=0.81** — HARD FAIL: auto-labeled ab but truth is pb -- confident wrong label
2. **JAVA-SEL-08** (S15/ADV-2) item 5113 `Checkout. Stock reservation. Reserve stock from invent`  
   expected **auto/pb/kb**, got **auto/ab/gbm conf=0.81** — HARD FAIL: auto-labeled ab but truth is pb -- confident wrong label
3. **JAVA-SEL-30** (S07,S08,S06,S39,S36,S35,S02/ADV-8) item 5203 `Checkout. Ledger. Reconcile ledger after checkout`  
   expected **abstain/ti**, got **auto/pb/gbm conf=0.75** — HARD FAIL: auto-labeled pb but truth is ti -- confident wrong label
4. **TS-PW-01** (S40) item 5256 `storefront/product-grid.spec.ts › Product grid › lazy-`  
   expected **suggest/nd/rule_cold**, got **auto/si/gbm conf=0.81** — HARD FAIL: auto-labeled si but truth is nd -- confident wrong label
5. **TS-PW-01** (S40) item 5257 `storefront/mini-cart.spec.ts › Mini cart › shows the i`  
   expected **suggest/nd/rule_cold**, got **auto/si/gbm conf=0.95** — HARD FAIL: auto-labeled si but truth is nd -- confident wrong label
6. **JAVA-SEL-05** (S16/ADV-1) item 5100 `Checkout. Order history. Load recent orders table`  
   expected **auto/pb/gbm**, got **suggest/si/gbm conf=0.45** — expected auto-inherit but only suggested -- history/hash signal weaker than spec assumes
7. **JAVA-SEL-05** (S16/ADV-1) item 5101 `Checkout. Order history. Load recent orders table`  
   expected **auto/pb/gbm**, got **suggest/si/gbm conf=0.45** — expected auto-inherit but only suggested -- history/hash signal weaker than spec assumes
8. **JAVA-SEL-05** (S16/ADV-1) item 5102 `Checkout. Order history. Load recent orders table`  
   expected **auto/pb/gbm**, got **suggest/si/gbm conf=0.45** — expected auto-inherit but only suggested -- history/hash signal weaker than spec assumes
9. **JAVA-SEL-06** (S16/ADV-1) item 5103 `Checkout. Order history. Load recent orders table`  
   expected **auto/si/gbm**, got **suggest/si/gbm conf=0.45** — expected auto-inherit but only suggested -- history/hash signal weaker than spec assumes
10. **JAVA-SEL-06** (S16/ADV-1) item 5104 `Checkout. Order history. Load recent orders table`  
   expected **auto/si/gbm**, got **suggest/si/gbm conf=0.45** — expected auto-inherit but only suggested -- history/hash signal weaker than spec assumes

## EXPECTED-DEBATABLE (not counted as analyzer failures)

- 71× — spec expected suggest; analyzer auto-labeled the CORRECT label (post-replay history richer than the spec's suggest assumption)
