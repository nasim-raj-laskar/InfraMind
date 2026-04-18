# InfraMind

**A production-grade LLMOps platform for autonomous infrastructure root cause analysis (RCA)** — leveraging multi-agent orchestration, retrieval-augmented generation (RAG), self-correcting LLM workflows on AWS Bedrock, and a fully serverless human-in-the-loop (HITL) review layer. Built for SRE/DevOps teams requiring zero-touch incident triage with full observability, experiment tracking, and quality gates.

---

## System Architecture

### High-Level Pipeline Flow

```mermaid
flowchart TB
    subgraph INGEST["Data Ingestion · Airflow DAG"]
        LOGS["S3 raw/"] --> FETCH["fetch_logs"] --> NORM["normalize_logs"] --> NORM_OUT["Normalized JSON"]
    end

    subgraph RAG["RAG Knowledge Base"]
        RUNBOOKS["Markdown Runbooks"] --> EMBED["Titan Embed v2"] --> CHROMA[("ChromaDB")]
        CHROMA -->|"cosine search · top-6 · MMR rerank"| CONTEXT["Augmented Context"]
    end

    subgraph MODEL["Dynamic Model Selection"]
        CHECK{"Log > 2000 chars?"}
        CHECK -->|No| L8["Llama 3 8B"]
        CHECK -->|Yes| L70["Llama 3 70B"]
    end

    subgraph AGENTS["Multi-Agent Pipeline · AWS Bedrock"]
        A1["1 Investigator"] --> A2["2 Root Cause"] --> A3["3 Fix Generator"] --> A4["4 Formatter"]
        A4 --> CRITIC{"5 Critic · Mistral 7B\nScore ≥ 0.8?"}
        CRITIC -->|"No · inject feedback"| A1
        CRITIC -->|Max retries| FAIL["FAILED"]
    end

    subgraph HITL["Human-in-the-Loop · AWS Step Functions"]
        SF["trigger_sf_review\nfire and forget"]
        STORE["StoreForReview Lambda\nDynamoDB + task token"]
        WAIT["WaitForTaskToken\npaused · zero cost"]
        UI["ReviewUI Lambda\nAPI Gateway · SRE queue"]
        APPROVE["OnApprove Lambda\nS3 move · MLflow tag"]
        REJECT["OnReject Lambda\nfeedback-in-log · raw/"]
        SF --> STORE --> WAIT
        WAIT -->|"SRE approves"| APPROVE
        WAIT -->|"SRE rejects + feedback"| REJECT
        REJECT -->|"next DAG run picks up"| LOGS
    end

    subgraph OBS["Observability"]
        MLFLOW["MLflow · DagsHub"]
        DEEPEVAL["DeepEval"]
        GRAFANA["Grafana"]
    end

    NORM_OUT --> CHECK
    NORM_OUT -->|query embedding| CHROMA
    L8 & L70 -->|LLM inference| A1
    CONTEXT -->|injected into prompts| A1
    CRITIC -->|pass| SF
    AGENTS -->|telemetry| MLFLOW
    AGENTS -->|eval| DEEPEVAL
    DEEPEVAL --> MLFLOW
    MLFLOW --> GRAFANA

    classDef teal fill:#0F6E56,color:#E1F5EE,stroke:#085041
    classDef blue fill:#185FA5,color:#E6F1FB,stroke:#0C447C
    classDef purple fill:#534AB7,color:#EEEDFE,stroke:#3C3489
    classDef coral fill:#993C1D,color:#FAECE7,stroke:#712B13
    classDef amber fill:#854F0B,color:#FAEEDA,stroke:#633806
    classDef green fill:#3B6D11,color:#EAF3DE,stroke:#27500A
    classDef red fill:#A32D2D,color:#FCEBEB,stroke:#791F1F
    classDef hitl fill:#185FA5,color:#E6F1FB,stroke:#0C447C

    class LOGS,FETCH,NORM,NORM_OUT teal
    class RUNBOOKS,EMBED,CHROMA,CONTEXT blue
    class CHECK,L8,L70 amber
    class A1,A2,A3,A4 purple
    class CRITIC coral
    class FAIL red
    class SF,STORE,WAIT,UI,APPROVE,REJECT hitl
    class MLFLOW,DEEPEVAL,GRAFANA green
```

### Multi-Agent Workflow with Self-Correction

