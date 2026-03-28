# InfraMind

**A LLMOps platform for autonomous infrastructure root cause analysis (RCA)** — leveraging multi-agent orchestration, retrieval-augmented generation (RAG), and self-correcting LLM workflows on AWS Bedrock. Built for SRE/DevOps teams requiring zero-touch incident triage with full observability, experiment tracking, and quality gates.

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
        A1["1 Investigator"] --> A2["2 Root Cause <br/> evidence"] --> A3["3 Fix Generator <br/> remediation steps"] --> A4["4 Formatter <br/> structured JSON"]
        A4 --> CRITIC{"5 Critic <br/> Mistral 7B Score ≥ 0.8?"}
        CRITIC -->|"No <br/> inject feedback"| A1
        CRITIC -->|Max retries| FAIL["FAILED"]
    end

    subgraph OBS["Observability & Output"]
        MLFLOW["MLflow · DagsHub <br/> params · metrics · artifacts"]
        DEEPEVAL["DeepEval <br/> faithfulness · relevancy · recall"]
        GRAFANA["Grafana <br/> throughput · cost · latency"]
        S3_OUT["S3 rca-results/ <br/>structured JSON"]
        SLACK["Slack <br/> incident alert"]
    end

    NORM_OUT --> CHECK
    NORM_OUT -->|query embedding| CHROMA
    L8 & L70 -->|LLM inference| A1
    CONTEXT -->|injected into prompts| A1
    CRITIC -->|pass| S3_OUT
    CRITIC -->|pass| SLACK
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

    class LOGS,FETCH,NORM,NORM_OUT teal
    class RUNBOOKS,EMBED,CHROMA,CONTEXT blue
    class CHECK,L8,L70 amber
    class A1,A2,A3,A4 purple
    class CRITIC coral
    class FAIL red
    class MLFLOW,DEEPEVAL,GRAFANA,S3_OUT,SLACK green
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
    participant S3
    
    Airflow->>Orchestrator: Trigger RCA (normalized_log)
    
    loop Self-Correction (max 2 retries)
        Orchestrator->>RAG: Query runbook context<br/>(semantic search)
        RAG-->>Orchestrator: Top-6 relevant chunks
        
        Orchestrator->>Bedrock: Agent 1: Investigator<br/>(log + context)
        Bedrock-->>Orchestrator: Incident summary
        
        Orchestrator->>Bedrock: Agent 2: Root Cause<br/>(summary + context)
        Bedrock-->>Orchestrator: Hypothesis + evidence
        
        Orchestrator->>Bedrock: Agent 3: Fix Generator<br/>(root cause + context)
        Bedrock-->>Orchestrator: Remediation steps
        
        Orchestrator->>Bedrock: Agent 4: Formatter<br/>(all outputs)
        Bedrock-->>Orchestrator: Structured RCA JSON
        
        Orchestrator->>Critic: Evaluate RCA quality
        Critic->>Bedrock: Score faithfulness + relevancy (Mistral-7B)
        Bedrock-->>Critic: Quality metrics
        Critic-->>Orchestrator: Score + feedback
        
        Orchestrator->>MLflow: Log attempt metrics<br/>(score, tokens, latency)
        
        alt Score ≥ 0.8
            Orchestrator->>S3: Write final RCA
            Orchestrator->>MLflow: Mark run SUCCESS
            Orchestrator-->>Airflow: Complete
        else Score < 0.8 && retries left
            Note over Orchestrator: Inject critic feedback<br/>into next iteration
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
        SPLIT["RecursiveCharacterTextSplitter<br/>chunk_size=1000<br/>overlap=200"]
        EMB_IDX["Bedrock Titan Embed<br/>1536-dim vectors"]
        STORE[("ChromaDB<br/>Persistent Storage")]
        
        MD --> SPLIT
        SPLIT --> EMB_IDX
        EMB_IDX --> STORE
    end
    
    subgraph "Query Phase (Per RCA)"
        QUERY["Log Error Context<br/>+ Agent Question"]
        EMB_Q["Bedrock Titan Embed<br/>Query Vector"]
        SEARCH["Cosine Similarity<br/>Top-K=6"]
        RERANK["MMR Reranking<br/>Diversity Filter"]
        CONTEXT["Augmented Context<br/>to LLM Prompt"]
        
        QUERY --> EMB_Q
        EMB_Q --> SEARCH
        STORE -."Vector Search".-> SEARCH
        SEARCH --> RERANK
        RERANK --> CONTEXT
    end
    
    style STORE fill:#4a3a1a,color:#fff
    style CONTEXT fill:#1a4a2e,color:#fff
