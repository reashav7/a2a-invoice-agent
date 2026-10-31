# A2A Invoice Action Agent

FastAPI implementation of the A2A 1.0 invoice proposal/receipt lifecycle.

## Important limitations before grading

This is a deployable baseline, not yet a verified grader-passing implementation. Its built-in decision function is conservative heuristic logic; exact hidden-package classification requires adapting `decide()` to the assignment's actual evidence schema or connecting a hosted model. It does not currently implement semantic decision caching, full cross-process atomic cancellation/result locking, nor durable disk on Render's free web service (the included persistent disk requires a paid Render service in many account/region configurations). Do not submit until these are addressed and exercised against the grader.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export A2A_BEARER_TOKENS='replace-with-long-random-token'
export A2A_BASE_URL='https://YOUR-HOST/a2a/'
uvicorn app.main:app --reload
```

Discovery: `GET /.well-known/agent-card.json`
API base: `/a2a/`

All non-card routes require `Authorization: Bearer ...` and `A2A-Version: 1.0`. POST additionally requires `Content-Type: application/a2a+json`.

## Deployment

Create a Render Web Service from this GitHub repository. Configure `A2A_BEARER_TOKENS` with a long random token and set `A2A_BASE_URL` to the exact final base URL ending in `/a2a/`. Check whether persistent disks are available on your selected Render plan before relying on SQLite persistence.
