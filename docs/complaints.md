# Complaint classifier

Data: **generated, 1500 complaints**. 1125 train / 375 held-out test complaints (stratified, seed 42).

Pipeline: lower-case, mask numbers and redactions, spaCy lemmatize (`en_core_web_sm`), drop stop words, TF-IDF (1-2 grams), logistic regression (balanced class weights).

**Accuracy 98.1%, macro F1 0.981** on the test set.

> The labelled set is generated from templates, so these scores show the pipeline works. They are **not** an estimate of performance on real complaints. Run with `--csv` on the CFPB credit-card complaints for a realistic number.

## Confusion matrix (rows = true, columns = predicted)

| true \ predicted | fraud | dispute | fees | not recv | other |
|---|---:|---:|---:|---:|---:|
| fraud | 72 | 2 | 0 | 1 | 0 |
| disputed charge | 0 | 75 | 0 | 0 | 0 |
| fees/interest | 0 | 0 | 74 | 1 | 0 |
| card not received | 0 | 0 | 0 | 75 | 0 |
| other | 1 | 0 | 1 | 1 | 72 |

## Per class

| label | precision | recall | F1 | support |
|---|---:|---:|---:|---:|
| fraud | 0.99 | 0.96 | 0.97 | 75 |
| disputed charge | 0.97 | 1.00 | 0.99 | 75 |
| fees/interest | 0.99 | 0.99 | 0.99 | 75 |
| card not received | 0.96 | 1.00 | 0.98 | 75 |
| other | 1.00 | 0.96 | 0.98 | 75 |

## Strongest terms per class

- **card not received**: card, deliver, old, arrive mail, replacement card
- **disputed charge**: charge, bill, merchant, refund, good
- **fees/interest**: fee, interest, pay, charge annual, annual fee
- **fraud**: account, steal, purchase, thief spend, spend num
- **other**: phone, copy, copy statement, mortgage application, application
