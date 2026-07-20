# analyzer-ng demo -- probe scorecard

Scored **200** probe items from launches {'webshop-ui': 161, 'payments-services': 162, 'frontend-apps': 163} against `expected.json`. Debatable (expectation/timing artifacts, excluded from fault counts): **59**.

## Summary

| scope | n | action acc | label acc (decided) | confidently-wrong | abstain recall | method match |
|---|---:|---:|---:|---:|---:|---:|
| **overall** | 141 | 0.73 | 0.745 | 12 | 0.763 | 0.275 |
| webshop-ui | 88 | 0.727 | 0.849 | 6 | 0.385 | 0.282 |
| payments-services | 25 | 0.68 | 0.571 | 6 | 1.0 | 0.438 |
| frontend-apps | 28 | 0.786 | 0.273 | 0 | 0.938 | 0.067 |

_action = auto/suggest/abstain (analyzer bands τ_auto=0.75, τ_suggest=0.45); label acc over non-abstains vs ground truth; confidently-wrong = auto-labeled with wrong label (hard fail); method match where expected_method is specified (hash/kb sub-classified best-effort, see header)._

## Per-scenario

| scenario | n | verdict | action acc | label acc | conf-wrong | notes |
|---|---:|---|---:|---:|---:|---|
| S01 | 3 | PASS | 1.0 | 1.0 | 0 | clean |
| S02 | 12 | FAIL | 0.333 | 0.875 | 0 | mixed |
| S03 | 5 | PASS | 1.0 | 1.0 | 0 | clean |
| S04 | 3 | PASS | 1.0 | 1.0 | 0 | clean |
| S05 | 12 | PASS | 1.0 | 1.0 | 0 | clean |
| S06 | 10 | FAIL | 0.2 | 0.875 | 0 | mixed |
| S07 | 10 | FAIL | 0.2 | 0.875 | 0 | mixed |
| S08 | 10 | FAIL | 0.2 | 0.875 | 0 | mixed |
| S09 | 3 | PASS | 1.0 | 1.0 | 0 | clean |
| S10 | 2 | PASS | 1.0 | 1.0 | 0 | clean |
| S11 | 0 | N/A | None | None | 0 | mixed |
| S12 | 4 | PASS | 1.0 | 1.0 | 0 | clean |
| S13 | 0 | N/A | None | None | 0 | mixed |
| S14 | 0 | N/A | None | None | 0 | mixed |
| S15 | 6 | PASS | 1.0 | 1.0 | 0 | clean |
| S16 | 12 | PARTIAL | 0.5 | None | 0 | mixed |
| S17 | 0 | N/A | None | None | 0 | mixed |
| S18 | 9 | FAIL | 0.667 | 0.286 | 3 | hard fail |
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
| S33 | 15 | PARTIAL | 0.667 | 0.714 | 0 | mixed |
| S35 | 10 | FAIL | 0.2 | 0.875 | 0 | mixed |
| S36 | 10 | FAIL | 0.2 | 0.875 | 0 | mixed |
| S37 | 6 | FAIL | 0.0 | 0.0 | 6 | hard fail |
| S38 | 0 | N/A | None | None | 0 | mixed |
| S39 | 15 | FAIL | 0.4 | 0.875 | 0 | mixed |
| S40 | 8 | PARTIAL | 0.75 | 0.0 | 0 | mixed |
| S41 | 3 | FAIL | 0.0 | 0.0 | 3 | hard fail |
| S45 | 14 | PASS | 1.0 | 1.0 | 0 | clean |

## Adversarial families