```

---

## Technology Stack

### LLMOps & GenAI Infrastructure

```mermaid
graph TB
    subgraph "Orchestration Layer"
        AIRFLOW["Apache Airflow 3.0<br/>Astro Runtime 12.x<br/>DAG-based Workflow"]
    end
    
    subgraph "LLM Inference (AWS Bedrock)"
        LLAMA8["Llama 3 8B<br/>Short logs ≤ 2000 chars<br/>$0.0003/1K input tokens"]
        LLAMA70["Llama 3 70B<br/>Long logs > 2000 chars<br/>$0.00265/1K input tokens"]
        MISTRAL["Mistral 7B<br/>Critic agent only<br/>Quality scoring"]
        TITAN["Titan Embed v2<br/>1536-dim embeddings<br/>RAG indexing + retrieval"]
    end
    
    subgraph "Vector Store & RAG"
        CHROMA["ChromaDB<br/>Persistent HNSW index<br/>Docker volume mount"]
        LANGCHAIN["LangChain<br/>RAG orchestration<br/>Prompt templates"]
    end
    
    subgraph "LLMOps Observability"
        MLFLOW["MLflow 2.x<br/>DagsHub remote backend<br/>Experiment tracking"]
        DEEPEVAL["DeepEval<br/>LLM-as-judge metrics<br/>Faithfulness + Relevancy"]
    end
    
    subgraph "Data Layer"
        S3["AWS S3<br/>Log storage + RCA results<br/>Lifecycle policies"]
    end
    
    AIRFLOW --> LLAMA8
    AIRFLOW --> LLAMA70
    AIRFLOW --> MISTRAL
    AIRFLOW --> CHROMA
    CHROMA --> TITAN
    LANGCHAIN --> CHROMA
    LANGCHAIN --> LLAMA8
    LLAMA8 --> MLFLOW
    LLAMA70 --> MLFLOW
    MISTRAL --> MLFLOW
    MISTRAL --> DEEPEVAL
    AIRFLOW --> S3
    
    style LLAMA8 fill:#1a3a5c,color:#fff
    style LLAMA70 fill:#1a3a5c,color:#fff
    style MISTRAL fill:#4a1a5c,color:#fff
    style TITAN fill:#1a4a2e,color:#fff
    style MLFLOW fill:#1a4a2e,color:#fff
    style CHROMA fill:#4a3a1a,color:#fff
```

| Component | Technology | Purpose |
|-----------|-----------|----------|
| **Orchestration** | Apache Airflow 3 (Astro CLI) | DAG-based pipeline scheduling, task dependency management |
| **LLM Runtime** | AWS Bedrock (Llama 3 8B / 70B + Mistral 7B) | Serverless LLM inference — model auto-selected by log size |
| **Embeddings** | AWS Bedrock Titan Embed v2 | 1536-dim semantic vectors for RAG retrieval |
| **Vector DB** | ChromaDB 0.4.x | Persistent HNSW index with cosine similarity search |
| **RAG Framework** | LangChain 0.1.x | Prompt engineering, retrieval chains, agent orchestration |
| **Experiment Tracking** | MLflow 2.x (DagsHub) | Hyperparameter logging, metric tracking, model versioning |
| **LLM Evaluation** | DeepEval 0.21.x | Faithfulness, answer relevancy, contextual recall metrics |
| **Object Storage** | AWS S3 | Raw logs, processed logs, RCA results, model artifacts |
| **Monitoring** | Prometheus + Grafana | Pipeline metrics, token usage, latency tracking |

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

## Deployment Architecture

### Containerized Airflow on Astro Runtime

```mermaid
graph TB
    subgraph "Docker Compose Stack"
        subgraph "Airflow Components"
            WEBSERVER["Webserver<br/>:8080<br/>UI + REST API"]
            SCHEDULER["Scheduler<br/>DAG parsing<br/>Task scheduling"]
            TRIGGERER["Triggerer<br/>Async task support"]
            POSTGRES[("PostgreSQL<br/>Metadata DB")]
        end
        
        subgraph "Persistent Volumes"
            DAGS_VOL["./dags/<br/>DAG definitions"]
            CHROMA_VOL["./chroma_data/<br/>Vector DB persist"]
            LOGS_VOL["./logs/<br/>Task logs"]
        end
        
        subgraph "External Services"
            BEDROCK["AWS Bedrock<br/>ap-south-1<br/>LLM inference"]
            S3["S3 Bucket<br/>Log storage"]
            MLFLOW["DagsHub MLflow<br/>Experiment tracking"]
        end
    end
    
    WEBSERVER --> POSTGRES
    SCHEDULER --> POSTGRES
    SCHEDULER --> DAGS_VOL
    SCHEDULER --> CHROMA_VOL
    SCHEDULER --> BEDROCK
    SCHEDULER --> S3
    SCHEDULER --> MLFLOW
    TRIGGERER --> POSTGRES
    
    style SCHEDULER fill:#1a3a5c,color:#fff
    style CHROMA_VOL fill:#4a3a1a,color:#fff
    style BEDROCK fill:#4a1a1a,color:#fff
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
| **AWS Account** | Bedrock enabled | Llama 3, Mistral 7B + Titan Embed in `ap-south-1` region |
| **S3 Bucket** | Standard tier | Versioning + lifecycle policies recommended |
| **DagsHub Account** | Free tier | MLflow tracking backend |

### AWS Bedrock Model Access

Enable the following models in AWS Console → Bedrock → Model access:

- `meta.llama3-8b-instruct-v1:0` — fast inference for short logs
- `meta.llama3-70b-instruct-v1:0` — deep reasoning for large logs
- `mistral.mistral-7b-instruct-v0:2` — critic / quality scoring agent
- `amazon.titan-embed-text-v2:0` — RAG embeddings

**Region**: `ap-south-1` (Mumbai) — lowest latency for Asia-Pacific

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
# AWS Credentials (IAM user with Bedrock + S3 access)
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_REGION=ap-south-1

# DagsHub MLflow Tracking
DAGSHUB_USERNAME=your_username
DAGSHUB_TOKEN=your_dagshub_token
MLFLOW_TRACKING_URI=https://dagshub.com/your_username/InfraMind.mlflow

```

The IAM policy should grant `bedrock:InvokeModel` on the four model ARNs above (`meta.llama3-*`, `mistral.mistral-7b-*`, `amazon.titan-embed-*`), plus `s3:GetObject`, `s3:PutObject`, `s3:ListBucket`, and `s3:DeleteObject` on your bucket.

### 3. Start Airflow Stack

**Windows (PowerShell)**:

```powershell
.\restart.ps1
```

This script:
1. Stops existing Airflow containers
2. Prunes Docker volumes (clean state)
3. Starts Astro dev environment
4. Configures Airflow pools and variables
5. Opens browser to `http://localhost:8080`

