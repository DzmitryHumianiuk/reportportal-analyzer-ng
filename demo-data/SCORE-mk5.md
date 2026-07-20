# analyzer-ng demo -- probe scorecard

Scored **200** probe items from launches {'webshop-ui': 173, 'payments-services': 174, 'frontend-apps': 175} against `expected.json`. Debatable (expectation/timing artifacts, excluded from fault counts): **68**.

## Summary

| scope | n | action acc | label acc (decided) | confidently-wrong | abstain recall | method match |
|---|---:|---:|---:|---:|---:|---:|
| **overall** | 132 | 0.773 | 0.787 | 6 | 0.684 | 0.34 |
| webshop-ui | 82 | 0.793 | 0.861 | 6 | 0.231 | 0.333 |
| payments-services | 22 | 0.773 | 1.0 | 0 | 1.0 | 0.692 |
| frontend-apps | 28 | 0.714 | 0.0 | 0 | 0.875 | 0.067 |

_action = auto/suggest/abstain (analyzer bands τ_auto=0.75, τ_suggest=0.45); label acc over non-abstains vs ground truth; confidently-wrong = auto-labeled with wrong label (hard fail); method match where expected_method is specified (hash/kb sub-classified best-effort, see header)._

## Per-scenario

| scenario | n | verdict | action acc | label acc | conf-wrong | notes |
|---|---:|---|---:|---:|---:|---|
| S01 | 3 | PASS | 1.0 | 1.0 | 0 | clean |
| S02 | 12 | FAIL | 0.417 | 1.0 | 0 | mixed |
| S03 | 5 | PASS | 1.0 | 1.0 | 0 | clean |
| S04 | 3 | FAIL | 0.0 | None | 0 | mixed |
| S05 | 12 | PASS | 1.0 | 1.0 | 0 | clean |
| S06 | 10 | FAIL | 0.3 | 1.0 | 0 | mixed |
| S07 | 10 | FAIL | 0.3 | 1.0 | 0 | mixed |
| S08 | 10 | FAIL | 0.3 | 1.0 | 0 | mixed |
| S09 | 3 | PASS | 1.0 | 1.0 | 0 | clean |
| S10 | 2 | PASS | 1.0 | 1.0 | 0 | clean |
| S11 | 0 | N/A | None | None | 0 | mixed |
| S12 | 4 | PASS | 1.0 | 1.0 | 0 | clean |
| S13 | 0 | N/A | None | None | 0 | mixed |
| S14 | 0 | N/A | None | None | 0 | mixed |
| S15 | 6 | PASS | 1.0 | 1.0 | 0 | clean |
| S16 | 12 | FAIL | 0.75 | 0.333 | 6 | hard fail |
| S17 | 0 | N/A | None | None | 0 | mixed |
| S18 | 9 | PARTIAL | 0.667 | 0.5 | 0 | mixed |
| S19 | 7 | PASS | 1.0 | 1.0 | 0 | clean |
| S20 | 0 | N/A | None | None | 0 | mixed |
| S21 | 4 | PARTIAL | 1.0 | 0.5 | 0 | mixed |
| S22 | 2 | FAIL | 0.0 | None | 0 | mixed |
| S23 | 2 | FAIL | 0.0 | None | 0 | mixed |
| S24 | 2 | FAIL | 0.0 | None | 0 | mixed |
| S25 | 0 | N/A | None | None | 0 | mixed |
| S26 | 0 | N/A | None | None | 0 | mixed |
| S27 | 0 | N/A | None | None | 0 | mixed |
| S29 | 4 | PASS | 1.0 | 1.0 | 0 | clean |
| S30 | 0 | N/A | None | None | 0 | mixed |
| S32 | 0 | N/A | None | None | 0 | mixed |
| S33 | 15 | PARTIAL | 0.533 | 0.4 | 0 | mixed |
| S35 | 10 | FAIL | 0.3 | 1.0 | 0 | mixed |
| S36 | 10 | FAIL | 0.3 | 1.0 | 0 | mixed |
| S37 | 0 | N/A | None | None | 0 | mixed |
| S38 | 0 | N/A | None | None | 0 | mixed |
| S39 | 15 | FAIL | 0.467 | 1.0 | 0 | mixed |
| S40 | 8 | PARTIAL | 0.75 | 0.0 | 0 | mixed |
| S41 | 0 | N/A | None | None | 0 | mixed |
| S45 | 14 | PASS | 1.0 | 1.0 | 0 | clean |

