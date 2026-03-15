# Airflow Setup Guide for InfraMind

## Prerequisites
- Astro CLI installed
- Docker Desktop running
- AWS credentials configured in `.env`

## Initial Setup

### 1. Start Airflow
```bash
astro dev start
```

This will spin up 5 containers:
- Postgres (metadata DB)
- Scheduler
- Webserver (UI at http://localhost:8080)
- Triggerer
- DAG Processor

Default credentials: `admin` / `admin`

### 2. Create Required Pool (CRITICAL)

ChromaDB uses file locks and cannot handle concurrent writes. You MUST create a pool to prevent corruption.

**Steps:**
1. Open Airflow UI: http://localhost:8080
2. Navigate to **Admin → Pools**
3. Click **+** to add new pool
4. Configure:
   - **Pool Name**: `single_thread_pool`
   - **Slots**: `1`
   - **Description**: `Prevents concurrent ChromaDB writes`
5. Click **Save**

### 3. Configure Airflow Variables (Optional)

Navigate to **Admin → Variables** and set:

| Key | Default | Description |
|-----|---------|-------------|
| `INFRAMIND_S3_BUCKET` | From settings.yaml | S3 bucket for logs |
| `INFRAMIND_S3_PREFIX` | From settings.yaml | S3 prefix path |
| `INFRAMIND_LOOKBACK_HOURS` | `1` | How far back to fetch logs |
| `INFRAMIND_FORCE_REBUILD` | `false` | Force ChromaDB rebuild |
| `INFRAMIND_SLACK_WEBHOOK` | None | Slack webhook for alerts |

### 4. Verify DAG

1. Go to **DAGs** page
2. Find `inframind_rca_pipeline`
3. Toggle it **ON**
4. Click **Trigger DAG** to test

## Persistence

### ChromaDB Persistence
The `docker-compose.override.yml` mounts `./chroma_db` to persist embeddings across container restarts. This prevents re-embedding on every startup.

### Environment Variables
The `.env` file at project root is automatically loaded into all containers by Astro CLI.

## Troubleshooting

### Import Errors
If you see `ModuleNotFoundError` for `core`, `config`, `agents`, or `prompts`:
- Verify `Dockerfile` contains: `ENV PYTHONPATH="${PYTHONPATH}:/usr/local/airflow"`
- Restart containers: `astro dev restart`

### ChromaDB Corruption
If ChromaDB fails with lock errors:
- Verify `single_thread_pool` exists with `slots=1`
- Check that `embed_runbooks` task uses this pool in `dag.py`

### XCom Size Limit Exceeded
If processing >50 logs and seeing XCom errors:
- Implement S3-backed XCom (see commented code in `dag.py`)
- Or switch to dynamic task mapping for parallel processing

### Lost Embeddings After Restart
If embeddings are lost after `astro dev stop`:
- Verify `docker-compose.override.yml` exists at project root
- Check volume mounts: `docker inspect <container_id>`

## Scaling Considerations

### Current Limitations
- **XCom**: 48KB limit per value (affects large log batches)
- **ChromaDB**: Single-threaded writes only
- **Sequential RCA**: Processes logs one-by-one in `run_rca` task

### Future Improvements
1. **S3-backed XCom**: Store large payloads in S3, pass S3 keys via XCom
2. **Dynamic Task Mapping**: Process each log as separate task instance
3. **Managed Vector DB**: Switch to Pinecone/Weaviate for concurrent access
4. **Celery Workers**: Scale horizontally with multiple workers

## Deployment to Astronomer Cloud

```bash
# Login
astro login

# Deploy to dev environment
astro deploy --deployment-name inframind-dev

# Set production variables
astro deployment variable create AWS_ACCESS_KEY_ID --value <key> --deployment-id <id>
astro deployment variable create AWS_SECRET_ACCESS_KEY --value <secret> --deployment-id <id>
```

## Monitoring

- **Airflow UI**: http://localhost:8080
- **MLflow**: Check `MLFLOW_TRACKING_URI` in `.env`
- **Logs**: `astro dev logs` or check Airflow UI → Task Logs
- **ChromaDB**: `./chroma_db/chroma.sqlite3` (SQLite browser)
