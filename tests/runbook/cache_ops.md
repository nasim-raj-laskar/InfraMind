# Cache Operations Runbook

## Overview
This runbook covers caching incidents, Redis failures, cache invalidation issues, and performance problems for InfraMind infrastructure. Primary cache: AWS ElastiCache Redis (host pattern: `redis.cache-inframind.internal`).

---

## CRITICAL: Cache Error Quick Reference

| Error | Meaning | First suspect |
|---|---|---|
| `ECONNREFUSED` on Redis port 6379 | Redis not accepting connections | ElastiCache node down, SG blocking 6379 |
| `READONLY You can't write against a read only replica` | Writing to replica instead of primary | Client pointed at wrong endpoint |
| `OOM command not allowed` | Redis out of memory | `maxmemory-policy` is `noeviction`, cache full |
| `WRONGTYPE Operation against a key holding wrong kind of value` | Key type mismatch in code | Bug — wrong data type being used for key |
| `ERR max number of clients reached` | Too many connections | Connection pool too large, connections not released |
| `Loading Redis is loading the dataset in memory` | Redis restarted and loading RDB/AOF | Wait — normal after restart, takes 30–120s |

---

## Issue 1: Cannot Connect to Redis (ECONNREFUSED)

**Symptoms:**
- `redis: dial tcp <ip>:6379: connect: connection refused`
- Application falling back to database for every request (cache miss storm)
- Response times spiking

**Diagnosis:**
```bash
# Step 1: Is the ElastiCache cluster available?
aws elasticache describe-replication-groups \
  --replication-group-id inframind-cache \
  --query 'ReplicationGroups[0].Status'
# Expected: "available"

# Step 2: Can your pod reach Redis?
kubectl exec -it <app-pod> -- \
  nc -zv redis.cache-inframind.internal 6379

# Step 3: Is the security group allowing port 6379 from EKS nodes?
aws ec2 describe-security-groups \
  --group-ids <elasticache-sg-id> \
  --query 'SecurityGroups[0].IpPermissions'

# Step 4: Is DNS resolving?
kubectl exec -it <app-pod> -- \
  nslookup redis.cache-inframind.internal
```

**Root Cause Analysis:**
- ElastiCache node failed over — primary endpoint changes during failover (60–120s)
- Security group missing inbound rule for port 6379 from EKS node subnet
- ElastiCache cluster is in maintenance window (check AWS console)
- Application using cluster node endpoint instead of primary endpoint — breaks on failover

**Immediate Fix:**
```bash
# Always use the PRIMARY ENDPOINT, not node endpoints
# Primary: inframind-cache.xxxxx.ng.0001.use1.cache.amazonaws.com
# NOT: inframind-cache-0001-001.xxxxx.ng.0001.use1.cache.amazonaws.com

# Check ElastiCache events for failover or maintenance
aws elasticache describe-events \
  --source-identifier inframind-cache \
  --source-type replication-group \
  --duration 60

# If cluster is down, restart it
aws elasticache reboot-replication-group \
  --replication-group-id inframind-cache \
  --reboot-cache-cluster-nodes 0001
```

---

## Issue 2: Redis Out of Memory (OOM)

**Symptoms:**
- `OOM command not allowed when used memory > maxmemory`
- Write operations failing, reads still working
- Cache hit rate dropping — OOM errors on set operations

**Diagnosis:**
```bash
# Check memory usage
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal INFO memory | grep -E "used_memory_human|maxmemory_human|mem_fragmentation"

# Check eviction policy
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal CONFIG GET maxmemory-policy
# "noeviction" = OOM errors when full (bad for cache)
# "allkeys-lru" = evicts least recently used (good for cache)

# Check which keys are largest
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal --bigkeys
```

**Root Cause Analysis:**
- `maxmemory-policy` set to `noeviction` — Redis refuses writes when full instead of evicting
- Cache keys have no TTL — data accumulates forever, never evicted
- Single large key consuming disproportionate memory (check `--bigkeys`)
- Memory fragmentation — reported usage higher than actual data

**Immediate Fix:**
```bash
# Switch to LRU eviction immediately (non-destructive)
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal \
  CONFIG SET maxmemory-policy allkeys-lru

# Find and delete keys with no TTL (be careful in production)
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal \
  --scan --pattern '*' | while read key; do
    ttl=$(redis-cli -h redis.cache-inframind.internal TTL "$key")
    [ "$ttl" -eq -1 ] && echo "No TTL: $key"
  done

# Scale up ElastiCache node if consistently near limit
aws elasticache modify-replication-group \
  --replication-group-id inframind-cache \
  --cache-node-type cache.r6g.large \
  --apply-immediately
```

