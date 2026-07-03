# Local M3U Credential Manager

A local single-user web app for importing M3U/Xtream links, grouping credentials by provider, validating credentials, and archiving invalid records.

## Run Locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000.

## Docker

```powershell
docker compose up --build
```

Open http://127.0.0.1:8000. The SQLite database is stored in the `m3u-data` Docker volume at `/data/app.db`.

## GitHub Actions

The workflow in `.github/workflows/ci.yml` runs tests, validates `docker-compose.yml`, and builds the Docker image.

On pushes to `main`, `master`, or version tags like `v1.0.0`, it also pushes the image to GitHub Container Registry:

```text
ghcr.io/<owner>/<repo>:latest
ghcr.io/<owner>/<repo>:<branch-or-tag>
ghcr.io/<owner>/<repo>:sha-<commit>
```

No extra secrets are required for GHCR publishing; the workflow uses the built-in `GITHUB_TOKEN`.

## Configuration

- `DATABASE_URL`: defaults to `sqlite:///./data/app.db` locally and `sqlite:////data/app.db` in Docker Compose.
- `APP_HOST`: defaults to `0.0.0.0`.
- `APP_PORT`: defaults to `8000`.
- `VALIDATION_TIMEOUT_SECONDS`: defaults to `10`.
- `VALIDATION_CONCURRENCY`: defaults to `3`.
- `VALIDATION_RETRIES`: defaults to `1`.
