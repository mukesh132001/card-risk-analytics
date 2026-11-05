# Findings memo: loan risk at a Czech retail bank, 1993–1998

*Generated 2026-11-05 by `python -m card_analytics.findings`. Every number is computed from the reporting layer.*

**To:** Head of Retail Credit Risk · **From:** Mukesh Ethiraj, Analytics · **Data:** Berka (PKDD'99), 1,056,320 transactions, 682 loans

## Summary

The loan book holds 682 loans worth CZK 103.3m. 76 defaulted (11.1%), CZK 15.6m of principal. Most of that loss was predictable **before the loan was granted**: a model using only
pre-loan account behaviour flags 78% of defaulted principal. A simple rule, a collections queue and a
watch list, all built from data the bank already has, put that to work.

## Three recommendations

### 1. Refer any applicant with a pre-loan overdraft or penalty interest

31 loans went to accounts that had been overdrawn or charged penalty interest before the loan started. **28 of them defaulted (90%)**, against 7.4% for everyone else. Together they account for 37% of all defaulted principal.

- **Impact:** CZK 5.8m of defaulted principal; at 45% loss given default, **up to ≈ CZK 2.6m of losses the rule would have avoided**.
- **Cost:** 3 good loans (CZK 0.6m) would also have been referred; at a 3% margin that is ≈ CZK 0.1m of interest income at risk, and only if they were declined rather than approved after review.
- **Action:** add the rule to origination as a mandatory manual review. No model needed: it's two lookups on the account history.

### 2. Work the arrears queue in model-score order

45 live loans (CZK 11.2m) are already running in debt. The default model ranks 35 of them above its alert threshold, so collectors can start with the highest-risk accounts.

- **Impact:** expected loss on the queue is ≈ CZK 5.0m at 45% LGD; **every extra 10% recovered is worth ≈ CZK 0.5m**.
- **Action:** feed `rpt_loan_scores` into the collections system each month and track recovery rate by score band.

### 3. Put healthy loans that look like past defaulters on a watch list

66 of the 403 loans that are currently up to date (CZK 15.0m) score above the threshold. Among flagged loans that have already finished, 43% defaulted (51 loans).

- **Impact (scenario):** if 25% of the watch list defaults, well below that historical rate, expected loss is **≈ CZK 1.7m**. Early contact (payment plans, restructuring) is cheaper than collections.
- **Action:** monthly watch-list report to relationship managers; measure how many move into arrears vs a control group.

Some loans appear under more than one recommendation, so the three impacts overlap and shouldn't be added up.

## Supporting findings

- **The model.** Logistic regression on pre-loan behaviour flags 22% of loans and catches 75% of defaults (78% of defaulted principal); 38% of flags default vs 11% at random. See the [model card](model-card.md).
- **Segments.** Card holders (segment 1, 859 accounts) default on 5 of 165 loans; the cash-only, low-activity segment 3 (546 accounts) on 19 of 27. Segments use the whole history, so they are for portfolio monitoring, not loan decisions. See [segmentation](segmentation.md).
- **Data you can trust.** 72/72 months reconcile to the source to the cent, and the pipeline blocks any refresh that fails a control. See the [reconciliation report](reconciliation-report.md).

## Assumptions and limits

| Assumption | Value | Why |
| --- | ---: | --- |
| Loss given default | 45% | Basel II foundation-IRB supervisory LGD for senior unsecured corporate claims, used as a benchmark |
| Net interest margin | 3% a year | Illustrative; replace with the bank's own figure |
| Watch-list default scenario | 25% | Illustrative, deliberately below the historical rate |

Loss uses original loan amount as exposure (the data has no outstanding balances), so losses are upper bounds. The sample is small (682 loans, 76 defaults), and loans that started in 1998 have had little time to default. Amounts are in Czech koruna (CZK), the bank's currency.