## Adversarial families

| adv_case | verdict | one-line |
|---|---|---|
| ADV-1 | FAIL | 12 items, action_acc=0.75, conf_wrong=6 |
| ADV-2 | PASS | 6 items, action_acc=1.0, conf_wrong=0 |
| ADV-3 | PARTIAL | 9 items, action_acc=0.667, conf_wrong=0 |
| ADV-4 | PASS | 12 items, action_acc=1.0, conf_wrong=0 |
| ADV-5 | FAIL | 2 items, action_acc=0.0, conf_wrong=0 |
| ADV-6 | PARTIAL | 4 items, action_acc=1.0, conf_wrong=0 |
| ADV-7 | FAIL | 8 items, action_acc=0.375, conf_wrong=0 |
| ADV-8 | FAIL | 12 items, action_acc=0.25, conf_wrong=0 |
| ADV-9 | PASS | WSU twin labels=['pb'], PSV twin labels=['si'] -> project-local (no leak) |
| ADV-D1 | N/A | 0 items, action_acc=None, conf_wrong=0 |
| ADV-D4 | N/A | 0 items, action_acc=None, conf_wrong=0 |
| tie | PASS | 2 items, action_acc=1.0, conf_wrong=0 |

## Top failures

1. **JAVA-SEL-06** (S16/ADV-1) item 2553 `Checkout. Order history. Load recent orders table`  
   expected **auto/si/gbm**, got **auto/pb/gbm conf=0.95** — HARD FAIL: auto-labeled pb but truth is si -- confident wrong label
2. **JAVA-SEL-06** (S16/ADV-1) item 2554 `Checkout. Order history. Load recent orders table`  
   expected **auto/si/gbm**, got **auto/pb/gbm conf=0.95** — HARD FAIL: auto-labeled pb but truth is si -- confident wrong label
3. **JAVA-SEL-06** (S16/ADV-1) item 2555 `Checkout. Order history. Load recent orders table`  
   expected **auto/si/gbm**, got **auto/pb/gbm conf=0.95** — HARD FAIL: auto-labeled pb but truth is si -- confident wrong label
4. **JAVA-SEL-07** (S16/ADV-1) item 2556 `Checkout. Order history. Load recent orders table`  
   expected **abstain/ti/gbm**, got **auto/pb/gbm conf=0.95** — HARD FAIL: auto-labeled pb but truth is ti -- confident wrong label
5. **JAVA-SEL-07** (S16/ADV-1) item 2557 `Checkout. Order history. Load recent orders table`  
   expected **abstain/ti/gbm**, got **auto/pb/gbm conf=0.95** — HARD FAIL: auto-labeled pb but truth is ti -- confident wrong label
6. **JAVA-SEL-07** (S16/ADV-1) item 2558 `Checkout. Order history. Load recent orders table`  
   expected **abstain/ti/gbm**, got **auto/pb/gbm conf=0.95** — HARD FAIL: auto-labeled pb but truth is ti -- confident wrong label
7. **JAVA-SEL-15** (S04/ADV-7) item 2596 `Checkout. Export. Download order history CSV`  
   expected **auto/pb/hash**, got **abstain/ti/gbm conf=0.38** — action mismatch
8. **JAVA-SEL-15** (S04/ADV-7) item 2597 `Checkout. Export. Download order history CSV`  
   expected **auto/pb/hash**, got **abstain/ti/gbm conf=0.38** — action mismatch
9. **JAVA-SEL-15** (S04/ADV-7) item 2598 `Checkout. Export. Download order history CSV`  
   expected **auto/pb/hash**, got **abstain/ti/gbm conf=0.38** — action mismatch
10. **JAVA-SEL-16** (S23/ADV-7) item 2599 `Checkout. Cart. Recalculate total after coupon removal`  
   expected **suggest/pb/gbm**, got **abstain/ti** — expected suggest but abstained -- retrieval found no usable candidate

## EXPECTED-DEBATABLE (not counted as analyzer failures)

- 68× — spec expected suggest; analyzer auto-labeled the CORRECT label (post-replay history richer than the spec's suggest assumption)
