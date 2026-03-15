# CI/CD Pipeline Runbook

## Overview
This runbook covers CI/CD pipeline failures, deployment issues, and build system incidents for InfraMind infrastructure. Primary stack: GitHub Actions, AWS CodePipeline, ArgoCD, Docker.

---

## CRITICAL: Pipeline Failure Quick Reference

| Error | Stage | First suspect |
|---|---|---|
| `exit code 1` in build | Build | Compilation error, failing test, lint error |
| `no space left on device` | Build | Docker layer cache full on runner |
| `unauthorized` / `denied` | Push | Registry credentials expired or missing |
| `ImagePullBackOff` after deploy | Deploy | Image tag mismatch, wrong registry |
| `OutOfSync` in ArgoCD | Deploy | Manifest drift, failed sync |
| `context deadline exceeded` | Any | Timeout — network issue or slow step |
| `permission denied` | Any | IAM role missing policy, secret not mounted |

---

## Issue 1: Build Failing — Compilation or Test Error

**Symptoms:**
- Pipeline fails at build or test stage with exit code 1
- Error messages in build logs referencing specific files/lines

**Diagnosis:**
```bash
# In GitHub Actions: check the failed step's logs directly
# Look for the FIRST error — subsequent errors are often cascading

# For Docker builds — reproduce locally
docker build -t inframind-app:debug . 2>&1 | tail -50

# For test failures — run tests locally with same env vars
docker run --rm \
  -e DATABASE_URL=$DATABASE_URL \
  inframind-app:debug \
  pytest tests/ -v

# Check if it was passing before (recent commit broke it)
git log --oneline -10
git bisect start
git bisect bad HEAD
git bisect good <last-known-good-commit>
```

**Root Cause Analysis:**
- Code change introduced compilation error or broke tests
- Dependency version pinning issue — upstream package changed behavior
- Environment variable missing in CI that exists locally
- Test relies on external service that is down

**Immediate Fix:**
```bash
# Re-run with fresh cache (rules out stale cache issues)
# GitHub Actions: use "Re-run jobs" with "Re-run with fresh cache" option

# If specific dependency is broken, pin to last working version
# In requirements.txt / package.json / go.mod

# Add missing env var to CI secrets
# GitHub: Settings → Secrets → Actions → New repository secret
```

---

## Issue 2: Docker Image Build — No Space Left on Device

**Symptoms:**
- Build fails with `no space left on device`
- Happens on self-hosted runners or after many builds
- `docker build` fails partway through

**Diagnosis:**
```bash
# Check runner disk usage
df -h /

# Check Docker disk usage
docker system df

# Find largest layers
docker system df -v | head -30
```

**Root Cause Analysis:**
- Docker build cache accumulating on runner
- Old images not being pruned between builds
- Large build artifacts being copied into image unnecessarily

**Immediate Fix:**
```bash
# Prune unused Docker objects (safe — only removes dangling/unused)
docker system prune -f

# More aggressive cleanup (removes all unused images)
docker system prune -af

# Add to CI pipeline as a post-build step to prevent recurrence
docker image prune -f --filter "until=24h"
```

**Prevention — add to `.dockerignore`:**
```
node_modules/
.git/
*.log
dist/
coverage/
.pytest_cache/
__pycache__/
```

---

## Issue 3: Image Push Failing — Unauthorized / Access Denied

**Symptoms:**
- Build succeeds but push fails with `unauthorized` or `denied: requested access to the resource is denied`
- ECR push failing in CI

**Diagnosis:**
```bash
# For ECR — check if login step ran and token is fresh (ECR tokens expire after 12h)
aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin \
  <account-id>.dkr.ecr.ap-south-1.amazonaws.com

# Check if the repository exists
aws ecr describe-repositories --repository-names inframind-app

# Check CI role has ECR push permissions
aws iam simulate-principal-policy \
  --policy-source-arn <ci-role-arn> \
  --action-names ecr:PutImage ecr:InitiateLayerUpload \
  --resource-arns arn:aws:ecr:ap-south-1:<account>:repository/inframind-app
```

**Root Cause Analysis:**
- ECR authentication token expired (12-hour TTL) — login step missing or skipped
- CI IAM role missing `ecr:PutImage`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart` permissions
- ECR repository doesn't exist yet — must be created before first push
- Wrong AWS region in push command

**Immediate Fix:**
```yaml
# Add ECR login step BEFORE docker push in GitHub Actions
- name: Login to ECR
  run: |
    aws ecr get-login-password --region ap-south-1 | \
    docker login --username AWS --password-stdin \
    ${{ secrets.AWS_ACCOUNT_ID }}.dkr.ecr.ap-south-1.amazonaws.com

# Ensure these IAM permissions on CI role:
# ecr:GetAuthorizationToken
# ecr:BatchCheckLayerAvailability
# ecr:PutImage
# ecr:InitiateLayerUpload
# ecr:UploadLayerPart
# ecr:CompleteLayerUpload
```

---

## Issue 4: ArgoCD Sync Failing / OutOfSync

**Symptoms:**
- ArgoCD shows application as `OutOfSync` or `Degraded`
- Sync fails with error in ArgoCD UI
- New deployment not rolling out despite successful image push

**Diagnosis:**
```bash
# Check ArgoCD app status
kubectl get application <app-name> -n argocd

