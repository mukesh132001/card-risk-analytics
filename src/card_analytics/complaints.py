"""Complaint / dispute text classifier: spaCy cleaning + TF-IDF + logistic regression.

Usage:
    python -m card_analytics.complaints                       # generated labelled set
    python -m card_analytics.complaints --csv complaints.csv  # CFPB credit-card complaints

Labels: fraud, disputed charge, fees/interest, card not received, other.

With no CSV a labelled set is GENERATED from templates (seeded, reproducible). It is
synthetic, so the scores prove the pipeline works, not how it would do on real
customer text. Pass the CFPB export (columns "Issue" and "Consumer complaint
narrative") to get honest numbers. Writes docs/complaints.md with the confusion matrix.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_recall_fscore_support
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

SEED = 42
LABELS = ["fraud", "disputed charge", "fees/interest", "card not received", "other"]
DOC = Path(__file__).resolve().parents[2] / "docs" / "complaints.md"

_nlp = None


def get_nlp():
    """Load spaCy once. Parser and NER are off: only the lemmatizer is needed."""
    global _nlp
    if _nlp is None:
        import spacy
        try:
            _nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
        except OSError as e:
            raise SystemExit("spaCy model missing: python -m spacy download en_core_web_sm") from e
    return _nlp


def _pre(text: str) -> str:
    text = str(text).lower()
    text = re.sub(r"x{2,}(/x{2,})*", " ", text)          # CFPB redaction (XXXX, XX/XX/XXXX)
    text = re.sub(r"\$?\d[\d,.]*", " num ", text)         # amounts and dates carry no class signal
    return re.sub(r"\s+", " ", text).strip()


def clean(texts) -> list[str]:
    """Lower-case, mask numbers/redactions, lemmatize, drop stop words and punctuation."""
    nlp = get_nlp()
    out = []
    for doc in nlp.pipe((_pre(t) for t in texts), batch_size=256):
        out.append(" ".join(t.lemma_ for t in doc if t.is_alpha and not t.is_stop and len(t.lemma_) > 1))
    return out


# ---- generated labelled set -------------------------------------------------

_OPEN = ["Hello,", "I am writing because", "I need help.", "Please look into this.", "To whom it may concern,", ""]
_BODY = {
    "fraud": [
        "someone stole my card details and made purchases I never authorized",
        "there are transactions on my account from a country I have never visited",
        "my card was cloned and the thief spent {amt} before I noticed",
        "I think my identity was stolen, an account was opened in my name",
        "I received an alert for a purchase I did not make and suspect fraud",
        "a scammer tricked me into giving my one-time code and money left my account",
    ],
    "disputed charge": [
        "I was charged {amt} twice by the same merchant for one order",
        "the merchant billed me for a service I cancelled and refuses to refund",
        "I returned the item weeks ago but the refund never appeared on my statement",
        "the amount charged does not match the receipt, I want to dispute this charge",
        "I was billed for goods that never arrived from the seller",
        "the shop charged me more than the price we agreed and will not correct it",
    ],
    "fees/interest": [
        "you charged me an annual fee I was never told about",
        "my interest rate went up to 29 percent without any notice",
        "I was hit with a late fee even though I paid on time",
        "an overlimit fee and penalty interest of {amt} appeared on my statement",
        "the cash advance fee is far higher than what the contract says",
        "I keep paying interest on a balance I thought was at zero percent",
    ],
    "card not received": [
        "my replacement card never arrived in the mail",
        "I ordered a new credit card two weeks ago and it still has not been delivered",
        "the card was sent to an old address and I have not received it",
        "I am still waiting for my card, the tracking says delivered but I never got it",
        "the new card has not come and my old one is expired",
        "I applied, was approved, but no card or pin letter ever showed up",
    ],
    "other": [
        "I want to close my account and nobody answers the phone",
        "the mobile app keeps crashing when I try to log in",
        "customer service was rude and could not explain my credit limit",
        "please update my phone number and mailing preferences",
        "I need a copy of my statements for a mortgage application",
        "my credit score dropped and I do not understand the reporting",
    ],
}
_CLOSE = ["Please fix this.", "I have called several times.", "This is unacceptable.", "Thank you.", "", ""]
_NOISE = [  # sentences shared across classes, so words alone do not give the label away
    "I have been a customer for years.",
    "I called the helpline and was told to wait.",
    "I also checked my statement online.",
    "The agent said it would take ten days.",
]


def generate(n: int = 1500, seed: int = SEED) -> pd.DataFrame:
    """Generated labelled complaints. About 8% pick up a sentence from another class."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        label = LABELS[i % len(LABELS)]
        parts = [rng.choice(_OPEN), rng.choice(_BODY[label]).format(amt=f"${rng.randint(20, 900)}")]
        if rng.random() < 0.5:
            parts.append(rng.choice(_NOISE))
        if rng.random() < 0.08:
            other = rng.choice([x for x in LABELS if x != label])
            parts.append("Also, " + rng.choice(_BODY[other]).format(amt=f"${rng.randint(20, 900)}") + ".")
        parts.append(rng.choice(_CLOSE))
        rows.append((" ".join(p for p in parts if p), label))
    return pd.DataFrame(rows, columns=["text", "label"])


