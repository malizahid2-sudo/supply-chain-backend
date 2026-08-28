# ML-021 Backend — Deploy Folder

This folder is intentionally flat and minimal — just the two files Render
needs. No spaces in any path, no nesting, so there's nothing for GitHub or
Render to misread.

## Files
- `api_postgres.py` — the FastAPI app
- `requirements.txt` — pinned dependencies

## Render settings (once this is pushed to GitHub)
- **Root Directory:** leave blank (these files sit at the repo root)
- **Language:** Python 3
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `uvicorn api_postgres:app --host 0.0.0.0 --port $PORT`
- **Instance Type:** Free
- **Environment Variables:** `DATABASE_URL` = your Neon connection string