**macOS/Linux**:

```bash
# Start Airflow
astro dev start --no-browser --settings-file /dev/null

# Configure pools and variables
SCHEDULER=$(docker ps --format "{{.Names}}" | grep scheduler)

docker exec $SCHEDULER airflow pools set single_thread_pool 1 "ChromaDB write lock"
docker exec $SCHEDULER airflow variables set INFRAMIND_S3_BUCKET your-bucket-name
docker exec $SCHEDULER airflow variables set INFRAMIND_S3_PREFIX raw/
docker exec $SCHEDULER airflow variables set INFRAMIND_MAX_LOGS 3
docker exec $SCHEDULER airflow variables set INFRAMIND_FORCE_REBUILD false
```

### 4. Verify Deployment

```bash
# Check container health
docker ps --filter "name=inframind"

# View scheduler logs
docker logs -f $(docker ps --format "{{.Names}}" | grep scheduler)

# Test ChromaDB persistence
docker exec $(docker ps --format "{{.Names}}" | grep scheduler) \
  python -c "from core.vectordb import VectorDB; db = VectorDB(); print(db.collection.count())"
```

### 5. Trigger DAG

**Via Airflow UI**:
1. Navigate to `http://localhost:8080` (admin / admin)
2. Enable `inframind_rca_pipeline` DAG
3. Click "Trigger DAG" → "Trigger"

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

## S3 Data Layout & Lifecycle

### Bucket Structure

```mermaid
graph TB
    subgraph "S3 Bucket: your-bucket-name"
        subgraph "raw/ (Input)"
            RAW1["app_api_20260316.log<br/>CloudWatch JSON"]
            RAW2["kubelet_20260315.log<br/>K8s structured"]
            RAW3["postgres_error.log<br/>RDS logs"]
        end
        
        subgraph "processed/ (Archive)"
            PROC1["app_api_20260316.log<br/>Moved after RCA"]
            PROC2["kubelet_20260315.log<br/>Moved after RCA"]
        end
        
        subgraph "rca-results/ (Output)"
            RCA1["results_20260316_175519.json<br/>Structured RCA"]
            RCA2["results_20260315_143022.json<br/>Structured RCA"]
        end
        
        subgraph "mlflow-artifacts/ (Optional)"
            ARTIFACT1["run_abc123/<br/>Model outputs"]
        end
    end
    
    RAW1 -."After RCA".-> PROC1
    RAW2 -."After RCA".-> PROC2
    
    style RAW1 fill:#4a3a1a,color:#fff
    style RCA1 fill:#1a4a2e,color:#fff
```

### Automated Lifecycle Policies

**Recommended S3 Lifecycle Rules**:

```json
{
  "Rules": [
    {
      "Id": "ArchiveProcessedLogs",
      "Status": "Enabled",
      "Filter": {"Prefix": "processed/"},
      "Transitions": [
        {"Days": 30, "StorageClass": "STANDARD_IA"},
        {"Days": 90, "StorageClass": "GLACIER"}
      ]
    },
    {
      "Id": "RetainRCAResults",
      "Status": "Enabled",
      "Filter": {"Prefix": "rca-results/"},
      "Transitions": [
        {"Days": 365, "StorageClass": "GLACIER_DEEP_ARCHIVE"}
      ]
    },
    {
      "Id": "DeleteOldArtifacts",
      "Status": "Enabled",
      "Filter": {"Prefix": "mlflow-artifacts/"},
      "Expiration": {"Days": 180}
    }
  ]
}
```

**Cost Optimization**:
- `raw/`: Standard storage (transient, deleted after move)
- `processed/`: Standard → IA (30d) → Glacier (90d)
- `rca-results/`: Standard → Deep Archive (365d)
- `mlflow-artifacts/`: Auto-delete after 180 days

---

## Configuration Management

### Hierarchical Config Architecture

```mermaid
graph TB
    subgraph "Configuration Sources"
        YAML["config/settings.yaml<br/>Pipeline defaults"]
        MODELS["config/models.yaml<br/>LLM model specs"]
        ENV[".env<br/>Secrets & credentials"]
        AIRFLOW_VARS["Airflow Variables<br/>Runtime overrides"]
    end
    
    subgraph "Config Loader (config/config.py)"
        LOADER["Singleton ConfigManager<br/>Merge + validate"]
    end
    
    subgraph "Runtime Components"
        DAG["Airflow DAG"]
        AGENTS["LLM Agents"]
        RAG["RAG Pipeline"]
    end
    
    YAML --> LOADER
    MODELS --> LOADER
    ENV --> LOADER
    AIRFLOW_VARS --> LOADER
    
    LOADER --> DAG
    LOADER --> AGENTS
    LOADER --> RAG
    
    style LOADER fill:#4a3a1a,color:#fff
```

### Key Configuration Parameters

**Pipeline Settings** (`config/settings.yaml`)

| Parameter | Default | Description | Impact |
|-----------|---------|-------------|--------|
| `pipeline.max_retries` | `2` | Self-correction loop iterations | Higher = better quality, more cost |
| `pipeline.quality_threshold` | `0.8` | Minimum critic score to accept RCA | Lower = faster, less reliable |
| `pipeline.timeout_seconds` | `300` | Max execution time per log | Prevents runaway LLM calls |
| `vectordb.chunk_k` | `6` | RAG retrieval count | Higher = more context, slower |
| `vectordb.chunk_size` | `1000` | Text splitter chunk size | Affects retrieval granularity |
| `vectordb.chunk_overlap` | `200` | Overlap between chunks | Prevents context boundary loss |

