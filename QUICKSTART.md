# ⚡ Quick Start Checklist

## Before First Run

- [ ] `.env` file exists at project root with AWS credentials
- [ ] Run `astro dev start`
- [ ] Wait for all 5 containers to be healthy
- [ ] Open http://localhost:8080 (admin/admin)

## CRITICAL: Create Pool (Do This Once)

**Without this, ChromaDB will corrupt!**

1. Admin → Pools → **+**
2. Pool Name: `single_thread_pool`
3. Slots: `1`
4. Save

## Test the DAG

1. DAGs page → Find `inframind_rca_pipeline`
2. Toggle **ON**
3. Click **Trigger DAG**
4. Watch task progress in Graph view

## Common Issues

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError: No module named 'core'` | Check Dockerfile has `ENV PYTHONPATH` line, then `astro dev restart` |
| ChromaDB lock errors | Create `single_thread_pool` with slots=1 |
| Embeddings lost after restart | Verify `docker-compose.override.yml` exists |
| XCom size errors | Reduce log batch size or implement S3-backed XCom |

## Files Modified

✅ `Dockerfile` - Added PYTHONPATH
✅ `docker-compose.override.yml` - ChromaDB persistence
✅ `dags/dag.py` - Pool constraint + timeout
✅ `.env` - Already at project root

## Next Steps

- Set Airflow Variables for S3 bucket/prefix (Admin → Variables)
- Configure Slack webhook for alerts (optional)
- Monitor first run in Airflow UI
- Check MLflow for RCA metrics
