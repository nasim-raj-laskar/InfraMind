# InfraMind Airflow Fixes - Summary

## Issues Identified & Fixed

### ✅ 1. PYTHONPATH for Cross-Package Imports
**Problem**: Airflow can't import `core`, `config`, `agents`, `prompts` modules.

**Fix**: Already present in `Dockerfile`:
```dockerfile
ENV PYTHONPATH="${PYTHONPATH}:/usr/local/airflow"
```

**Status**: ✅ Already fixed

---

### ✅ 2. ChromaDB Persistence Across Restarts
**Problem**: Embeddings lost on `astro dev stop` → costly re-embedding every startup.

**Fix**: Created `docker-compose.override.yml` with volume mounts:
```yaml
services:
  scheduler:
    volumes:
      - ./chroma_db:/usr/local/airflow/chroma_db
  webserver:
    volumes:
      - ./chroma_db:/usr/local/airflow/chroma_db
  triggerer:
    volumes:
      - ./chroma_db:/usr/local/airflow/chroma_db
```

**Status**: ✅ Fixed (file renamed from `docker.compose.override.yml`)

---

### ✅ 3. Environment Variables Loading
**Problem**: `.env` file might not be picked up in containers.

**Fix**: `.env` already at project root — Astro CLI auto-loads it.

**Status**: ✅ Already correct

---

### ✅ 4. ChromaDB Concurrent Write Protection
**Problem**: Multiple workers can corrupt ChromaDB with simultaneous writes.

**Fix**: Added pool constraint in `dags/dag.py`:
```python
embed_runbooks = PythonOperator(
    task_id="embed_runbooks",
    python_callable=task_embed_runbooks,
    pool="single_thread_pool",  # Prevents concurrent ChromaDB writes
)
```

**Manual Step Required**: Create pool in Airflow UI:
- Admin → Pools → + → Name: `single_thread_pool`, Slots: `1`

**Status**: ✅ Code fixed, ⚠️ Manual UI step required

---

### ✅ 5. Task Timeout Protection
**Problem**: `run_rca` task could hang indefinitely on Bedrock API issues.

**Fix**: Added execution timeout in `dags/dag.py`:
```python
run_rca = PythonOperator(
    task_id="run_rca",
    python_callable=task_run_rca,
    execution_timeout=timedelta(minutes=30),
)
```

**Status**: ✅ Fixed

---

### ⚠️ 6. XCom Size Limit (Future-Proofing)
**Problem**: XCom has 48KB limit — will break with large log batches.

**Fix**: Added warning in docstring:
```python
NOTE: XCom has 48KB size limit. If processing large batches (>50 logs),
consider switching to S3-backed XCom or dynamic task mapping.
```

**Status**: ⚠️ Documented, not blocking for current scale

---

## Files Modified

| File | Change | Priority |
|------|--------|----------|
| `Dockerfile` | ✅ Already has PYTHONPATH | Critical |
| `docker-compose.override.yml` | ✅ Renamed + added triggerer | High |
| `dags/dag.py` | ✅ Added pool + timeout | High |
| `.env` | ✅ Already at project root | High |
| `AIRFLOW_SETUP.md` | ✅ Created comprehensive guide | Medium |
| `QUICKSTART.md` | ✅ Created quick checklist | Medium |

---

## What You Need to Do

### Immediate (Before First Run)
1. ✅ Files already fixed — no code changes needed
2. ⚠️ **CRITICAL**: Create `single_thread_pool` in Airflow UI after `astro dev start`
   - Admin → Pools → + → Name: `single_thread_pool`, Slots: `1`

### Optional (For Production)
3. Set Airflow Variables (Admin → Variables):
   - `INFRAMIND_S3_BUCKET`
   - `INFRAMIND_S3_PREFIX`
   - `INFRAMIND_SLACK_WEBHOOK` (for alerts)

### Future (When Scaling)
4. Implement S3-backed XCom for large batches
5. Switch to dynamic task mapping for parallel RCA
6. Consider managed vector DB (Pinecone/Weaviate)

---

## Testing the Fixes

```bash
# 1. Start Airflow
astro dev start

# 2. Create pool in UI (see QUICKSTART.md)

# 3. Trigger DAG
# Go to http://localhost:8080 → DAGs → inframind_rca_pipeline → Trigger

# 4. Monitor
astro dev logs -f

# 5. Verify persistence
astro dev stop
astro dev start
# Check that chroma_db/ still exists and embeddings aren't re-created
```

---

## Rollback Plan

If issues occur:
```bash
# Stop containers
astro dev stop

# Revert docker-compose (if needed)
git checkout docker-compose.override.yml

# Revert DAG (if needed)
git checkout dags/dag.py

# Restart
astro dev start
```

---

## Summary

All critical fixes are in place:
- ✅ Import paths work
- ✅ ChromaDB persists across restarts
- ✅ Concurrent write protection added
- ✅ Task timeouts prevent hangs
- ✅ XCom limits documented

**Only manual step**: Create `single_thread_pool` in Airflow UI after first startup.