**Model Configuration** (`config/models.yaml`)

The model used for Investigator, Root Cause, Fix Generator, and Formatter agents is chosen dynamically at runtime based on log size — Llama 3 8B for logs under 2000 characters, Llama 3 70B for anything larger. The Critic agent always uses Mistral 7B. Titan Embed v2 handles all RAG embeddings.

```mermaid
graph LR
    LOG["Incoming Log"]
    CHECK{"Log size > 2000 chars?"}
    SMALL["Llama 3 8B\nInvestigator / Root Cause\nFix Generator / Formatter"]
    LARGE["Llama 3 70B\nInvestigator / Root Cause\nFix Generator / Formatter"]
    CRITIC["Mistral 7B\nCritic Agent\n(always)"]

    LOG --> CHECK
    CHECK -->|"No"| SMALL
    CHECK -->|"Yes"| LARGE
    SMALL --> CRITIC
    LARGE --> CRITIC

    style SMALL fill:#1a3a5c,color:#fff
    style LARGE fill:#1a3a5c,color:#fff
    style CRITIC fill:#4a1a5c,color:#fff
    style CHECK fill:#4a3a1a,color:#fff
```

**Airflow Variables** (Runtime Overrides)

| Variable | Default | Description |
|----------|---------|-------------|
| `INFRAMIND_MAX_LOGS` | `3` | Batch size per DAG run (cost control) |
| `INFRAMIND_FORCE_REBUILD` | `false` | Rebuild ChromaDB index (after runbook updates) |
| `INFRAMIND_SLACK_WEBHOOK` | — | Incident notification endpoint |
| `INFRAMIND_ENABLE_CACHE` | `true` | Cache LLM responses (dev mode) |
| `INFRAMIND_LOG_LEVEL` | `INFO` | Logging verbosity (DEBUG/INFO/WARNING) |

---

## RCA Output Schema

### Structured JSON Format

Each RCA result in `s3://bucket/rca-results/results_YYYYMMDD_HHMMSS.json`:

```json
{
  "incident_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-03-16T17:55:19Z",
  "log_source": "s3://bucket/raw/app_api_20260316.log",
  "severity": "High",
  "summary": "PostgreSQL connection pool exhaustion causing API 503 errors",
  "root_cause": "Max connections (100) exceeded due to connection leak in ORM session management. Connections not properly closed after exception handling in user_service.py:L247.",
  "immediate_fix": "1. Restart API pods to reset connection pool\n2. Apply connection timeout (30s) via ConfigMap\n3. Deploy hotfix with explicit session.close() in finally block",
  "preventive_measures": [
    "Implement connection pool monitoring alerts (threshold: 80%)",
    "Add circuit breaker pattern for database calls",
    "Enable pgBouncer connection pooling layer"
  ],
  "confidence": 0.91,
  "model_used": "meta.llama3-8b-instruct-v1:0",
  "mlflow_run_id": "a3f2c1b0d9e8f7a6b5c4d3e2f1a0b9c8",
  "attempts": 2,
  "final_score": 0.85,
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
  ],
  "status": "success"
}
```

### Output Schema Validation

```mermaid
graph LR
    subgraph "Agent Pipeline Output"
        FORMATTER["Formatter Agent<br/>Structured JSON"]
    end
    
    subgraph "Validation Layer"
        SCHEMA["Pydantic Model<br/>RCAOutput"]
        CHECKS["Field Validation<br/>• UUID format<br/>• Severity enum<br/>• Score range [0,1]<br/>• Required fields"]
    end
    
    subgraph "Storage"
        S3["S3 rca-results/<br/>Immutable storage"]
        METADATA["Airflow XCom<br/>Task metadata"]
    end
    
    FORMATTER --> SCHEMA
    SCHEMA --> CHECKS
    CHECKS -->|"Valid"| S3
    CHECKS -->|"Valid"| METADATA
    CHECKS -->|"Invalid"| ERROR["Raise ValidationError<br/>Retry with feedback"]
    
    style CHECKS fill:#4a3a1a,color:#fff
    style ERROR fill:#4a1a1a,color:#fff
```

---

## LLMOps: Experiment Tracking & Observability

### MLflow Integration Architecture

```mermaid
graph TB
    subgraph "Airflow Worker"
        TASK["RCA Task Execution"]
        TRACKER["MLflow Client<br/>tracker.py"]
    end
    
    subgraph "MLflow Tracking Server (DagsHub)"
        BACKEND["PostgreSQL Backend<br/>Experiment metadata"]
        ARTIFACTS["S3 Artifact Store<br/>Model outputs"]
        UI["MLflow UI<br/>Experiment comparison"]
    end
    
    subgraph "Logged Metrics per RCA Run"
        PARAMS["Parameters<br/>• model_name<br/>• temperature<br/>• max_tokens<br/>• chunk_k"]
        METRICS["Metrics<br/>• attempt_N_score<br/>• final_critic_score<br/>• faithfulness<br/>• answer_relevancy<br/>• token_count<br/>• latency_ms<br/>• cost_usd"]
        ARTIFACTS_LOG["Artifacts<br/>• rca_output.json<br/>• critic_feedback.txt<br/>• retrieved_context.md<br/>• prompt_templates/"]
    end
    
    TASK --> TRACKER
    TRACKER -->|"mlflow.log_param()"| PARAMS
    TRACKER -->|"mlflow.log_metric()"| METRICS
    TRACKER -->|"mlflow.log_artifact()"| ARTIFACTS_LOG
    
    PARAMS --> BACKEND
    METRICS --> BACKEND
    ARTIFACTS_LOG --> ARTIFACTS
    
    BACKEND --> UI
    ARTIFACTS --> UI
    
    style TRACKER fill:#1a4a2e,color:#fff
    style UI fill:#1a3a5c,color:#fff
```

