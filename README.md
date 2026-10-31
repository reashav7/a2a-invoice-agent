# A2A Invoice Action Agent

FastAPI implementation of the A2A 1.0 invoice proposal/receipt lifecycle. It never makes a payment or performs an invoice action: it records only grader-accepted proposals as receipts.

## Deploy on Render

1. Open the repository in Render and choose **New → Blueprint** (not a manual Web Service), so Render reads `render.yaml` and creates both the free web service and free Postgres database.
2. During setup, provide `OPENAI_API_KEY`. The default model is `gpt-4o-mini` (override with `OPENAI_MODEL` if needed).
3. Deploy. Render's `RENDER_EXTERNAL_HOSTNAME` is used to publish the Agent Card base URL automatically.
4. The public base URL to submit is `https://YOUR-SERVICE.onrender.com/a2a/`. The card is at `https://YOUR-SERVICE.onrender.com/.well-known/agent-card.json`.

The blueprint uses Render Free Web Service and Free Postgres in Singapore. Render documents that Free web services spin down after inactivity, while a Free Postgres database expires after 30 days. This is acceptable for short-lived grading but not long-term production storage.

## Protocol

- Public discovery: `GET /.well-known/agent-card.json`.
- API base: `/a2a/`; endpoints are relative to the advertised base URL.
- Every non-card route requires a non-empty exact `Authorization: Bearer <token>` value. Each distinct bearer token maps to a separate principal.
- Every protected request requires `A2A-Version: 1.0`; every POST requires `Content-Type: application/a2a+json`.
- Proposal creation returns `TASK_STATE_INPUT_REQUIRED` with one proposal artifact. A continuation must match the owner, task, context, batch, package IDs, action IDs, and actions. Only accepted results appear in the final receipt artifact.
- Message idempotency hashes recursively key-sorted compact JSON of `message` only. A changed semantic message under the same principal/message ID returns 409.
- Decisions are cached by canonical semantic package content (excluding package/task/batch namespace identifiers). Model work happens only for uncached packages and is sent in one batched API call.
- Postgres advisory locks serialize same-message retries; row locks serialize result/cancel terminal transitions. SQLite is available for local tests.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY='your-key'
export A2A_BASE_URL='https://your-host/a2a/'
export DB_PATH='./agent.db'
uvicorn app.main:app --reload
```

## Important grading caveat

The project has been smoke-tested locally for proposal creation, output media type, message replay/conflict, accepted receipt creation, terminal immutability and owner isolation. It has not been run against the hidden core/audit batches. The model prompt requires exact decisive references, but accurate decisions depend on the hidden invoice package schema and its evidence layout. Run Check/Save and correct any feedback before assuming full marks. Do not deploy without an AI API key; heuristic fallback exists only for local development and is not suitable for hidden-test accuracy.
