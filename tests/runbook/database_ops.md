# Database Operations Runbook

## Overview
This runbook covers database-related incidents, troubleshooting steps, and recovery procedures for InfraMind infrastructure. Primary database: AWS RDS (host pattern: `rds.cluster-inframind.internal`).

---

## CRITICAL: Connection Refused vs Connection Timeout

These are **different failures** requiring different responses. Never conflate them.

| Error | Meaning | First suspect |
|---|---|---|
| `connection refused` | Server not accepting connections at all | RDS instance down, security group blocking port, wrong host/port |
| `connection timeout` | Server reachable but not responding in time | Pool exhaustion, overloaded DB, network latency |
| `host not found` / `no such host` | DNS resolution failed | VPC DNS issue, wrong internal hostname |

---

## Issue 1: Connection Refused (ERROR 500 / ECONNREFUSED)

**Symptoms:**
- `connection refused` in application logs
- `ERROR 500: Database connection refused`
- `dial tcp: connect: connection refused`

**Diagnosis (in order):**

1. **Is the RDS instance running?**
   ```bash
   aws rds describe-db-instances \
     --query 'DBInstances[*].[DBInstanceIdentifier,DBInstanceStatus]'
   # Expected: "available" — anything else is the root cause
   ```

2. **Is the security group allowing your pod's traffic?**
   ```bash
   # Get the pod's node IP
   kubectl get pod <pod-name> -o wide
   # Verify SG allows inbound 3306/5432 from that node's CIDR
   aws ec2 describe-security-groups --group-ids <rds-sg-id>
   ```

3. **Is the internal DNS resolving correctly?**
   ```bash
   kubectl exec -it <app-pod> -- nslookup rds.cluster-inframind.internal
   # Should resolve to a private IP in your VPC range
   ```

4. **Is the port actually open?**
   ```bash
   kubectl exec -it <app-pod> -- nc -zv rds.cluster-inframind.internal 3306
   # "succeeded" = network OK, problem is elsewhere
   # "refused" = SG or RDS not listening
   ```

**Root Cause Analysis:**
- RDS instance stopped, rebooting, or in maintenance window — MOST LIKELY CAUSE
- Security group blocking port 3306/5432 from EKS node subnet
- RDS max_connections reached — server actively refusing new connections
- Wrong port in application config (MySQL: 3306, PostgreSQL: 5432)

**IMPORTANT: "connection refused" means server is reachable but REJECTING the
connection. VPC routing failures cause TIMEOUT or "no route to host" — NEVER
"connection refused". Do NOT investigate VPC routing for this error.**

**Immediate Fix:**
```bash
# 1. Check RDS status
aws rds describe-db-instances --db-instance-identifier inframind-db

# 2. If stopped, start it
aws rds start-db-instance --db-instance-identifier inframind-db

# 3. If SG issue, temporarily allow all from node CIDR (then fix properly)
aws ec2 authorize-security-group-ingress \
  --group-id <rds-sg-id> \
  --protocol tcp --port 3306 \
  --cidr <node-subnet-cidr>
```

---

## Issue 2: Connection Timeout

**Symptoms:**
- `connection timeout` errors in logs
- Applications unable to connect to database
- High connection pool exhaustion
- `too many connections` errors

**Diagnosis:**

1. **Check current connection count:**
   ```sql
   SHOW STATUS LIKE 'Threads_connected';
   SHOW VARIABLES LIKE 'max_connections';
   -- If Threads_connected is close to max_connections, pool is exhausted
   ```

2. **Find who is holding connections:**
   ```sql
   SHOW FULL PROCESSLIST;
   -- Look for long-running queries in "Sleep" state eating connections
   ```

3. **Check RDS CloudWatch metrics:**
   ```bash
   aws cloudwatch get-metric-statistics \
     --namespace AWS/RDS \
     --metric-name DatabaseConnections \
     --dimensions Name=DBInstanceIdentifier,Value=inframind-db \
     --start-time $(date -u -d '1 hour ago' +%FT%TZ) \
     --end-time $(date -u +%FT%TZ) \
     --period 300 --statistics Average
   ```