### Tracked Telemetry per RCA Execution

**Parameters (Hyperparameters)**
- `model_name`: Llama 3 8B or 70B (auto-selected by log size) / Mistral 7B (critic)
- `temperature`: 0.0 (deterministic) to 1.0 (creative)
- `max_tokens`: Response length limit
- `chunk_k`: RAG retrieval count
- `quality_threshold`: Critic acceptance score

**Metrics (Performance)**
- `attempt_1_score`, `attempt_2_score`: Per-iteration critic scores
- `final_critic_score`: Accepted RCA quality (0.0-1.0)
- `faithfulness`: DeepEval groundedness metric
- `answer_relevancy`: DeepEval relevance to query
- `total_tokens`: Input + output token count
- `inference_latency_ms`: End-to-end LLM call duration
- `cost_usd`: Bedrock API cost (tokens × pricing)

**Artifacts (Outputs)**
- `rca_output.json`: Final structured RCA result
- `critic_feedback.txt`: Quality assessment reasoning
- `retrieved_context.md`: RAG chunks used in prompt
- `agent_prompts/`: All 5 agent prompt templates with variables

**Access MLflow UI**: `https://dagshub.com/<username>/InfraMind.mlflow`

### DeepEval LLM-as-Judge Metrics

```mermaid
graph LR
    subgraph "Evaluation Pipeline"
        RCA["Generated RCA Output"]
        CONTEXT["Retrieved RAG Context"]
        QUERY["Original Log Query"]
        
        EVAL1["FaithfulnessMetric<br/>Hallucination detection"]
        EVAL2["AnswerRelevancyMetric<br/>Query alignment"]
        EVAL3["ContextualRecallMetric<br/>Context utilization"]
        
        JUDGE["Mistral 7B<br/>LLM-as-Judge<br/>(Critic Agent)"]
        SCORES["Normalized Scores<br/>0.0 - 1.0"]
    end
    
    RCA --> EVAL1
    CONTEXT --> EVAL1
    RCA --> EVAL2
    QUERY --> EVAL2
    RCA --> EVAL3
    CONTEXT --> EVAL3
    
    EVAL1 --> JUDGE
    EVAL2 --> JUDGE
    EVAL3 --> JUDGE
    
    JUDGE --> SCORES
    SCORES -."Log to MLflow".-> MLFLOW[("MLflow Tracking")]
    
    style JUDGE fill:#4a1a5c,color:#fff
    style MLFLOW fill:#1a4a2e,color:#fff
```

---

## Supported Log Formats & Parsers

### Multi-Format Normalization Pipeline

```mermaid
graph LR
    subgraph "Raw Log Formats"
        K8S["Kubernetes<br/>kubelet, kube-apiserver<br/>Structured JSON"]
        CW["CloudWatch<br/>JSON exports<br/>Nested fields"]
        RDS["RDS / PostgreSQL<br/>Error logs<br/>Plaintext"]
        APP["Application Logs<br/>JSON structured<br/>Custom schemas"]
        SYSLOG["Syslog<br/>RFC 3164/5424<br/>Plaintext"]
    end
    
    subgraph "Normalizer (core/normalizer.py)"
        DETECT["Format Detection<br/>Regex + heuristics"]
        PARSE["Parser Dispatch<br/>Format-specific logic"]
        NORM["Normalized Schema<br/>timestamp, level, message, metadata"]
    end
    
    K8S --> DETECT
    CW --> DETECT
    RDS --> DETECT
    APP --> DETECT
    SYSLOG --> DETECT
    
    DETECT --> PARSE
    PARSE --> NORM
    
    NORM --> OUTPUT["Standardized JSON<br/>Ready for LLM ingestion"]
    
    style DETECT fill:#4a3a1a,color:#fff
    style OUTPUT fill:#1a4a2e,color:#fff
```

### Normalized Output Schema

```json
{
  "timestamp": "2026-03-16T17:45:32Z",
  "level": "ERROR",
  "source": "app-api-pod-7f8d9c",
  "message": "Connection pool exhausted: max_connections=100",
  "metadata": {
    "namespace": "production",
    "pod_ip": "10.0.1.42",
    "error_code": "SQLSTATE[08006]"
  },
  "raw_log": "<original log line>"
}
```

**Supported Formats**:
- **Kubernetes**: `kubelet`, `kube-apiserver`, `kube-scheduler` (JSON)
- **CloudWatch**: Exported JSON logs with nested `@timestamp`, `@message`
- **RDS**: PostgreSQL error logs, MySQL slow query logs
- **Application**: JSON structured logs (Logstash, Fluentd, Winston)
- **Syslog**: RFC 3164 (BSD) and RFC 5424 (IETF) formats

---

## Prompt Engineering & Agent Design

### Agent Prompt Template Architecture

```mermaid
graph TB
    subgraph "Prompt Templates (prompts/)"
        INV["investigator.txt<br/>Extract incident details"]
        ROOT["root_cause.txt<br/>Hypothesis generation"]
        FIX["fix_generator.txt<br/>Remediation steps"]
        FMT["formatter.txt<br/>JSON structuring"]
        CRIT["critic.txt<br/>Quality assessment"]
    end
    
    subgraph "Prompt Variables"
        VARS["Jinja2 Templating<br/>• {{log_content}}<br/>• {{rag_context}}<br/>• {{previous_output}}<br/>• {{critic_feedback}}"]
    end
    
    subgraph "LangChain Prompt Chain"
        CHAIN["PromptTemplate<br/>+ ChatPromptTemplate<br/>+ SystemMessage"]
    end
    
    INV --> VARS
    ROOT --> VARS
    FIX --> VARS
    FMT --> VARS
    CRIT --> VARS
    
    VARS --> CHAIN
    CHAIN --> BEDROCK["AWS Bedrock<br/>Llama 3 8B / 70B + Mistral 7B"]
    
    style VARS fill:#4a3a1a,color:#fff
    style BEDROCK fill:#1a3a5c,color:#fff
```