# Get detailed sync error
kubectl describe application <app-name> -n argocd | grep -A 20 "Sync Status"

# Check ArgoCD application controller logs
kubectl logs -n argocd deployment/argocd-application-controller | tail -50

# Manual sync attempt with verbose output
argocd app sync <app-name> --debug

# Common sync errors:
# "ComparisonError" = ArgoCD can't read the Git repo or manifest
# "hook failed" = pre/post sync hook job failed
# "resource already exists" = conflict with manually applied resource
```

**Root Cause Analysis:**
- Git repository unreachable (token expired, wrong URL)
- Manifest YAML syntax error preventing parsing
- Resource already exists in cluster with different owner (manual `kubectl apply` conflict)
- Sync hook (pre/post) job failed — check hook job logs
- ArgoCD lacks RBAC to create/update the resource type

**Immediate Fix:**
```bash
# Force sync (skips hook failures, use carefully)
argocd app sync <app-name> --force

# Hard refresh (clears ArgoCD cache and re-reads from Git)
argocd app get <app-name> --hard-refresh

# If manual resource conflict — remove the annotation and let ArgoCD own it
kubectl annotate <resource> <name> \
  argocd.argoproj.io/managed-by=argocd \
  --overwrite

# Roll back to last successful version in ArgoCD
argocd app rollback <app-name> <revision-id>
```

---

## Issue 5: Deployment Succeeds but Service Not Updated

**Symptoms:**
- Pipeline shows green
- Old version still running in cluster
- Image tag in deployment hasn't changed

**Diagnosis:**
```bash
# Check what image is actually running
kubectl get deployment <deployment> -n <namespace> \
  -o jsonpath='{.spec.template.spec.containers[0].image}'

# Check if deployment was actually updated
kubectl rollout history deployment/<deployment> -n <namespace>

# Check if imagePullPolicy is preventing refresh
kubectl get deployment <deployment> -o yaml | grep imagePullPolicy
# "IfNotPresent" with "latest" tag = image never refreshed!
```

**Root Cause Analysis:**
- Using `latest` tag with `imagePullPolicy: IfNotPresent` — node already has the old `latest` image and won't re-pull
- ArgoCD not watching the correct Git branch
- CD step updating wrong namespace or deployment name
- Deployment update succeeded but rollout is stuck (check Issue 6 in kubernetes runbook)

**Fix:**
```yaml
# NEVER use latest tag in production — always use immutable tags
image: inframind-app:${GIT_SHA}   # Use Git commit SHA as tag

# If you must use mutable tags, force re-pull
imagePullPolicy: Always           # Always pull — slower but always fresh
```

---

## Issue 6: Pipeline Timeout (Context Deadline Exceeded)

**Symptoms:**
- Step times out after 10-30 minutes
- `context deadline exceeded` or `SIGTERM received`
- Happens inconsistently (sometimes passes, sometimes fails)

**Diagnosis:**
```bash
# Identify which step is slow
# In GitHub Actions — check timestamps on each step

# For slow Docker builds — check if cache is working
# Look for "CACHED" lines in build output. No CACHED = cache miss every time.

# For slow kubectl apply — check if cluster API server is under load
kubectl get --raw /healthz
kubectl top nodes
```

**Root Cause Analysis:**
- Docker build cache not being restored (cache key misconfigured)
- Network timeout pulling large base images from Docker Hub (rate limited)
- Cluster API server overloaded during deployment step
- Test suite has a hanging test with no timeout configured

**Immediate Fix:**
```yaml
# Add timeouts to GitHub Actions steps explicitly
- name: Deploy
  timeout-minutes: 10
  run: kubectl apply -f k8s/

# Use ECR mirror for Docker Hub to avoid rate limits
# Set in daemon.json: "registry-mirrors": ["<ecr-public-mirror>"]

# Add Docker layer caching in GitHub Actions
- uses: actions/cache@v3
  with:
    path: /tmp/.buildx-cache
    key: ${{ runner.os }}-buildx-${{ github.sha }}
    restore-keys: |
      ${{ runner.os }}-buildx-
```

---

## Pipeline Health Checks
```bash
# Check all ArgoCD apps
kubectl get applications -n argocd

# Check recent GitHub Actions runs via CLI
gh run list --limit 10

# Check ECR for recently pushed images
aws ecr describe-images \
  --repository-name inframind-app \
  --query 'sort_by(imageDetails,&imagePushedAt)[-5:].[imageTags[0],imagePushedAt]'

# Check CodePipeline status
aws codepipeline list-pipeline-executions \
  --pipeline-name inframind-pipeline \
  --max-items 5
```

---

## Escalation Contacts
- **Platform Engineering:** platform-team@company.com
- **DevOps Team:** devops-oncall@company.com
- **Severity 1 Incidents:** +1-555-0123