```mermaid
sequenceDiagram
    participant Airflow
    participant Orchestrator
    participant RAG as ChromaDB RAG
    participant Bedrock as AWS Bedrock<br/>(Llama 3 / Mistral 7B)
    participant Critic
    participant MLflow
    participant SF as Step Functions

    Airflow->>Orchestrator: Trigger RCA (normalized_log)

    loop Self-Correction (max 2 retries)
        Orchestrator->>RAG: Query runbook context (semantic search)
        RAG-->>Orchestrator: Top-6 relevant chunks

        Orchestrator->>Bedrock: Agent 1: Investigator (log + context)
        Bedrock-->>Orchestrator: Incident summary

        Orchestrator->>Bedrock: Agent 2: Root Cause (summary + context)
        Bedrock-->>Orchestrator: Hypothesis + evidence

        Orchestrator->>Bedrock: Agent 3: Fix Generator (root cause + context)
        Bedrock-->>Orchestrator: Remediation steps

        Orchestrator->>Bedrock: Agent 4: Formatter (all outputs)
        Bedrock-->>Orchestrator: Structured RCA JSON

        Orchestrator->>Critic: Evaluate RCA quality
        Critic->>Bedrock: Score faithfulness + relevancy (Mistral-7B)
        Bedrock-->>Critic: Quality metrics
        Critic-->>Orchestrator: Score + feedback

        Orchestrator->>MLflow: Log attempt metrics (score, tokens, latency)

        alt Score ≥ 0.8
            Orchestrator->>SF: trigger_sf_review (fire and forget)
            Orchestrator->>MLflow: Mark run SUCCESS
            Orchestrator-->>Airflow: Complete
        else Score < 0.8 && retries left
            Note over Orchestrator: Inject critic feedback into next iteration
        else Max retries exceeded
            Orchestrator->>MLflow: Mark run FAILED
            Orchestrator-->>Airflow: Fail with diagnostics
        end
    end
```

### RAG Knowledge Retrieval Pipeline

```mermaid
graph LR
    subgraph "Indexing Phase (One-time)"
        MD["Markdown Runbooks<br/>SOP Documents"]
        SPLIT["RecursiveCharacterTextSplitter<br/>chunk_size=1000 · overlap=200"]
        EMB_IDX["Bedrock Titan Embed<br/>1536-dim vectors"]
        STORE[("ChromaDB<br/>Persistent Storage")]
        MD --> SPLIT --> EMB_IDX --> STORE
    end

    subgraph "Query Phase (Per RCA)"
        QUERY["Log Error Context<br/>+ Agent Question"]
        EMB_Q["Bedrock Titan Embed<br/>Query Vector"]
        SEARCH["Cosine Similarity<br/>Top-K=6"]
        RERANK["MMR Reranking<br/>Diversity Filter"]
        CONTEXT["Augmented Context<br/>to LLM Prompt"]
        QUERY --> EMB_Q --> SEARCH --> RERANK --> CONTEXT
        STORE -."Vector Search".-> SEARCH
    end

    style STORE fill:#4a3a1a,color:#fff
    style CONTEXT fill:#1a4a2e,color:#fff
```

---

## Human-in-the-Loop (HITL) Architecture

Rather than writing RCA results directly to storage, the pipeline hands off to a **fully serverless HITL review layer** built on AWS Step Functions, Lambda, API Gateway, and DynamoDB. Airflow fires and forgets — the DAG completes as `SUCCESS` without waiting for a human decision, keeping pipeline slots free regardless of how long review takes.

### HITL Flow

```mermaid
flowchart TB
    subgraph AIRFLOW["Airflow — last task"]
        TRG["trigger_sf_review\nsfn.start_execution()\nreturns immediately"]
    end

    subgraph SF["AWS Step Functions state machine"]
        STORE["StoreForReview Lambda\nwrites RCA + task token\nto DynamoDB"]
        WAIT["WaitForTaskToken\npaused indefinitely\n72h heartbeat timeout"]
        ROUTE{"RouteDecision"}
        APPROVE["OnApprove Lambda"]
        REJECT["OnReject Lambda"]
        TIMEOUT["EscalateTimeout\nOnReject with\ntimeout feedback"]

        STORE --> WAIT
        WAIT -->|"send_task_success"| ROUTE
        WAIT -->|"HumanRejection"| REJECT
        WAIT -->|"HeartbeatTimeout"| TIMEOUT
        ROUTE --> APPROVE
    end

    subgraph UI["Review UI — API Gateway + Lambda"]
        Q["GET /queue\nlist pending RCAs\nfrom DynamoDB"]
        D["GET /rca/{id}\nfull RCA + AI critic\n+ raw log content"]
        A["POST /approve\nsend_task_success(token)"]
        R["POST /reject\nsend_task_failure(token)\n+ feedback form"]
    end

    subgraph OUTPUTS["On Approve"]
        S3M["S3 raw/ → processed/\nlog archived"]
        S3R["S3 rca-results/\nRCA JSON written"]
        ML["MLflow tag\nhuman_verdict=approved\nrater_id"]
    end

    subgraph RETRY["On Reject — feedback-in-log"]
        FBL["New log written to S3 raw/\noriginal log + AI critic output\n+ human feedback embedded\nas # === comment lines"]
        NEXT["Next scheduled DAG run\npicks up naturally\nagents read prior context"]
    end

    TRG -->|"starts execution"| STORE
    Q & D --> UI
    A -->|"resumes SF"| ROUTE
    R -->|"resumes SF"| REJECT
    APPROVE --> S3M & S3R & ML
    REJECT --> FBL --> NEXT
```

