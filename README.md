# Card Risk Analytics

Analytics, ML and an AI assistant on card transaction data: a tested SQL model, reconciled numbers, BI dashboards, a fraud model and a Claude-powered colleague assistant.

Built to mirror how bank data, BI and risk teams work: **SQL + dbt on Databricks/Snowflake, Python (pandas, scikit-learn, spaCy), Tableau / Power BI, data-quality controls, and GenAI with RAG**.

> Status: 🚧 in progress. See the [roadmap](#roadmap) and the GitHub Issues board.

## Pipeline

```mermaid
flowchart LR
    RAW[Raw transactions<br/>CSV / Kafka export] --> STG[dbt staging]
    STG --> MART[Star schema<br/>fact_transactions · dim_customer · dim_merchant · dim_date]
    MART --> DQ{Data-quality and<br/>reconciliation checks}
    DQ -->|pass| BI[Tableau / Power BI dashboards]
    DQ -->|pass| ML[Fraud and default models]
    MART --> AI[Colleague assistant<br/>RAG + MCP tools]
    DQ -->|fail| STOP[Block refresh + alert]
```

## Tech stack

| Area | Tools |
| --- | --- |
| Storage / compute | PostgreSQL locally → Databricks or Snowflake |
| Modeling | SQL, dbt (staging → marts, tests) |
| Python | pandas, scikit-learn, spaCy, pytest |
| BI | Tableau Public, Power BI, Excel MI pack |
| Orchestration | GitHub Actions / Airflow |
| Governance | Data dictionary, lineage, PII masking, reconciliation report |
| AI | Claude (Anthropic API / AWS Bedrock), embeddings + pgvector, MCP tools |

## Repository layout

```
data/sample/         Small synthetic dataset (committed)
data/raw/            Full datasets (git-ignored)
dbt/                 dbt project: staging and mart models + tests
src/card_analytics/  Python package: data generation, quality checks, models
tests/               pytest suite
notebooks/           Exploration and model notebooks
dashboards/          Tableau / Power BI files and screenshots
assistant/           RAG + agent colleague assistant
docs/                Data dictionary, lineage, findings memo
```

## Roadmap

- [ ] **M1 — Data:** synthetic generator + public card-fraud dataset loaded into PostgreSQL
- [ ] **M2 — SQL model:** dbt staging → star schema with tests (nulls, uniqueness, accepted values)
- [ ] **M3 — Controls:** reconciliation report (source vs model to the cent), data dictionary, lineage
- [ ] **M4 — BI:** Tableau / Power BI dashboard (spend, active cards, delinquency, fraud rate) + Excel MI pack
- [ ] **M5 — ML:** segmentation (k-means) and fraud model (logistic regression → gradient boosting) with a model card
- [ ] **M6 — NLP:** classify complaint/dispute text with spaCy
- [ ] **M7 — AI assistant:** RAG over policy docs with citations + MCP tools over the platform APIs, with an eval set
- [ ] **M8 — Findings memo:** two pages, three recommendations with dollar impact

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\activate                 # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
$env:PYTHONPATH = "src"                # Windows PowerShell (export PYTHONPATH=src on macOS/Linux)

# 1. Start PostgreSQL (needs Docker Desktop)
docker compose up -d

# 2. Generate and load 50,000 synthetic transactions, then run the data-quality checks
python -m card_analytics.generate --rows 50000 --out data/raw/transactions.csv
python -m card_analytics.load --csv data/raw/transactions.csv

# No Docker? Use SQLite instead:
python -m card_analytics.load --csv data/raw/transactions.csv --db sqlite:///cardrisk.db

pytest
```

### Real bank data: Berka (PKDD'99)

Anonymized data from a real Czech bank, 1993–1998: 4,500 accounts, 5,369 clients, 682 loans, 892 cards and 1,056,320 transactions. Download it (Kaggle: "Berka dataset") into `data/raw/berka/` (git-ignored), then:

```powershell
python -m card_analytics.berka --src data/raw/berka --db sqlite:///cardrisk.db
```

The loader translates Czech codes to English (`PRIJEM` → credit, `VYDAJ` → debit), converts `YYMMDD` dates, derives client gender and birth date from the birth number, flags defaulted loans, and runs 26 integrity checks: row counts, unique keys, foreign keys and date ranges.

## Related

The transaction platform that produces this data: [card-banking-platform](https://github.com/mukesh132001/card-banking-platform).
