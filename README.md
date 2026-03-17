# InfraMind

Autonomous root cause analysis (RCA) pipeline for infrastructure logs. Fetches logs from S3, normalizes them, retrieves relevant runbook context via RAG, runs a multi-agent LLM workflow on AWS Bedrock, and pushes structured RCA results back to S3 — all orchestrated by Apache Airflow on Astro CLI.

---

## Architecture

```
S3 raw/          ChromaDB (RAG)
    │                  │
    ▼                  ▼
fetch_logs ──► normalize_logs ──► run_rca ──► post_results
                                    │
                          ┌─────────┴──────────┐
                          │   Bedrock Agents    │
                          │  1. Investigator    │
                          │  2. Root Cause      │
                          │  3. Fix Generator   │
                          │  4. Formatter       │
                          │  5. Critic (loop)   │
                          └─────────────────────┘
                                    │
                             MLflow (DagsHub)
                             S3 rca-results/
```

**Self-correction loop** — the Critic agent scores each RCA attempt. If the score is below the quality threshold, feedback is injected and the pipeline retries (up to `MAX_RETRIES`).

---

## Stack

| Component | Technology |
|-----------|-----------|
| Orchestration | Apache Airflow 3 via Astro CLI |
| LLM | AWS Bedrock (Claude 3 Haiku / Sonnet) |
| Embeddings | AWS Bedrock Titan Embed |
| Vector DB | ChromaDB (persistent) |
| RAG | LangChain + Markdown runbooks |
| Experiment Tracking | MLflow on DagsHub |
| Log Storage | AWS S3 |
| Evaluation | DeepEval (faithfulness + relevancy) |

---

## Project Structure

```
InfraMind/
├── dags/
│   ├── dag.py          # Airflow DAG — 5 task pipeline
│   ├── workflow.py     # RCA orchestrator + self-correction loop
│   └── ingestion.py    # S3 log fetching
├── agents/
│   ├── investigator.py
│   ├── root_cause.py
│   ├── fix_generator.py
│   ├── formatter.py
│   └── critic.py
├── core/
│   ├── vectordb.py     # ChromaDB + Bedrock embeddings
│   ├── normalizer.py   # Multi-format log parser
│   ├── evaluator.py    # DeepEval integration
│   ├── tracker.py      # MLflow helpers
│   └── bedrock_client.py
├── config/
│   ├── config.py       # Single source of truth for all config
│   ├── settings.yaml
│   └── models.yaml
├── prompts/            # Agent prompt templates
├── runbook/            # Markdown runbooks (RAG knowledge base)
├── Dockerfile          # Astro Runtime + PYTHONPATH
├── docker-compose.override.yml  # ChromaDB volume persistence
├── requirements.txt
└── restart.ps1         # Windows clean restart script
```

---

## Prerequisites

- [Astro CLI](https://www.astronomer.io/docs/astro/cli/install-cli)
- Docker Desktop
- AWS account with Bedrock access (Claude 3 + Titan Embed enabled in `ap-south-1`)
- S3 bucket with logs under `raw/`
- DagsHub account for MLflow tracking

---

## Setup

### 1. Clone and configure

```bash
git clone https://github.com/nasim-raj-laskar/InfraMind.git
cd InfraMind
```

Create a `.env` file at the project root:

```env
AWS_ACCESS_KEY_ID=<your_key>
AWS_SECRET_ACCESS_KEY=<your_secret>
AWS_REGION=ap-south-1
DAGSHUB_USERNAME=<your_username>
DAGSHUB_TOKEN=<your_token>
MLFLOW_TRACKING_URI=https://dagshub.com/<username>/InfraMind.mlflow
```

### 2. Start Airflow

**Windows (PowerShell):**
```powershell
.\restart.ps1
```

**Mac/Linux:**
```bash
astro dev start --no-browser --settings-file /dev/null
```

Then apply pool and variables manually:
```bash
SCHEDULER=$(docker ps --format "{{.Names}}" | grep scheduler)
docker exec $SCHEDULER airflow pools set single_thread_pool 1 "ChromaDB write lock"
docker exec $SCHEDULER airflow variables set INFRAMIND_S3_BUCKET <your-bucket>
docker exec $SCHEDULER airflow variables set INFRAMIND_S3_PREFIX raw/
docker exec $SCHEDULER airflow variables set INFRAMIND_MAX_LOGS 3
docker exec $SCHEDULER airflow variables set INFRAMIND_FORCE_REBUILD false
```

### 3. Trigger the DAG

Open http://localhost:8080 (admin / admin) → DAGs → `inframind_rca_pipeline` → Toggle ON → Trigger.

---

## S3 Bucket Layout

```
s3://your-bucket/
├── raw/                    # Drop log files here
│   └── app_api_20260316.log
├── processed/              # Logs moved here after RCA (auto)
│   └── app_api_20260316.log
└── rca-results/            # RCA output JSON (auto)
    └── results_20260316_175519.json
```

Logs are automatically moved from `raw/` → `processed/` after each run to prevent reprocessing.

---

## Configuration

Key settings in `config/settings.yaml`:

| Setting | Default | Description |
|---------|---------|-------------|
| `pipeline.max_retries` | `2` | Max self-correction attempts per log |
| `pipeline.quality_threshold` | `0.8` | Critic score needed to accept RCA |
| `vectordb.chunk_k` | `6` | Runbook chunks retrieved per query |

Airflow Variables (set via UI or `docker exec`):

| Variable | Default | Description |
|----------|---------|-------------|
| `INFRAMIND_MAX_LOGS` | `3` | Max logs processed per DAG run |
| `INFRAMIND_FORCE_REBUILD` | `false` | Force ChromaDB rebuild |
| `INFRAMIND_SLACK_WEBHOOK` | — | Optional Slack alerts |

---

## RCA Output

Each result in `rca-results/results_*.json`:

```json
{
  "incident_id": "uuid",
  "severity": "High",
  "summary": "...",
  "root_cause": "...",
  "immediate_fix": "...",
  "confidence": 0.91,
  "model_used": "Claude 3 Haiku",
  "mlflow_run_id": "...",
  "attempts": 2,
  "final_score": 0.85,
  "status": "success"
}
```

---

## MLflow Tracking

Every RCA run logs to DagsHub:
- Per-attempt confidence, faithfulness, relevancy scores
- Final critic score
- Token usage and cost
- Full RCA output and critique text

View at: `https://dagshub.com/<username>/InfraMind.mlflow`

---

## Supported Log Formats

- Kubernetes (`kubelet`, `kube-apiserver`)
- CloudWatch JSON exports
- RDS / PostgreSQL error logs
- Application logs (JSON structured)
- Generic syslog / plaintext

---

## License

MIT