### Dual-Critic Quality Gate

Every RCA passes through two critics before reaching an SRE:

- **AI critic (Mistral 7B via DeepEval)** — scores faithfulness, answer relevancy, and contextual recall. Triggers the self-correction loop if score < 0.8.
- **Human critic (SRE via Review UI)** — assesses semantic and domain correctness the AI cannot evaluate.

On rejection, the SRE provides a structured `feedback_type` (`wrong_rc` / `wrong_fix` / `hallucination` / `incomplete`) plus free-text reason and an optional corrected root cause. Both the AI critic output and the human feedback are embedded into the rejected log file:

```
<original log content>

# === RCA OUTPUT ===
# summary: PostgreSQL connection pool exhausted
# root_cause: ORM session leak in user_service.py
# fix: Restart pods + add session.close()

# === HUMAN FEEDBACK ===
# feedback_type: wrong_rc
# reason: Actual cause was OOM killer terminating the pod
# corrected_root_cause: Memory limit breached — container hit 512Mi ceiling
# timestamp: 2026-03-29T10:22:00Z
```

The new timestamped file (`raw/kubelet_20260315_rejected_20260329_102200.log`) is picked up on the next scheduled DAG run. The investigator prompt instructs agents to treat `# ===` sections as prior analysis context and human corrections — avoiding the same mistake while re-examining the raw evidence independently.

> **Why no auto-retrigger?** Deliberately omitted to prevent race conditions between a rejection-triggered run and an in-progress pipeline processing a different log batch.

### HITL AWS Components

| Component | Service | Purpose |
|-----------|---------|---------|
| State machine | AWS Step Functions | `waitForTaskToken` pause — zero cost while waiting |
| Pending queue | DynamoDB `rca_reviews` | Stores RCA + task token per incident |
| Review interface | Lambda + API Gateway | Serverless HTML/JS SRE queue — no container to maintain |
| Approve action | Lambda `InfraMind-OnApprove` | S3 move, rca-results write, MLflow tag |
| Reject action | Lambda `InfraMind-OnReject` | Feedback-in-log write to `raw/`, MLflow tag |
| Handoff | Lambda `InfraMind-StoreForReview` | Receives RCA from Airflow, writes to DynamoDB |

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Orchestration** | Apache Airflow 3 (Astro CLI) | DAG-based pipeline scheduling, task dependency management |
| **LLM Runtime** | AWS Bedrock — Llama 3 8B / 70B + Mistral 7B | Serverless LLM inference — model auto-selected by log size |
| **Embeddings** | AWS Bedrock Titan Embed v2 | 1536-dim semantic vectors for RAG retrieval |
| **Vector DB** | ChromaDB 0.4.x | Persistent HNSW index with cosine similarity search |
| **RAG Framework** | LangChain 0.1.x | Prompt engineering, retrieval chains, agent orchestration |
| **HITL Orchestration** | AWS Step Functions | `waitForTaskToken` stateful human review pause |
| **HITL Compute** | AWS Lambda (Python 3.11) | StoreForReview · OnApprove · OnReject · ReviewUI |
| **HITL API** | AWS API Gateway (REST, Regional) | `/queue` · `/rca/{id}` · `/approve` · `/reject` · `/log` |
| **HITL Queue** | AWS DynamoDB (on-demand) | Pending RCA store with task token per incident |
| **Experiment Tracking** | MLflow 2.x (DagsHub) | Hyperparameter logging, metric tracking, human verdict tags |
| **LLM Evaluation** | DeepEval 0.21.x | Faithfulness, answer relevancy, contextual recall metrics |
| **Object Storage** | AWS S3 | Raw logs, processed logs, RCA results, model artifacts |
| **Monitoring** | Prometheus + Grafana | Pipeline metrics, token usage, latency tracking |

