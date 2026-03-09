# Database Operations Runbook

## Overview
This runbook covers database-related incidents, troubleshooting steps, and recovery procedures for InfraMind infrastructure.

## Common Database Issues

### Connection Timeouts
**Symptoms:**
- `connection timeout` errors in logs
- Applications unable to connect to database
- High connection pool exhaustion

**Immediate Actions:**
1. Check database server status: `kubectl get pods -l app=database`
2. Verify connection limits: `SHOW VARIABLES LIKE 'max_connections';`
3. Kill long-running queries: `SHOW PROCESSLIST;`

**Root Cause Analysis:**
- Connection pool misconfiguration
- Unoptimized queries causing locks
- Insufficient database resources

### Disk Space Issues
**Symptoms:**
- `No space left on device` errors
- Database write failures
- Transaction log growth

**Immediate Actions:**
1. Check disk usage: `df -h /var/lib/mysql`
2. Identify large files: `du -sh /var/lib/mysql/*`
3. Purge old logs: `PURGE BINARY LOGS BEFORE DATE_SUB(NOW(), INTERVAL 7 DAY);`

### Performance Degradation
**Symptoms:**
- Slow query response times
- High CPU/Memory usage
- Lock wait timeouts

**Immediate Actions:**
1. Check slow query log
2. Analyze current queries: `SHOW FULL PROCESSLIST;`
3. Review index usage: `EXPLAIN SELECT ...`

## Recovery Procedures

### Database Backup Restoration
```bash
# Stop application services
kubectl scale deployment app --replicas=0

# Restore from backup
mysql -u root -p database_name < backup_file.sql

# Restart services
kubectl scale deployment app --replicas=3
```

### Failover to Replica
```bash
# Promote read replica
kubectl patch service db-service -p '{"spec":{"selector":{"role":"replica"}}}'

# Update application config
kubectl set env deployment/app DB_HOST=replica-host
```

## Monitoring Commands
```bash
# Check database metrics
kubectl top pods -l app=database

# View recent logs
kubectl logs -l app=database --tail=100

# Connection status
mysql -e "SHOW STATUS LIKE 'Threads_connected';"
```

## Escalation Contacts
- **Primary DBA:** dba-team@company.com
- **Infrastructure Team:** infra-oncall@company.com
- **Severity 1 Incidents:** +1-555-0123