### Example: Root Cause Agent Prompt

```markdown
# ROLE
You are an expert SRE analyzing infrastructure incidents.

# TASK
Given the incident summary and retrieved runbook context, generate a detailed root cause hypothesis.

# INPUT
## Incident Summary
{{incident_summary}}

## Retrieved Runbook Context
{{rag_context}}

## Previous Attempt Feedback (if retry)
{{critic_feedback}}

# OUTPUT FORMAT
Provide:
1. Root cause hypothesis (2-3 sentences)
2. Supporting evidence from logs
3. Confidence level (0.0-1.0)

# CONSTRAINTS
- Base analysis ONLY on provided context
- Cite specific log lines or runbook sections
- If uncertain, state assumptions explicitly
```

### Prompt Optimization Techniques

| Technique | Implementation | Benefit |
|-----------|----------------|----------|
| **Few-shot examples** | Include 2-3 example RCAs in system prompt | Improves output structure consistency |
| **Chain-of-thought** | "Think step-by-step" instruction | Better reasoning for complex failures |
| **Role prompting** | "You are an expert SRE..." | Activates domain-specific knowledge |
| **Output constraints** | JSON schema in prompt | Reduces parsing errors |
| **Context injection** | RAG chunks + previous outputs | Grounds LLM in factual data |

---

## Cost Optimization & Token Management

### Token Usage Breakdown

```mermaid
graph TB
    subgraph "Per-RCA Token Budget"
        INPUT["Input Tokens<br/>• Log content: ~500<br/>• RAG context: ~3000<br/>• Prompt template: ~800<br/>• Previous outputs: ~1500<br/>Total: ~5800"]
        
        OUTPUT["Output Tokens<br/>• Investigator: ~500<br/>• Root Cause: ~800<br/>• Fix Generator: ~600<br/>• Formatter: ~400<br/>• Critic: ~200<br/>Total: ~2500"]
    end
    
    subgraph "Cost Calculation"
        LLAMA8["Llama 3 8B<br/>Input: $0.0003/1K<br/>Output: $0.0006/1K"]
        LLAMA70["Llama 3 70B<br/>Input: $0.00265/1K<br/>Output: $0.0035/1K"]
        MISTRAL["Mistral 7B<br/>Critic only<br/>Low token count"]
        
        COST["Per-RCA Cost<br/>8B path: ~$0.003<br/>70B path: ~$0.018<br/>Avg mixed: ~$0.010"]
    end
    
    INPUT --> LLAMA8
    OUTPUT --> LLAMA8
    INPUT --> LLAMA70
    OUTPUT --> LLAMA70
    INPUT --> MISTRAL
    OUTPUT --> MISTRAL
    
    LLAMA8 --> COST
    LLAMA70 --> COST
    MISTRAL --> COST
    
    style LLAMA8 fill:#1a3a5c,color:#fff
    style LLAMA70 fill:#1a3a5c,color:#fff
    style MISTRAL fill:#4a1a5c,color:#fff
    style COST fill:#1a4a2e,color:#fff
```

### Cost Optimization Strategies

**1. Dynamic Model Selection**
- **Investigator, Root Cause, Fix Generator, Formatter**: Llama 3 8B for short logs (≤ 2000 chars), Llama 3 70B for long logs — decided automatically at runtime
- **Critic**: Always Mistral 7B

**2. Context Window Management** — logs are truncated at 2000 characters (the model-selection threshold), and RAG retrieval is capped at top-6 chunks.

**3. Response Caching** (Dev/Test) — LLM responses are cached by prompt hash to avoid redundant Bedrock calls during development.

**4. Batch Processing**
- Process multiple logs in parallel (Airflow dynamic task mapping)
- Amortize RAG indexing cost across batch

**Monthly Cost Estimate** (1000 logs/month, 2 retries avg, mixed 8B/70B):

1000 logs × 2 attempts × ~$0.010 avg = **~$20/month** in LLM costs, plus ~$5/month S3 and free-tier DagsHub MLflow — roughly **$25/month total** for self-hosted.

---

## Monitoring & Observability

### Metrics Collection Architecture

```mermaid
graph TB
    subgraph "Airflow Metrics"
        DAG_METRICS["DAG Metrics<br/>• dag_run_duration<br/>• task_success_rate<br/>• pool_utilization"]
        STATSD["StatsD Exporter<br/>UDP :8125"]
    end
    
    subgraph "Custom Application Metrics"
        APP_METRICS["Python Metrics<br/>• rca_attempts_total<br/>• critic_score_histogram<br/>• token_usage_counter<br/>• bedrock_latency_summary"]
        PROM_CLIENT["Prometheus Client<br/>prometheus_client.py"]
    end
    
    subgraph "Prometheus"
        PROM[("Prometheus<br/>:9090<br/>Time-series DB")]
        SCRAPE["Scrape Targets<br/>• Airflow :8080/metrics<br/>• App :8000/metrics"]
    end
    
    subgraph "Grafana"
        DASH["InfraMind Dashboard<br/>• RCA success rate<br/>• Token cost trends<br/>• Latency percentiles<br/>• Critic score distribution"]
    end
    
    DAG_METRICS --> STATSD
    STATSD --> PROM
    APP_METRICS --> PROM_CLIENT
    PROM_CLIENT --> PROM
    SCRAPE --> PROM
    PROM --> DASH
    
    style PROM fill:#4a1a1a,color:#fff
    style DASH fill:#1a3a5c,color:#fff
```