---

## Project Structure

```
InfraMind/
├── dags/
│   ├── dag.py              # Airflow DAG — fetch → normalize → run_rca → trigger_sf_review
│   ├── workflow.py         # RCA orchestrator + self-correction loop
│   └── ingestion.py        # S3 log fetching
├── agents/
│   ├── investigator.py
│   ├── root_cause.py
│   ├── fix_generator.py
│   ├── formatter.py
│   └── critic.py
├── core/
│   ├── vectordb.py         # ChromaDB + Bedrock embeddings
│   ├── normalizer.py       # Multi-format log parser
│   ├── evaluator.py        # DeepEval integration
│   ├── tracker.py          # MLflow helpers
│   ├── sfn_client.py       # Step Functions start_execution wrapper
│   └── bedrock_client.py
├── hitl/
│   ├── lambdas/
│   │   ├── store_for_review.py   # StoreForReview Lambda
│   │   ├── on_approve.py         # OnApprove Lambda
│   │   ├── on_reject.py          # OnReject Lambda
│   │   └── review_ui.py          # ReviewUI Lambda — serves HTML + API routes
│   └── state_machine.json        # Step Functions ASL definition
├── config/
│   ├── config.py           # Single source of truth for all config
│   ├── settings.yaml
│   └── models.yaml
├── prompts/                # Agent prompt templates
├── runbook/                # Markdown runbooks (RAG knowledge base)
├── Dockerfile              # Astro Runtime + PYTHONPATH
├── docker-compose.override.yml   # ChromaDB volume persistence
├── requirements.txt
├── restart.ps1             # Windows clean restart script
└── restart.sh              # macOS/Linux clean restart script
```

---

## Deployment Architecture

### Containerized Airflow on Astro Runtime

```mermaid
graph TB
    subgraph "Docker Compose Stack"
        subgraph "Airflow Components"
            WEBSERVER["Webserver :8080\nUI + REST API"]
            SCHEDULER["Scheduler\nDAG parsing · Task scheduling"]
            TRIGGERER["Triggerer\nAsync task support"]
            POSTGRES[("PostgreSQL\nMetadata DB")]
        end

        subgraph "Persistent Volumes"
            DAGS_VOL["./dags/\nDAG definitions"]
            CHROMA_VOL["./chroma_data/\nVector DB persist"]
            LOGS_VOL["./logs/\nTask logs"]
        end
    end

    subgraph "AWS Services"
        BEDROCK["AWS Bedrock\nap-south-1 · LLM inference"]
        S3["S3 Bucket\nLog storage + RCA results"]
        MLFLOW["DagsHub MLflow\nExperiment tracking"]
        SF_AWS["Step Functions\nHITL state machine"]
        LAMBDA["Lambda\nHITL compute"]
        APIGW["API Gateway\nReview UI"]
        DYNAMO["DynamoDB\nPending queue"]
    end

    WEBSERVER --> POSTGRES
    SCHEDULER --> POSTGRES
    SCHEDULER --> DAGS_VOL & CHROMA_VOL
    SCHEDULER --> BEDROCK & S3 & MLFLOW
    SCHEDULER -->|"trigger_sf_review"| SF_AWS
    SF_AWS --> LAMBDA --> DYNAMO & S3
    APIGW --> LAMBDA

    style SCHEDULER fill:#1a3a5c,color:#fff
    style SF_AWS fill:#185FA5,color:#fff
    style LAMBDA fill:#534AB7,color:#fff
```

### Airflow DAG Task Dependencies

![](assets/dag.png)

**Task Pool Configuration**
- `single_thread_pool` (slots=1): Serializes ChromaDB writes to prevent race conditions
- `default_pool` (slots=128): Parallel execution for fetch/normalize tasks

---

## Prerequisites

### Infrastructure Requirements

