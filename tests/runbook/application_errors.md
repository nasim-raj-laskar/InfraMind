# Application Errors & API Failures Runbook

## Overview
This runbook covers application-level incidents, HTTP error spikes, memory/CPU issues, and API failure patterns for InfraMind services.

---

## CRITICAL: HTTP Status Code Reference

| Status | Meaning | Owned by | First suspect |
|---|---|---|---|
| `499` | Client closed request | Client | Response too slow, client timeout |
| `500` | Internal server error | Application | Unhandled exception, check app logs |
| `502` | Bad gateway | Ingress/LB | App crashed or not listening, wrong port |
| `503` | Service unavailable | App/LB | App overloaded, no healthy replicas |
| `504` | Gateway timeout | Ingress/LB | App responding too slowly (> timeout threshold) |
| `429` | Too many requests | Application/API GW | Rate limiter triggered, upstream rate limiting |

**502 vs 503 vs 504 are different — never treat them the same:**
- `502` = connection to app refused or app returned garbage — app is likely down
- `503` = app is up but refusing requests — overloaded or circuit breaker open
- `504` = app is up and accepted request but didn't respond in time — slow processing

---

## Issue 1: HTTP 500 Error Spike

**Symptoms:**
- Sudden spike in 5xx error rate in monitoring
- Users reporting errors
- `ERROR 500` in application logs with stack traces

**Diagnosis:**
```bash
# Step 1: Get recent error logs
kubectl logs -l app=<service> -n <namespace> --since=15m | \
  grep -i "error\|exception\|panic" | tail -50

# Step 2: Check if it's all pods or specific ones
kubectl get pods -l app=<service> -n <namespace>
kubectl logs <specific-pod> --tail=100

# Step 3: Check error rate by endpoint (if you have request logging)
kubectl logs -l app=<service> --since=15m | \
  grep "500" | awk '{print $7}' | sort | uniq -c | sort -rn | head -10

# Step 4: Did anything change recently?
kubectl rollout history deployment/<service>
kubectl describe deployment/<service> | grep -A 3 "Annotations"

# Step 5: Check if dependencies are healthy
kubectl get pods -n <namespace>   # Are dependent services up?
```

**Root Cause Analysis:**
- Code bug introduced in recent deployment — correlate with rollout time
- Dependency (DB, cache, external API) returning errors — app failing to handle gracefully
- Configuration change (env var, secret) causing unexpected behavior
- Memory exhaustion causing request failures before OOMKill

**Immediate Fix:**
```bash
# If recent deployment — rollback immediately
kubectl rollout undo deployment/<service>
kubectl rollout status deployment/<service>

# If dependency is down — check circuit breaker / fallback
# Increase replicas if resource-constrained
kubectl scale deployment <service> --replicas=5

# Add more context to logs temporarily
kubectl set env deployment/<service> LOG_LEVEL=DEBUG
```

---

## Issue 2: HTTP 502 Bad Gateway

**Symptoms:**
- Ingress returning 502 to all clients
- App pods appear to be running but returning 502
- `kubectl logs` on ingress-nginx shows `connect() failed (111: Connection refused)`

**Diagnosis:**
```bash
# Step 1: Is the app actually listening on the correct port?
kubectl exec -it <app-pod> -- \
  netstat -tlnp | grep <expected-port>
# If empty — app crashed or listening on wrong port

# Step 2: Are endpoints registered?
kubectl get endpoints <service-name>
# Should show pod IPs — if empty, selector doesn't match pod labels

# Step 3: Test directly without ingress
kubectl port-forward pod/<pod-name> 8080:<app-port>
curl http://localhost:8080/health

# Step 4: Check readiness probe
kubectl describe pod <pod-name> | grep -A 5 "Readiness"
# If failing, pod is excluded from Service endpoints → 502
```

**Root Cause Analysis:**
- App listening on different port than Service/Ingress expects
- Readiness probe failing — pod excluded from load balancer but still "Running"
- App crashed after startup — process died but container didn't exit (supervisor issue)
- Service selector labels don't match pod labels — zero endpoints

**Immediate Fix:**
```bash
# Verify port configuration matches across pod, service, ingress
kubectl get pod <pod> -o yaml | grep containerPort
kubectl get service <svc> -o yaml | grep targetPort
kubectl get ingress <ing> -o yaml | grep servicePort

# If readiness probe too aggressive during startup, add initialDelaySeconds
kubectl patch deployment <deployment> -n <namespace> --type=merge -p '{
  "spec": {"template": {"spec": {"containers": [{
    "name": "<container>",
    "readinessProbe": {"initialDelaySeconds": 30}
  }]}}}}'
```

---

## Issue 3: HTTP 504 Gateway Timeout — Slow Responses

**Symptoms:**
- Requests timing out at 30s/60s boundary
- `504 Gateway Time-out` in ingress logs
- P99 latency spiking while P50 is normal (tail latency problem)

**Diagnosis:**
```bash
# Step 1: What's slow — the app or its dependencies?
# Check if DB is slow
kubectl exec -it <app-pod> -- \
  curl -w "@curl-format.txt" http://localhost:<port>/health
# If health check (no DB) is fast but other endpoints slow = DB bottleneck

# Step 2: Check for slow queries in DB
kubectl exec -it <db-pod> -- \
  mysql -e "SHOW FULL PROCESSLIST;" | grep -v Sleep

# Step 3: Check app thread/goroutine pool usage
kubectl exec -it <app-pod> -- \
  curl localhost:<metrics-port>/metrics | grep -E "threads|goroutines|workers"

# Step 4: Check for GC pauses (JVM apps)
kubectl logs <app-pod> | grep -i "GC pause\|stop-the-world"
```