### Key Performance Indicators (KPIs)

| Metric | Target | Alert Threshold |
|--------|--------|------------------|
| **RCA Success Rate** | ≥ 95% | < 90% |
| **Avg Critic Score** | ≥ 0.85 | < 0.75 |
| **P95 Latency** | ≤ 30s | > 60s |
| **Token Cost/RCA** | ≤ $0.02 | > $0.05 |
| **ChromaDB Query Time** | ≤ 500ms | > 2s |
| **Faithfulness Score** | ≥ 0.80 | < 0.70 |

### Grafana Dashboard Panels

See `monitoring/grafana/dashboards/inframind.json` for full configuration.

**Panel Highlights**:
1. **RCA Pipeline Throughput**: Logs processed per hour
2. **Multi-Agent Latency Breakdown**: Time spent per agent
3. **Token Usage Heatmap**: Cost distribution by hour/day
4. **Critic Score Distribution**: Quality histogram
5. **RAG Retrieval Accuracy**: Context relevance metrics
6. **Bedrock API Errors**: Rate limiting, throttling events

---

## Troubleshooting & Debugging

### Common Issues

#### 1. ChromaDB Collection Not Found

**Symptom**: `ValueError: Collection 'runbook_embeddings' does not exist`

**Solution**:
```bash
# Force rebuild vector index
docker exec $(docker ps --format "{{.Names}}" | grep scheduler) \
  airflow variables set INFRAMIND_FORCE_REBUILD true

# Trigger DAG to rebuild
docker exec $(docker ps --format "{{.Names}}" | grep scheduler) \
  airflow dags trigger inframind_rca_pipeline
```

#### 2. Bedrock Throttling (429 Errors)

**Symptom**: `botocore.exceptions.ClientError: ThrottlingException`

**Solution**:
```python
# Add exponential backoff in core/bedrock_client.py
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
def invoke_model(self, prompt):
    # ... existing code
```

#### 3. Low Critic Scores (< 0.8)

**Symptom**: RCA retries exhausted, final score below threshold

**Diagnosis**:
```bash
# Check MLflow run for detailed metrics
mlflow ui --backend-store-uri $MLFLOW_TRACKING_URI

# Review critic feedback
cat logs/rca_critic_feedback_<run_id>.txt
```

**Solutions**:
- Improve RAG context: Add more runbooks to `runbook/`
- Tune chunk_k: Increase from 6 to 10 in `config/settings.yaml`
- Force 70B: Set `log_size_threshold: 0` in `config/settings.yaml` to always use Llama 3 70B

#### 4. High Token Costs

**Symptom**: Monthly bill exceeds budget

**Mitigation**:
```yaml
# config/settings.yaml
pipeline:
  max_retries: 1  # Reduce from 2
  quality_threshold: 0.75  # Lower from 0.8

vectordb:
  chunk_k: 4  # Reduce from 6
```

### Debug Mode

```bash
# Enable verbose logging
docker exec $(docker ps --format "{{.Names}}" | grep scheduler) \
  airflow variables set INFRAMIND_LOG_LEVEL DEBUG

# Tail scheduler logs
docker logs -f $(docker ps --format "{{.Names}}" | grep scheduler) | grep InfraMind

# Inspect task logs in Airflow UI
# http://localhost:8080 → DAGs → inframind_rca_pipeline → Graph → Click task → Logs
```

---

## Roadmap & Future Enhancements

### Q2 2026
- [ ] **Fine-tuned Critic Model**: RLHF on human feedback data
- [ ] **Streaming RCA**: Real-time log ingestion via Kinesis
- [ ] **Multi-modal Analysis**: Image support (Grafana screenshots, architecture diagrams)

### Q3 2026
- [ ] **Agentic Remediation**: Auto-execute fixes via Ansible/Terraform
- [ ] **Federated Learning**: Cross-tenant model improvements (privacy-preserving)
- [ ] **Graph RAG**: Knowledge graph for incident correlation

### Q4 2026
- [ ] **Causal Inference**: Bayesian networks for root cause ranking
- [ ] **Explainable AI**: SHAP values for LLM decision transparency

---



## Appendix

### A. Prompt Template Examples

**Investigator Agent** (`prompts/investigator.txt`):
```
You are an expert Site Reliability Engineer analyzing infrastructure logs.

TASK: Extract key incident details from the provided log.

LOG CONTENT:
{{log_content}}

RUNBOOK CONTEXT:
{{rag_context}}

OUTPUT FORMAT (JSON):
{
  "timestamp": "ISO 8601 timestamp of first error",
  "severity": "Critical|High|Medium|Low",
  "affected_components": ["list of impacted services/pods/nodes"],
  "error_patterns": ["list of recurring error messages"],
  "summary": "2-3 sentence incident description"
}

CONSTRAINTS:
- Base analysis ONLY on provided log content
- Do not speculate beyond available data
- If severity is unclear, default to "Medium"
```

### B. MLflow Experiment Schema

**Run Parameters**:
```python
mlflow.log_params({
    "model_name": "meta.llama3-8b-instruct-v1:0",
    "temperature": 0.1,
    "max_tokens": 2048,
    "chunk_k": 6,
    "quality_threshold": 0.8,
    "max_retries": 2,
    "log_source": "s3://bucket/raw/app.log"
})
```