# ---- CFPB loader --------------------------------------------------------------

def map_cfpb_issue(issue: str) -> str:
    s = str(issue).lower()
    if "fraud" in s or "scam" in s or "identity" in s or "unauthorized" in s:
        return "fraud"
    if "not received" in s or "never received" in s or "getting a credit card" in s or "card was not" in s:
        return "card not received"
    if "fee" in s or "interest" in s or "apr" in s or "rate" in s:
        return "fees/interest"
    if "dispute" in s or "billing" in s or "purchase" in s or "charge" in s or "refund" in s:
        return "disputed charge"
    return "other"


def load_cfpb(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, usecols=lambda c: c in ("Issue", "Sub-issue", "Consumer complaint narrative"))
    df = df.dropna(subset=["Consumer complaint narrative", "Issue"])
    sub = df["Sub-issue"].fillna("") if "Sub-issue" in df else ""
    return pd.DataFrame({"text": df["Consumer complaint narrative"], "label": (df["Issue"] + " " + sub).map(map_cfpb_issue)})


# ---- model --------------------------------------------------------------------

def build_model() -> Pipeline:
    return Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
        ("clf", LogisticRegression(max_iter=1000, C=5.0, class_weight="balanced", random_state=SEED)),
    ])


def top_terms(model: Pipeline, k: int = 5) -> dict[str, list[str]]:
    names = np.array(model.named_steps["tfidf"].get_feature_names_out())
    clf = model.named_steps["clf"]
    return {c: list(names[np.argsort(clf.coef_[i])[::-1][:k]]) for i, c in enumerate(clf.classes_)}


def train_eval(df: pd.DataFrame, test_size: float = 0.25) -> dict:
    """Clean, split (stratified), fit on train, score on held-out test."""
    X = clean(df["text"])
    y = df["label"].to_numpy()
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=test_size, stratify=y, random_state=SEED)
    model = build_model().fit(X_tr, y_tr)
    pred = model.predict(X_te)
    p, r, f, s = precision_recall_fscore_support(y_te, pred, labels=LABELS, zero_division=0)
    return {
        "model": model, "n_train": len(X_tr), "n_test": len(X_te),
        "accuracy": accuracy_score(y_te, pred),
        "macro_f1": f1_score(y_te, pred, average="macro", labels=LABELS, zero_division=0),
        "confusion": confusion_matrix(y_te, pred, labels=LABELS),
        "per_class": pd.DataFrame({"label": LABELS, "precision": p, "recall": r, "f1": f, "support": s}),
        "top_terms": top_terms(model),
    }


def classify(model: Pipeline, texts) -> list[str]:
    return list(model.predict(clean(texts)))


def write_doc(res: dict, source: str, path: Path = DOC) -> None:
    short = ["fraud", "dispute", "fees", "not recv", "other"]
    lines = [
        "# Complaint classifier", "",
        f"Data: **{source}**. {res['n_train']} train / {res['n_test']} held-out test complaints (stratified, seed {SEED}).", "",
        "Pipeline: lower-case, mask numbers and redactions, spaCy lemmatize (`en_core_web_sm`), drop stop words, "
        "TF-IDF (1-2 grams), logistic regression (balanced class weights).", "",
        f"**Accuracy {res['accuracy']:.1%}, macro F1 {res['macro_f1']:.3f}** on the test set.", "",
    ]
    if source.startswith("generated"):
        lines += ["> The labelled set is generated from templates, so these scores show the pipeline works. "
                  "They are **not** an estimate of performance on real complaints. Run with `--csv` on the "
                  "CFPB credit-card complaints for a realistic number.", ""]
    lines += ["## Confusion matrix (rows = true, columns = predicted)", "",
              "| true \\ predicted | " + " | ".join(short) + " |", "|---|" + "---:|" * len(short)]
    for lab, row in zip(LABELS, res["confusion"]):
        lines.append(f"| {lab} | " + " | ".join(str(v) for v in row) + " |")
    lines += ["", "## Per class", "", "| label | precision | recall | F1 | support |", "|---|---:|---:|---:|---:|"]
    for r in res["per_class"].itertuples():
        lines.append(f"| {r.label} | {r.precision:.2f} | {r.recall:.2f} | {r.f1:.2f} | {r.support} |")
    lines += ["", "## Strongest terms per class", ""]
    lines += [f"- **{c}**: {', '.join(t)}" for c, t in res["top_terms"].items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--csv", help="CFPB credit-card complaints CSV (default: generated set)")
    ap.add_argument("--rows", type=int, default=1500, help="size of the generated set")
    ap.add_argument("--doc", default=str(DOC))
    a = ap.parse_args(argv)
    if a.csv:
        df, source = load_cfpb(a.csv), f"CFPB complaints ({Path(a.csv).name})"
    else:
        df, source = generate(a.rows), f"generated, {a.rows} complaints"
    res = train_eval(df)
    write_doc(res, source, Path(a.doc))
    print(f"accuracy {res['accuracy']:.3f}  macro F1 {res['macro_f1']:.3f}  -> {a.doc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