**Root Cause Analysis:**
- Database slow query causing request to block — most common cause
- Thread pool exhausted — requests queuing behind slow requests
- GC pause on JVM app — stop-the-world pauses causing timeouts
- External API call without timeout — one slow upstream blocks threads
- N+1 query problem — code making 100s of DB calls per request

**Immediate Fix:**
```bash
# Increase ingress timeout temporarily while root cause is fixed
kubectl annotate ingress <ingress> \
  nginx.ingress.kubernetes.io/proxy-read-timeout="120" \
  nginx.ingress.kubernetes.io/proxy-send-timeout="120"

# Scale horizontally to reduce per-pod load
kubectl scale deployment <service> --replicas=8

# Kill slow DB queries immediately
kubectl exec -it <db-pod> -- \
  mysql -e "SHOW PROCESSLIST;" | \
  awk '$6 > 30 {print "KILL "$1";"}' | mysql
```

---

## Issue 4: 429 Too Many Requests — Rate Limiting

**Symptoms:**
- Clients receiving `429 Too Many Requests`
- `X-RateLimit-Remaining: 0` in response headers
- Specific user/IP being throttled or all traffic being throttled

**Diagnosis:**
```bash
# Check if it's your app's rate limiter or an upstream rate limiter
# Look at the response body — your rate limiter vs upstream's format

# Check API Gateway throttling (if using AWS API GW)
aws cloudwatch get-metric-statistics \
  --namespace AWS/ApiGateway \
  --metric-name 4XXError \
  --dimensions Name=ApiName,Value=inframind-api \
  --start-time $(date -u -d '30 minutes ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 60 --statistics Sum

# If calling external API — check their rate limit headers
kubectl logs <app-pod> | grep -i "rate.limit\|retry-after\|x-ratelimit"
```

**Root Cause Analysis:**
- Traffic spike exceeding rate limit config — limit too low for current traffic
- Retry storm — failed requests being retried aggressively, multiplying traffic
- Bug causing single user to make excessive requests
- External API rate limiting InfraMind — need backoff strategy

**Immediate Fix:**
```bash
# Increase API Gateway throttling limits
aws apigateway update-stage \
  --rest-api-id <api-id> \
  --stage-name production \
  --patch-operations \
    op=replace,path=/defaultRouteSettings/throttlingBurstLimit,value=1000 \
    op=replace,path=/defaultRouteSettings/throttlingRateLimit,value=500

# Add exponential backoff to external API calls in application code
# Immediate: reduce retry frequency if retry storm is the cause
kubectl set env deployment/<app> MAX_RETRIES=3 RETRY_BACKOFF_MS=1000
```

---

## Issue 5: Memory Leak — Gradual Performance Degradation

**Symptoms:**
- Application performance degrades over time (hours/days)
- Restarts make it better temporarily
- Memory usage grows continuously in metrics
- Happens consistently since a specific deployment

**Diagnosis:**
```bash
# Check memory usage trend
kubectl top pods -l app=<service> --sort-by=memory

# Check if memory grows over pod uptime
# Compare memory of pods with different restart ages
kubectl get pods -l app=<service> -o wide
# older pods = higher memory?

# For Node.js — get heap snapshot
kubectl exec -it <app-pod> -- \
  kill -USR2 1   # Triggers heapdump if configured

# For JVM — get heap dump
kubectl exec -it <app-pod> -- \
  jmap -dump:format=b,file=/tmp/heap.hprof 1
kubectl cp <pod>:/tmp/heap.hprof ./heap.hprof
```

**Root Cause Analysis:**
- Event listener or callback not cleaned up — memory accumulates over time
- Cache growing without eviction — data structure in memory never bounded
- Database connection not released — pool fills up slowly
- Log buffer growing — logs being buffered but not flushed

**Immediate Fix:**
```bash
# Set up automatic restart via pod lifecycle (buys time while fix is developed)
# Add to deployment spec:
kubectl patch deployment <deployment> -n <namespace> --type=merge -p '{
  "spec": {"template": {"spec": {
    "containers": [{
      "name": "<container>",
      "lifecycle": {
        "preStop": {"exec": {"command": ["/bin/sh", "-c", "sleep 5"]}}
      }
    }]
  }}}}'

# Schedule rolling restart every 24h as temporary mitigation (NOT a fix)
kubectl rollout restart deployment/<service>
```

---

## Application Performance Monitoring
```bash
# Check all service response times (if prometheus/metrics available)
kubectl exec -it <app-pod> -- \
  curl localhost:<metrics-port>/metrics | \
  grep -E "http_request_duration|request_total"

# Check pod resource usage across namespace
kubectl top pods -n <namespace> --sort-by=cpu
kubectl top pods -n <namespace> --sort-by=memory

# Get recent error events
kubectl get events -n <namespace> \
  --sort-by=.metadata.creationTimestamp | \
  grep -i "error\|fail\|kill" | tail -20

# Check HPA status (is autoscaling working?)
kubectl get hpa -n <namespace>
kubectl describe hpa <hpa-name>
```

---

## Escalation Contacts
- **Backend Engineering:** backend-team@company.com
- **Infrastructure Team:** infra-oncall@company.com
- **Security Team:** security-oncall@company.com (for unusual 4xx spikes)
- **Severity 1 Incidents:** +1-555-0123
