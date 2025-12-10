## Local development

To install from sources and run for development:

```bash
uv sync
uv run pip install uvicorn
uv run uvicorn titiler.application.main:app --reload  --env-file .env
```

## Deployment

Instructions and other required files for deployment are in complimentary private repository.