| adv_case | verdict | one-line |
|---|---|---|
| ADV-1 | PARTIAL | 12 items, action_acc=0.5, conf_wrong=0 |
| ADV-2 | PASS | 6 items, action_acc=1.0, conf_wrong=0 |
| ADV-3 | FAIL | 9 items, action_acc=0.667, conf_wrong=3 |
| ADV-4 | FAIL | 18 items, action_acc=0.667, conf_wrong=6 |
| ADV-5 | FAIL | 2 items, action_acc=0.0, conf_wrong=0 |
| ADV-6 | PARTIAL | 4 items, action_acc=1.0, conf_wrong=0 |
| ADV-7 | PARTIAL | 8 items, action_acc=0.75, conf_wrong=0 |
| ADV-8 | FAIL | 12 items, action_acc=0.167, conf_wrong=0 |
| ADV-9 | PASS | WSU twin labels=['pb'], PSV twin labels=['si'] -> project-local (no leak) |
| ADV-D1 | N/A | 0 items, action_acc=None, conf_wrong=0 |
| ADV-D4 | N/A | 0 items, action_acc=None, conf_wrong=0 |
| tie | PASS | 2 items, action_acc=1.0, conf_wrong=0 |

## Top failures

1. **JAVA-SEL-12** (S37/ADV-4) item 1777 `Account. Session. Reject expired session cookie`  
   expected **suggest/ab/gbm**, got **auto/pb/hash conf=0.95** — HARD FAIL: auto-labeled pb but truth is ab -- confident wrong label
2. **JAVA-SEL-12** (S37/ADV-4) item 1779 `Account. Session. Reject expired session cookie`  
   expected **suggest/ab/gbm**, got **auto/pb/hash conf=0.95** — HARD FAIL: auto-labeled pb but truth is ab -- confident wrong label
3. **JAVA-SEL-12** (S37/ADV-4) item 1781 `Account. Session. Reject expired session cookie`  
   expected **suggest/ab/gbm**, got **auto/pb/hash conf=0.95** — HARD FAIL: auto-labeled pb but truth is ab -- confident wrong label
4. **JAVA-SEL-12** (S37/ADV-4) item 1783 `Account. Session. Reject expired session cookie`  
   expected **suggest/ab/gbm**, got **auto/pb/hash conf=0.95** — HARD FAIL: auto-labeled pb but truth is ab -- confident wrong label
5. **JAVA-SEL-12** (S37/ADV-4) item 1785 `Account. Session. Reject expired session cookie`  
   expected **suggest/ab/gbm**, got **auto/pb/hash conf=0.95** — HARD FAIL: auto-labeled pb but truth is ab -- confident wrong label
6. **JAVA-SEL-12** (S37/ADV-4) item 1787 `Account. Session. Reject expired session cookie`  
   expected **suggest/ab/gbm**, got **auto/pb/hash conf=0.95** — HARD FAIL: auto-labeled pb but truth is ab -- confident wrong label
7. **NET-XUN-03** (S18/ADV-3) item 1828 `Hawkins.Payments.Transfers.Tests.TransferApiTests.Post`  
   expected **suggest/si/gbm**, got **auto/pb/hash conf=0.95** — HARD FAIL: auto-labeled pb but truth is si -- confident wrong label
8. **NET-XUN-03** (S18/ADV-3) item 1830 `Hawkins.Payments.Transfers.Tests.TransferApiTests.Post`  
   expected **suggest/si/gbm**, got **auto/pb/hash conf=0.95** — HARD FAIL: auto-labeled pb but truth is si -- confident wrong label
9. **NET-XUN-03** (S18/ADV-3) item 1832 `Hawkins.Payments.Transfers.Tests.TransferApiTests.Post`  
   expected **suggest/si/gbm**, got **auto/pb/hash conf=0.95** — HARD FAIL: auto-labeled pb but truth is si -- confident wrong label
10. **NET-XUN-08** (S41) item 1848 `Hawkins.Payments.MoneyMovement.Tests.InstantPayoutsE2E`  
   expected **suggest/nd/gbm**, got **auto/pb/hash conf=0.95** — HARD FAIL: auto-labeled pb but truth is nd -- confident wrong label

## EXPECTED-DEBATABLE (not counted as analyzer failures)

- 59× — spec expected suggest; analyzer auto-labeled the CORRECT label (post-replay history richer than the spec's suggest assumption)
