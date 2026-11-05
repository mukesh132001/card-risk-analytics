# Assistant evaluation

Generated 2026-11-05 22:03 UTC by `python -m card_analytics.assistant --eval`.

**Mode: offline.** Measures retrieval hit rate (expected doc in the top 3) for docs questions; reference SQL correctness for sql questions.

**Overall: 50/50 (100%), target 85%: PASS.** Docs 40/40, SQL 10/10.

| ID | Question | Pass | Got |
| --- | --- | :---: | --- |
| d01 | What does the is_overdrawn column mean? | ✓ | docs/model-card.md, docs/data-dictionary.md |
| d02 | What is the grain of fact_transactions? | ✓ | docs/data-dictionary.md, docs/lineage.md |
| d03 | Which client columns are quasi-identifiers? | ✓ | docs/data-dictionary.md |
| d04 | How are client IDs masked in the reporting layer? | ✓ | docs/data-dictionary.md |
| d05 | What values can age_band take? | ✓ | docs/data-dictionary.md, README.md |
| d06 | What are the possible statement frequencies on an account? | ✓ | docs/data-dictionary.md |
| d07 | When is loan_is_default set to 1? | ✓ | docs/data-dictionary.md, README.md |
| d08 | What PII class is account_id? | ✓ | docs/data-dictionary.md |
| d09 | Which tables are analysts and dashboards allowed to read? | ✓ | docs/data-dictionary.md, README.md, docs/reconciliation-report.md |
| d10 | What does signed_amount_czk mean? | ✓ | docs/model-card.md, docs/data-dictionary.md |
| d11 | How is the lineage diagram kept up to date? | ✓ | README.md, docs/lineage.md |
| d12 | Which table is dim_date built from? | ✓ | README.md, docs/lineage.md |
| d13 | What happens in the pipeline when a data-quality check fails? | ✓ | README.md, docs/findings-memo.md |
| d14 | How many months reconcile to the source? | ✓ | docs/reconciliation-report.md, docs/findings-memo.md |
| d15 | How are amounts compared in the reconciliation? | ✓ | docs/reconciliation-report.md, docs/model-card.md |
| d16 | What is the total loan amount in the source and the model? | ✓ | docs/model-card.md, docs/reconciliation-report.md |
| d17 | How was the number of customer segments chosen? | ✓ | README.md, docs/segmentation.md |
| d18 | How many customer segments did k-means pick? | ✓ | README.md, docs/segmentation.md |
| d19 | Which customer segment has the highest loan default rate? | ✓ | docs/segmentation.md, docs/model-card.md |
| d20 | What share of accounts are in segment 4? | ✓ | docs/segmentation.md, docs/findings-memo.md |
| d21 | What features does the customer segmentation use? | ✓ | docs/segmentation.md, README.md |
| d22 | What does the loan default model predict? | ✓ | docs/model-card.md |
| d23 | Why is card ownership left out of the default model? | ✓ | docs/model-card.md, README.md |
| d24 | How was the alert threshold for the default model chosen? | ✓ | docs/model-card.md, docs/findings-memo.md |
| d25 | Why was logistic regression chosen over gradient boosting? | ✓ | docs/model-card.md, README.md |
| d26 | How was the default model validated? | ✓ | docs/model-card.md |
| d27 | What share of loans does the default model flag? | ✓ | docs/model-card.md |
| d28 | What are the limitations of the default model? | ✓ | docs/model-card.md, README.md |
| d29 | Why is the default rate for 1998 loans understated? | ✓ | docs/model-card.md, docs/findings-memo.md |
| d30 | What is the first recommendation in the findings memo? | ✓ | docs/findings-memo.md |
| d31 | What loss given default does the findings memo assume? | ✓ | docs/findings-memo.md |
| d32 | How much are the loans currently in arrears worth? | ✓ | docs/findings-memo.md |
| d33 | How many healthy loans are on the watch list? | ✓ | docs/findings-memo.md |
| d34 | What net interest margin does the memo assume? | ✓ | docs/findings-memo.md, docs/model-card.md |
| d35 | What is the overall loan default rate? | ✓ | docs/model-card.md |
| d36 | Can the impacts of the three recommendations be added together? | ✓ | docs/findings-memo.md |
| d37 | Which dataset does the project use? | ✓ | docs/model-card.md, README.md |
| d38 | What command runs the full pipeline? | ✓ | README.md |
| d39 | What is on the Summary tab of the Excel MI pack? | ✓ | README.md, docs/lineage.md |
| d40 | How many integrity checks does the Berka loader run? | ✓ | README.md, docs/model-card.md, docs/lineage.md |
| s01 | How many transactions were there in 1997? | ✓ | 284409 |
| s02 | How many accounts are in customer segment 2? | ✓ | 1885 |
| s03 | Which region has the most loans? | ✓ | south Moravia |
| s04 | How many loans does the default model flag? | ✓ | 152 |
| s05 | How many districts are there? | ✓ | 77 |
| s06 | How many accounts in the Prague region have a card? | ✓ | 132 |
| s07 | How many transactions left an account overdrawn, in total? | ✓ | 2999 |
| s08 | How many loans started in 1997? | ✓ | 196 |
| s09 | How many accounts have a gold card? | ✓ | 88 |
| s10 | How many clients are in the 65+ age band? | ✓ | 818 |
