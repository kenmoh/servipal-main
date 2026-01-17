# servipal-backend

Backend service for Servipal.

## Development

The project uses `uv` for dependency management.

```bash
uv sync
uv run fastapi dev
```

## Deployment

The application is configured for deployment on Google Cloud Run.

### Prerequisites

- Google Cloud SDK (`gcloud`) installed and authenticated.
- A Google Cloud Project.

### Deploy with Cloud Build

You can deploy using Google Cloud Build which builds the container and deploys it to Cloud Run.

```bash
gcloud builds submit --config cloudbuild.yaml .
```

Alternatively, you can build and deploy manually:

```bash
# Build
docker build -t gcr.io/[PROJECT_ID]/servipal-backend .

# Push
docker push gcr.io/[PROJECT_ID]/servipal-backend

# Deploy
gcloud run deploy servipal-backend --image gcr.io/[PROJECT_ID]/servipal-backend --platform managed
```
