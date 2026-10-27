# Document Intake Automation

Turns invoices and purchase orders that arrive as PDFs or emails into validated, structured data
with Claude, sends low-confidence documents to a human review queue, and keeps a full audit trail
of what came in, what was extracted and what a person changed.

> Work in progress. See the phases below.

## Run locally

```bash
cp .env.example .env
docker compose up -d db
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # Windows
uvicorn app.main:app --reload --port 8010
```