**Root Cause Analysis:**
- Connection pool misconfiguration (pool size too large for RDS max_connections)
- Application not releasing connections (missing `finally` / `with` blocks)
- Unoptimized queries causing locks and holding connections open
- Insufficient RDS instance class for the connection demand

**Immediate Fix:**
```bash
# Kill idle connections older than 5 minutes
kubectl exec -it <app-pod> -- mysql -h rds.cluster-inframind.internal \
  -e "SELECT CONCAT('KILL ', id, ';') FROM information_schema.processlist 
      WHERE command='Sleep' AND time > 300;" | mysql -h rds.cluster-inframind.internal

# Restart application pods to reset connection pools
kubectl rollout restart deployment/<app-deployment>
```

---

## Issue 3: RDS Hostname Not Resolving

**Symptoms:**
- `no such host` or `unknown host rds.cluster-inframind.internal`
- Works from some pods but not others

**Diagnosis:**
```bash
# Check CoreDNS is healthy
kubectl get pods -n kube-system -l k8s-app=kube-dns

# Test resolution from a debug pod
kubectl run dns-test --image=busybox --rm -it -- \
  nslookup rds.cluster-inframind.internal

# Check if VPC DNS is enabled on the VPC
aws ec2 describe-vpc-attribute --vpc-id <vpc-id> --attribute enableDnsSupport
aws ec2 describe-vpc-attribute --vpc-id <vpc-id> --attribute enableDnsHostnames
```

**Root Cause Analysis:**
- VPC DNS support or DNS hostnames disabled
- CoreDNS misconfigured or crashing
- Private hosted zone in Route53 missing or misconfigured

---

## Issue 4: Disk Space / Write Failures

**Symptoms:**
- `No space left on device` errors
- Database write failures
- Transaction log growth

**Immediate Actions:**
```bash
# Check RDS allocated storage vs used
aws rds describe-db-instances \
  --db-instance-identifier inframind-db \
  --query 'DBInstances[0].AllocatedStorage'

# Enable storage autoscaling if not already on
aws rds modify-db-instance \
  --db-instance-identifier inframind-db \
  --max-allocated-storage 500

# Purge old binary logs (MySQL)
mysql -e "PURGE BINARY LOGS BEFORE DATE_SUB(NOW(), INTERVAL 7 DAY);"
```

---

## Issue 5: Performance Degradation

**Symptoms:**
- Slow query response times (> 1s for simple queries)
- High CPU/Memory on RDS instance
- Lock wait timeouts

**Immediate Actions:**
```bash
# Check RDS CPU/memory via CloudWatch
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS --metric-name CPUUtilization \
  --dimensions Name=DBInstanceIdentifier,Value=inframind-db \
  --start-time $(date -u -d '30 minutes ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 60 --statistics Average

# Find slow queries
kubectl exec -it <db-pod> -- \
  mysql -e "SELECT * FROM performance_schema.events_statements_summary_by_digest 
            ORDER BY AVG_TIMER_WAIT DESC LIMIT 10;"
```

---

## Recovery Procedures

### Failover to Read Replica
```bash
# Promote read replica (causes brief downtime)
aws rds promote-read-replica \
  --db-instance-identifier inframind-db-replica

# Update app to point to replica
kubectl set env deployment/<app> DB_HOST=inframind-db-replica.cluster-inframind.internal
```

### Restore from Snapshot
```bash
# List available snapshots
aws rds describe-db-snapshots \
  --db-instance-identifier inframind-db \
  --query 'DBSnapshots[*].[DBSnapshotIdentifier,SnapshotCreateTime]'

# Restore
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier inframind-db-restored \
  --db-snapshot-identifier <snapshot-id>
```

---

## Monitoring Commands
```bash
# Pod-level DB metrics
kubectl top pods -l app=database

# Live connection count
mysql -h rds.cluster-inframind.internal \
  -e "SHOW STATUS LIKE 'Threads_connected';"

# Recent DB pod logs
kubectl logs -l app=database --tail=100 --timestamps
```

---

## Escalation Contacts
- **Primary DBA:** dba-team@company.com
- **Infrastructure Team:** infra-oncall@company.com
- **Severity 1 Incidents:** +1-555-0123