| Component | Requirement | Notes |
|-----------|-------------|-------|
| **Astro CLI** | v1.20+ | [Install guide](https://www.astronomer.io/docs/astro/cli/install-cli) |
| **Docker Desktop** | 4.25+ | 8GB RAM, 4 CPU cores recommended |
| **AWS Account** | Bedrock + Lambda + Step Functions enabled | All services in `ap-south-1` region |
| **S3 Bucket** | Standard tier | Versioning + lifecycle policies recommended |
| **DynamoDB Table** | `rca_reviews` on-demand | Created automatically or via console |
| **DagsHub Account** | Free tier | MLflow tracking backend |

### AWS Bedrock Model Access

Enable the following models in AWS Console → Bedrock → Model access:

- `meta.llama3-8b-instruct-v1:0` — fast inference for short logs
- `meta.llama3-70b-instruct-v1:0` — deep reasoning for large logs
- `mistral.mistral-7b-instruct-v0:2` — critic / quality scoring agent
- `amazon.titan-embed-text-v2:0` — RAG embeddings

**Region**: `ap-south-1` (Mumbai) — lowest latency for Asia-Pacific

### IAM Permissions Required

Your Airflow IAM user needs the following in addition to existing Bedrock + S3 permissions:

```json
{
  "Effect": "Allow",
  "Action": "states:StartExecution",
  "Resource": "arn:aws:states:ap-south-1:YOUR_ACCOUNT:stateMachine:InfraMind-HITL"
}
```

---

## Setup & Deployment

### 1. Clone Repository

```bash
git clone https://github.com/nasim-raj-laskar/InfraMind.git
cd InfraMind
```

### 2. Environment Configuration

Create `.env` at project root:

```bash
# AWS Credentials (IAM user with Bedrock + S3 + Step Functions access)
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_REGION=ap-south-1

# DagsHub MLflow Tracking
DAGSHUB_USERNAME=your_username
DAGSHUB_TOKEN=your_dagshub_token
MLFLOW_TRACKING_URI=https://dagshub.com/your_username/InfraMind.mlflow

# HITL — Step Functions
SF_STATE_MACHINE_ARN=arn:aws:states:ap-south-1:YOUR_ACCOUNT:stateMachine:InfraMind-HITL
INFRAMIND_S3_BUCKET=your-bucket-name
```

### 3. Deploy HITL Lambda Functions

Deploy the four Lambda functions in `hitl/lambdas/` to AWS (`ap-south-1`):

| Function | Trigger | Timeout | Layer needed |
|----------|---------|---------|-------------|
| `InfraMind-StoreForReview` | Step Functions | 10s | None |
| `InfraMind-OnApprove` | Step Functions | 30s | `mlflow` |
| `InfraMind-OnReject` | Step Functions | 30s | `mlflow` |
| `InfraMind-ReviewUI` | API Gateway | 10s | None |

Build the mlflow Lambda Layer:

```bash
mkdir -p python
pip install mlflow requests urllib3 packaging \
  --target python/ \
  --platform manylinux2014_x86_64 \
  --python-version 3.11 \
  --only-binary=:all:
zip -r mlflow-layer.zip python/
# Upload to Lambda → Layers → Create layer → attach to OnApprove + OnReject
```

### 4. Deploy Step Functions State Machine

Go to AWS Console → Step Functions → Create state machine → paste `hitl/state_machine.json`. Copy the ARN into your `.env` as `SF_STATE_MACHINE_ARN`.

### 5. Deploy API Gateway

Create a REST API (`InfraMind-HITL-API`, Regional, `ap-south-1`) with the following routes, all pointing to `InfraMind-ReviewUI` with **Lambda Proxy integration enabled**:

```
GET  /          → serves the HTML review UI
GET  /queue     → list pending RCAs from DynamoDB
GET  /rca/{id}  → full RCA detail + AI critic + raw log
GET  /log       → raw log content from S3 (?key=raw/...)
POST /approve   → send_task_success(token)
POST /reject    → send_task_failure(token) + human feedback
```

Enable CORS on all resources → Deploy to stage `prod` → copy the invoke URL into `InfraMind-ReviewUI` Lambda as the `API` constant.

### 6. Start Airflow Stack

**Windows (PowerShell)**:

```powershell
.\restart.ps1
```

**macOS/Linux**:

```bash
chmod +x restart.sh
./restart.sh
```

Both scripts stop existing containers, start Astro, wait for the scheduler, then apply pools and Airflow variables automatically.

### 7. Set Airflow Variables

```bash
SCHEDULER=$(docker ps --format "{{.Names}}" | grep scheduler)

docker exec $SCHEDULER airflow variables set INFRAMIND_S3_BUCKET your-bucket-name
docker exec $SCHEDULER airflow variables set INFRAMIND_S3_PREFIX raw/
docker exec $SCHEDULER airflow variables set INFRAMIND_MAX_LOGS 3
docker exec $SCHEDULER airflow variables set INFRAMIND_FORCE_REBUILD false
docker exec $SCHEDULER airflow variables set SF_STATE_MACHINE_ARN arn:aws:states:ap-south-1:YOUR_ACCOUNT:stateMachine:InfraMind-HITL
```

### 8. Verify Deployment

```bash
# Check container health
docker ps --filter "name=inframind"

# View scheduler logs
docker logs -f $(docker ps --format "{{.Names}}" | grep scheduler)

# Test ChromaDB persistence
docker exec $(docker ps --format "{{.Names}}" | grep scheduler) \
  python -c "from core.vectordb import VectorDB; db = VectorDB(); print(db.collection.count())"

# Test Step Functions connection
docker exec $(docker ps --format "{{.Names}}" | grep scheduler) \
  python -c "
import boto3, json, os
sfn = boto3.client('stepfunctions', region_name='ap-south-1')
r = sfn.start_execution(
  stateMachineArn=os.environ['SF_STATE_MACHINE_ARN'],
  name='connection-test-001',
  input=json.dumps({'rca_output': {'incident_id': 'test-001', 'severity': 'Low',
    'log_source': 's3://bucket/raw/test.log', 'mlflow_run_id': 'test',
    'summary': 'test', 'root_cause': 'test', 'immediate_fix': 'test'},
    'ai_critic': {'score': 0.9, 'reasoning': 'test'}})
)
print('SF connection OK:', r['executionArn'])
"
```

### 9. Trigger DAG

**Via Airflow UI**: Navigate to `http://localhost:8080` (admin / admin) → enable `inframind_rca_pipeline` → click Trigger DAG.

**Via CLI**:

```bash
docker exec $(docker ps --format "{{.Names}}" | grep scheduler) \
  airflow dags trigger inframind_rca_pipeline
```

**Via REST API**:

```bash
curl -X POST "http://localhost:8080/api/v1/dags/inframind_rca_pipeline/dagRuns" \
  -H "Content-Type: application/json" \
  -u "admin:admin" \
  -d '{"conf": {}}'
```

---

## S3 Data Layout

```
s3://your-bucket/
├── raw/                          ← Airflow picks up from here
│   ├── app_api_20260316.log      ← fresh log
│   └── kubelet_rejected_*.log    ← rejected logs with embedded feedback
├── processed/                    ← moved here on SRE approval
│   └── app_api_20260316.log
└── rca-results/                  ← written on SRE approval
    └── results_<incident_id>.json
```

**Lifecycle Policies**

```json
{
  "Rules": [
    {
      "Id": "ArchiveProcessedLogs",
      "Filter": {"Prefix": "processed/"},
      "Transitions": [
        {"Days": 30, "StorageClass": "STANDARD_IA"},
        {"Days": 90, "StorageClass": "GLACIER"}
      ]
    },
    {
      "Id": "RetainRCAResults",
      "Filter": {"Prefix": "rca-results/"},
      "Transitions": [{"Days": 365, "StorageClass": "GLACIER_DEEP_ARCHIVE"}]
    }
  ]
}
```

---

## Configuration Management

```mermaid
graph TB
    subgraph "Configuration Sources"
        YAML["config/settings.yaml · Pipeline defaults"]
        MODELS["config/models.yaml · LLM model specs"]
        ENV[".env · Secrets & credentials"]
        AIRFLOW_VARS["Airflow Variables · Runtime overrides"]
    end

    subgraph "Config Loader (config/config.py)"
        LOADER["Singleton ConfigManager · Merge + validate"]
    end

    subgraph "Runtime Components"
        DAG["Airflow DAG"]
        AGENTS["LLM Agents"]
        RAG["RAG Pipeline"]
        HITL_CFG["HITL Lambda"]
    end

    YAML & MODELS & ENV & AIRFLOW_VARS --> LOADER
    LOADER --> DAG & AGENTS & RAG & HITL_CFG

    style LOADER fill:#4a3a1a,color:#fff
```

**Pipeline Settings** (`config/settings.yaml`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `pipeline.max_retries` | `2` | Self-correction loop iterations |
| `pipeline.quality_threshold` | `0.8` | Minimum AI critic score to pass to HITL |
| `pipeline.timeout_seconds` | `300` | Max execution time per log |
| `vectordb.chunk_k` | `6` | RAG retrieval count |
| `vectordb.chunk_size` | `1000` | Text splitter chunk size |
| `vectordb.chunk_overlap` | `200` | Overlap between chunks |

**Airflow Variables**

| Variable | Default | Description |
|----------|---------|-------------|
| `INFRAMIND_S3_BUCKET` | — | S3 bucket name (required) |
| `INFRAMIND_MAX_LOGS` | `3` | Batch size per DAG run |
| `INFRAMIND_FORCE_REBUILD` | `false` | Rebuild ChromaDB index |
| `INFRAMIND_SLACK_WEBHOOK` | — | Incident notification endpoint |
| `INFRAMIND_ENABLE_CACHE` | `true` | Cache LLM responses (dev mode) |
| `INFRAMIND_LOG_LEVEL` | `INFO` | Logging verbosity |
| `SF_STATE_MACHINE_ARN` | — | Step Functions state machine ARN (required) |

---

## RCA Output Schema

Each approved RCA written to `s3://bucket/rca-results/results_<incident_id>.json`:

```json
{
  "incident_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-03-16T17:55:19Z",
  "log_source": "s3://bucket/raw/app_api_20260316.log",
  "severity": "High",
  "summary": "PostgreSQL connection pool exhaustion causing API 503 errors",
  "root_cause": "Max connections (100) exceeded due to connection leak in ORM session management.",
  "immediate_fix": "1. Restart API pods\n2. Apply connection timeout (30s)\n3. Deploy hotfix with session.close()",
  "preventive_measures": [
    "Implement connection pool monitoring alerts",
    "Add circuit breaker pattern for database calls",
    "Enable pgBouncer connection pooling layer"
  ],
  "confidence": 0.91,
  "model_used": "meta.llama3-8b-instruct-v1:0",
  "mlflow_run_id": "a3f2c1b0d9e8f7a6b5c4d3e2f1a0b9c8",
  "attempts": 2,
  "final_score": 0.85,
  "status": "approved",
  "approved_by": "sre_ananya",
  "metrics": {
    "faithfulness": 0.89,
    "answer_relevancy": 0.87,
    "contextual_recall": 0.82,
    "total_tokens": 4521,
    "inference_latency_ms": 3847,
    "cost_usd": 0.0113
  },
  "rag_context": [
    "runbook/database/connection_pool_tuning.md",
    "runbook/kubernetes/pod_restart_procedures.md"
  ]
}
```

---

## LLMOps: Experiment Tracking & Observability

Every RCA execution is tracked in **MLflow (DagsHub backend)**. Human verdicts are written back to the original MLflow run as tags post-review, enabling correlation between AI critic scores and SRE-assessed quality over time.

**Tracked per run:**

- Parameters: `model_name`, `temperature`, `max_tokens`, `chunk_k`, `quality_threshold`
- Metrics: `attempt_N_score`, `final_critic_score`, `faithfulness`, `answer_relevancy`, `total_tokens`, `inference_latency_ms`, `cost_usd`
- Tags (post-HITL): `human_verdict`, `rater_id`, `feedback_type`
- Artifacts: `rca_output.json`, `critic_feedback.txt`, `retrieved_context.md`

**Access MLflow UI**: `https://dagshub.com/<username>/InfraMind.mlflow`

### Key Performance Indicators

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| RCA Success Rate | ≥ 95% | < 90% |
| Avg AI Critic Score | ≥ 0.85 | < 0.75 |
| SRE Approval Rate | ≥ 80% | < 60% |
| P95 Latency | ≤ 30s | > 60s |
| Token Cost/RCA | ≤ $0.02 | > $0.05 |
| Faithfulness Score | ≥ 0.80 | < 0.70 |

---

## Cost Estimate

**Monthly (1000 logs/month, 2 retries avg, 50/50 8B/70B split):**

| Component | Cost |
|-----------|------|
| Llama 3 8B — 500 logs × 2 × 6000 tokens | $1.80 |
| Llama 3 70B — 500 logs × 2 × 6000 tokens | $15.90 |
| Mistral 7B critic — 1000 × 2 × 2000 tokens | $0.80 |
| Titan Embed v2 RAG queries | $0.10 |
| S3 storage | $1.27 |
| Step Functions — 5 transitions × 1000 executions | $0.13 |
| Lambda invocations | ~$0.50 |
| DynamoDB on-demand | ~$0.10 |
| **Total (self-hosted Airflow)** | **~$21/month** |
| Total (Astro Cloud) | ~$196/month |

---

## Troubleshooting

### ChromaDB Collection Not Found

```bash
docker exec $(docker ps --format "{{.Names}}" | grep scheduler) \
  airflow variables set INFRAMIND_FORCE_REBUILD true
docker exec $(docker ps --format "{{.Names}}" | grep scheduler) \
  airflow dags trigger inframind_rca_pipeline
```

### Bedrock Throttling (429 Errors)

Add exponential backoff in `core/bedrock_client.py`:

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def invoke_model(self, prompt):
    ...
```

### Step Functions AccessDenied

Confirm your Airflow IAM user has `states:StartExecution` on the state machine ARN. Then verify `SF_STATE_MACHINE_ARN` is set in both `.env` and Airflow variables.

### HITL Task Token Expired

Step Functions task tokens expire after 1 year but the heartbeat timeout is 72 hours. If no SRE action is taken within 72 hours, the `EscalateTimeout` state runs `OnReject` automatically with `feedback_type: timeout`.

### Low AI Critic Scores (< 0.8)

```yaml
# config/settings.yaml — temporary mitigation
vectordb:
  chunk_k: 10        # increase from 6
pipeline:
  quality_threshold: 0.75   # lower acceptance bar while adding runbooks
```

### Debug Mode

```bash
docker exec $(docker ps --format "{{.Names}}" | grep scheduler) \
  airflow variables set INFRAMIND_LOG_LEVEL DEBUG
docker logs -f $(docker ps --format "{{.Names}}" | grep scheduler) | grep InfraMind
```

---

## Prompt Engineering

Each agent uses a Jinja2 template in `prompts/`. Key variables injected per call:

| Variable | Source | Used by |
|----------|--------|---------|
| `{{log_content}}` | Normalized log | Investigator |
| `{{rag_context}}` | ChromaDB top-6 chunks | All agents |
| `{{previous_output}}` | Prior agent output | Root Cause, Fix Generator |
| `{{critic_feedback}}` | AI critic text | Investigator (on retry) |
| `{{feedback_history}}` | Embedded `# ===` sections in log | Investigator (on HITL rejection retry) |

The investigator prompt explicitly instructs: *"If the log contains lines starting with `# ===`, treat them as previous analysis context and human corrections. Do NOT repeat a rejected root cause. Re-examine raw log evidence independently to verify any provided correction."*

---

## Appendix

### A. MLflow Experiment Schema

```python
mlflow.log_params({
    "model_name": "meta.llama3-8b-instruct-v1:0",
    "temperature": 0.1, "max_tokens": 2048,
    "chunk_k": 6, "quality_threshold": 0.8, "max_retries": 2
})
mlflow.log_metrics({
    "attempt_1_score": 0.72, "attempt_2_score": 0.86,
    "final_critic_score": 0.86, "faithfulness": 0.89,
    "answer_relevancy": 0.84, "total_tokens": 8234,
    "inference_latency_ms": 24567, "cost_usd": 0.0147
})
# Written post-HITL review by OnApprove / OnReject Lambda
mlflow.set_tags({
    "human_verdict": "approved",   # or "rejected"
    "rater_id": "sre_ananya",
    "feedback_type": "wrong_rc"    # only on rejection
})
```

### B. Glossary

| Term | Definition |
|------|------------|
| **RAG** | Retrieval-Augmented Generation — LLM technique combining vector search with generation |
| **HITL** | Human-in-the-Loop — human review gate integrated into an automated pipeline |
| **HNSW** | Hierarchical Navigable Small World — graph-based approximate nearest neighbor algorithm |
| **LLMOps** | LLM Operations — practices for deploying and managing LLM systems in production |
| **RLHF** | Reinforcement Learning from Human Feedback — fine-tuning using human preferences |
| **Faithfulness** | Metric measuring if LLM output is grounded in provided context (no hallucinations) |
| **Answer Relevancy** | Metric measuring if LLM output addresses the original query |
| **Critic Agent** | LLM agent (Mistral 7B) that evaluates quality of other agents' outputs |
| **Self-Correction Loop** | Iterative refinement where critic feedback improves subsequent attempts |
| **waitForTaskToken** | Step Functions mechanism that pauses a state machine until an external signal resumes it |
| **XCom** | Airflow's cross-communication mechanism for passing data between tasks |
| **DAG** | Directed Acyclic Graph — Airflow's workflow definition structure |

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Citation

```bibtex
@software{inframind2026,
  author = {Nasim Raj Laskar},
  title  = {InfraMind: Autonomous Root Cause Analysis with Multi-Agent LLMs and Human-in-the-Loop Review},
  year   = {2026},
  url    = {https://github.com/nasim-raj-laskar/InfraMind}
}
```

---

## Support & Contact

- **Issues**: [GitHub Issues](https://github.com/nasim-raj-laskar/InfraMind/issues)
- **Discussions**: [GitHub Discussions](https://github.com/nasim-raj-laskar/InfraMind/discussions)
- **LinkedIn**: [Nasim Raj Laskar](https://linkedin.com/in/nasim-raj-laskar)

---

**Built with ❤️ for SRE teams fighting alert fatigue**

**Last Updated**: April 2026 · **Version**: 2.0.0 · **Maintainer**: Nasim Raj Laskar