**Run Metrics**:
```python
mlflow.log_metrics({
    "attempt_1_score": 0.72,
    "attempt_2_score": 0.86,
    "final_critic_score": 0.86,
    "faithfulness": 0.89,
    "answer_relevancy": 0.84,
    "contextual_recall": 0.81,
    "total_tokens": 8234,
    "input_tokens": 5821,
    "output_tokens": 2413,
    "inference_latency_ms": 24567,
    "cost_usd": 0.0147
})
```

**Run Tags**:
```python
mlflow.set_tags({
    "incident_id": "550e8400-e29b-41d4-a716-446655440000",
    "severity": "High",
    "status": "success",
    "dag_run_id": "manual__2026-03-16T17:55:19+00:00"
})
```

### C. ChromaDB Collection Metadata

```python
# Collection schema
collection = chroma_client.get_or_create_collection(
    name="runbook_embeddings",
    metadata={
        "hnsw:space": "cosine",
        "hnsw:construction_ef": 200,
        "hnsw:search_ef": 100,
        "hnsw:M": 16
    },
    embedding_function=bedrock_embed_fn
)

# Document metadata structure
metadata = {
    "source": "runbook/database/connection_pool_tuning.md",
    "chunk_index": 3,
    "total_chunks": 12,
    "category": "database",
    "last_updated": "2026-03-15T10:30:00Z",
    "author": "sre-team"
}
```

### D. Airflow Variable Reference

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `INFRAMIND_S3_BUCKET` | String | — | S3 bucket name (required) |
| `INFRAMIND_S3_PREFIX` | String | `raw/` | Log file prefix |
| `INFRAMIND_MAX_LOGS` | Integer | `3` | Max logs per DAG run |
| `INFRAMIND_FORCE_REBUILD` | Boolean | `false` | Rebuild ChromaDB index |
| `INFRAMIND_SLACK_WEBHOOK` | String | — | Slack notification URL |
| `INFRAMIND_ENABLE_CACHE` | Boolean | `true` | Cache LLM responses |
| `INFRAMIND_LOG_LEVEL` | String | `INFO` | Logging verbosity |
| `INFRAMIND_TIMEOUT` | Integer | `300` | Task timeout (seconds) |
| `INFRAMIND_RETRY_DELAY` | Integer | `60` | Retry delay (seconds) |

### E. Cost Breakdown Example

**Scenario**: 1000 logs/month, 2 attempts average, mixed Haiku/Sonnet

```
LLM Inference (50% 8B / 50% 70B split):
  - Agents 1-4 via Llama 3 8B (500 logs): 500 × 2 × 6000 tokens × $0.0003/1K = $1.80
  - Agents 1-4 via Llama 3 70B (500 logs): 500 × 2 × 6000 tokens × $0.00265/1K = $15.90
  - Critic via Mistral 7B (all logs): 1000 × 2 × 2000 tokens × $0.0002/1K = $0.80
  Subtotal: ~$18.50

Embeddings (Titan Embed v2):
  - RAG queries: 1000 × 2 × 500 tokens × $0.0001/1K = $0.10
  - Runbook indexing (one-time): ~$0.03
  Subtotal: $0.13

S3 Storage:
  - Processed logs + RCA results: ~$1.27
  Subtotal: $1.27

Airflow (Astro Cloud - optional): $175/month (or $0 for self-hosted)
MLflow (DagsHub): Free tier — $0

TOTAL (Self-hosted): ~$20/month
TOTAL (Astro Cloud): ~$195/month
```

### F. Glossary

| Term | Definition |
|------|------------|
| **RAG** | Retrieval-Augmented Generation - LLM technique combining vector search with generation |
| **HNSW** | Hierarchical Navigable Small World - graph-based approximate nearest neighbor algorithm |
| **LLMOps** | LLM Operations - practices for deploying and managing LLM systems in production |
| **RLHF** | Reinforcement Learning from Human Feedback - fine-tuning method using human preferences |
| **Faithfulness** | Metric measuring if LLM output is grounded in provided context (no hallucinations) |
| **Answer Relevancy** | Metric measuring if LLM output addresses the original query |
| **Contextual Recall** | Metric measuring if LLM utilized all relevant information from context |
| **Critic Agent** | LLM agent that evaluates quality of other agents' outputs |
| **Self-Correction Loop** | Iterative refinement process where critic feedback improves subsequent attempts |
| **XCom** | Airflow's cross-communication mechanism for passing data between tasks |
| **DAG** | Directed Acyclic Graph - Airflow's workflow definition structure |

---

## License

MIT License - see [LICENSE](LICENSE) file for details.

---

## Citation

If you use InfraMind in your research or production systems, please cite:

```bibtex
@software{inframind2026,
  author = {Nasim Raj Laskar},
  title = {InfraMind: Autonomous Root Cause Analysis with Multi-Agent LLMs},
  year = {2026},
  url = {https://github.com/nasim-raj-laskar/InfraMind},
  note = {LLMOps platform for infrastructure incident triage}
}
```

---

## Support & Contact

- **Issues**: [GitHub Issues](https://github.com/nasim-raj-laskar/InfraMind/issues)
- **Discussions**: [GitHub Discussions](https://github.com/nasim-raj-laskar/InfraMind/discussions)
- **Email**: nasim.raj.laskar@example.com
- **LinkedIn**: [Nasim Raj Laskar](https://linkedin.com/in/nasim-raj-laskar)

---

**Built with ❤️ for SRE teams fighting alert fatigue**

---

**Last Updated**: March 16, 2026  
**Version**: 1.0.0  
**Maintainer**: Nasim Raj Laskar