---

## Issue 3: Writing to Read Replica (READONLY Error)

**Symptoms:**
- `READONLY You can't write against a read only replica`
- Writes failing but reads working
- Error appears after a failover event

**Diagnosis:**
```bash
# Check which endpoint the app is using
kubectl get deployment <app> -o yaml | grep REDIS_URL

# Check who is currently the primary
aws elasticache describe-replication-groups \
  --replication-group-id inframind-cache \
  --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint'
```

**Root Cause Analysis:**
- After a failover, the old primary became a replica but app config still points to it
- Application hardcoded to a node endpoint instead of the primary/cluster endpoint
- Read/write split implemented incorrectly — writes going to read replica

**Immediate Fix:**
```bash
# Update app to use primary endpoint
kubectl set env deployment/<app> \
  REDIS_URL=redis://inframind-cache.xxxxx.ng.0001.use1.cache.amazonaws.com:6379

# Rollout to pick up new env var
kubectl rollout restart deployment/<app>
```

---

## Issue 4: Cache Stampede / Miss Storm

**Symptoms:**
- Database CPU spikes to 100% suddenly
- Response times spike for all users simultaneously
- Happens after Redis restart or cache flush
- Cache hit rate drops from ~95% to ~0% suddenly

**Diagnosis:**
```bash
# Check cache hit rate
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal INFO stats | \
  grep -E "keyspace_hits|keyspace_misses"
# hit_rate = hits / (hits + misses)

# Check if Redis was recently restarted
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal INFO server | grep uptime_in_seconds
```

**Root Cause Analysis:**
- Redis restarted without persistence (RDB/AOF disabled) — all cache lost, every request hits DB
- All cache keys had same TTL — they all expired simultaneously
- Cache was manually flushed (`FLUSHALL`) without warning
- Application deployed with cache-busting key change

**Immediate Fix:**
```bash
# Temporarily reduce database connection pool to prevent DB overload
kubectl set env deployment/<app> DB_POOL_SIZE=5

# Rate limit cache warming requests if possible
# Enable Redis persistence to survive restarts
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal CONFIG SET save "3600 1 300 100 60 10000"

# Add jitter to TTL in application code to prevent synchronized expiry
# Instead of: cache.set(key, value, ttl=3600)
# Use: cache.set(key, value, ttl=3600 + random(0, 300))
```

---

## Issue 5: Too Many Connections (max clients reached)

**Symptoms:**
- `ERR max number of clients reached`
- New connections refused while app is under load
- Redis connection pool exhausted in application

**Diagnosis:**
```bash
# Check connected clients
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal INFO clients
# connected_clients vs maxclients

# See what's connecting
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal CLIENT LIST | wc -l

# Check per-client info (look for idle clients hogging connections)
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal CLIENT LIST | grep "idle=[0-9][0-9][0-9]"
```

**Root Cause Analysis:**
- Application connection pool size too large for Redis `maxclients` limit (default: 65536 but ElastiCache may be lower)
- Connections not being released back to pool — missing `finally` block or context manager
- Too many application pods × pool size > Redis maxclients

**Immediate Fix:**
```bash
# Kill idle connections (idle > 60 seconds)
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal \
  CLIENT KILL ID $(redis-cli CLIENT LIST | awk -F'[ =]' '/idle=([6-9][0-9]|[0-9]{3})/{print $2}')

# Increase Redis maxclients if needed
aws elasticache modify-replication-group \
  --replication-group-id inframind-cache \
  --cache-parameter-group-name inframind-params

# Reduce connection pool size in application
# Each pod's pool should be: maxclients / num_pods / 2 (safety margin)
```

---

## Redis Health Check Commands
```bash
# Ping Redis
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal PING
# Expected: PONG

# Full info dump
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal INFO all

# Memory fragmentation ratio (>1.5 = problem, run MEMORY PURGE)
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal INFO memory | grep mem_fragmentation_ratio

# Slow log (queries > 10ms)
kubectl exec -it <app-pod> -- redis-cli \
  -h redis.cache-inframind.internal SLOWLOG GET 10
```

---

## Escalation Contacts
- **Infrastructure Team:** infra-oncall@company.com
- **Backend Engineering:** backend-team@company.com
- **Severity 1 Incidents:** +1-555-0123
