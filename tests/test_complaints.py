"""Complaint classifier tests."""
import pandas as pd

from card_analytics import complaints


def test_clean_lemmatizes_and_drops_noise():
    out = complaints.clean(["The charges were declined!! Amount $250.00 XXXX"])[0].split()
    assert "charge" in out and "decline" in out
    assert "the" not in out and "250" not in out and "xxxx" not in out


def test_generate_is_balanced_and_reproducible():
    a, b = complaints.generate(100, seed=1), complaints.generate(100, seed=1)
    assert a.equals(b)
    assert set(a.label) == set(complaints.LABELS)
    assert a.label.value_counts().nunique() == 1


def test_train_eval_beats_chance_and_confusion_shape():
    res = complaints.train_eval(complaints.generate(600))
    assert res["accuracy"] > 0.8
    assert res["confusion"].shape == (5, 5)
    assert res["confusion"].sum() == res["n_test"]


def test_classify_obvious_examples():
    model = complaints.train_eval(complaints.generate(600))["model"]
    got = complaints.classify(model, [
        "someone stole my card details and made purchases I never authorized",
        "my replacement card never arrived in the mail",
        "you charged me an annual fee I was never told about",
    ])
    assert got == ["fraud", "card not received", "fees/interest"]


def test_cfpb_issue_mapping_and_loader(tmp_path):
    assert complaints.map_cfpb_issue("Fraud or scam") == "fraud"
    assert complaints.map_cfpb_issue("Fees or interest") == "fees/interest"
    assert complaints.map_cfpb_issue("Problem with a purchase shown on your statement") == "disputed charge"
    assert complaints.map_cfpb_issue("Closing your account") == "other"
    p = tmp_path / "c.csv"
    pd.DataFrame({"Issue": ["Fraud or scam", "Other"], "Sub-issue": ["", ""],
                  "Consumer complaint narrative": ["stolen", None]}).to_csv(p, index=False)
    assert list(complaints.load_cfpb(str(p)).label) == ["fraud"]


def test_main_writes_doc(tmp_path):
    doc = tmp_path / "c.md"
    assert complaints.main(["--rows", "500", "--doc", str(doc)]) == 0
    text = doc.read_text(encoding="utf-8")
    assert "Confusion matrix" in text and "not** an estimate" in text
