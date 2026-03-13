# Messaging & Queue Operations Runbook

## Overview
This runbook covers message queue incidents, consumer lag, dead letter queues, and broker failures for InfraMind infrastructure. Primary systems: AWS SQS, Apache Kafka (MSK).

---

## CRITICAL: Queue Error Quick Reference

| Error | System | First suspect |
|---|---|---|
| `consumer lag growing` | Kafka | Consumer crashed, partition rebalancing, slow processing |
| `Message not visible` | SQS | Visibility timeout too short, consumer crashed mid-process |
| `LeaderNotAvailableException` | Kafka | Broker restarting, partition leader election in progress |
| `NotLeaderForPartitionException` | Kafka | Stale metadata — consumer needs to refresh broker list |
| `Queue depth > threshold` | SQS/Kafka | Consumer down, processing too slow, traffic spike |
| `Dead letter queue filling up` | SQS/Kafka | Poison pill messages, deserialization errors, downstream failures |
| `OFFSET_OUT_OF_RANGE` | Kafka | Consumer offset reset to deleted segment |

---

## Issue 1: Kafka Consumer Lag Growing

**Symptoms:**
- Consumer group lag increasing in monitoring
- Messages queuing up in topic but not being processed
- CloudWatch `EstimatedNumberOfMessagesPending` rising
- `kafka.consumer:type=consumer-fetch-manager-metrics,records-lag-max` high

**Diagnosis:**
```bash
# Check consumer group lag
kubectl exec -it <kafka-client-pod> -- \
  kafka-consumer-groups.sh \
  --bootstrap-server kafka.inframind.internal:9092 \
  --describe --group <consumer-group>
# Look at LAG column — non-zero means behind, growing means falling further behind

# Check consumer group status
kubectl exec -it <kafka-client-pod> -- \
  kafka-consumer-groups.sh \
  --bootstrap-server kafka.inframind.internal:9092 \
  --describe --group <consumer-group> | grep -E "STABLE|EMPTY|DEAD"
# DEAD = all consumers disconnected
# EMPTY = group exists but no consumers
# STABLE = consumers connected

# Check consumer pod health
kubectl get pods -l app=<consumer-app> -n <namespace>
kubectl logs -l app=<consumer-app> --tail=50

# Check partition count vs consumer count
# Partitions: N, Consumers: M → if M < N, some partitions unassigned
```

**Root Cause Analysis:**
- Consumer pod crashed — lag accumulates until pod restarts
- Rebalancing storm — consumers joining/leaving faster than group stabilizes
- Consumer processing too slow — processing time > poll interval causing timeout and rebalance
- Downstream service (DB, API) slow — consumer blocked waiting for it
- Topic partition count increased but consumer group not rebalanced

**Immediate Fix:**
```bash
# Scale up consumers (max = number of partitions)
kubectl scale deployment <consumer> --replicas=<partition-count>

# If consumers are stuck in rebalancing, restart them
kubectl rollout restart deployment/<consumer>

# Check if downstream is the bottleneck
kubectl top pods -l app=<consumer-app>

# Temporarily increase visibility timeout if messages are being requeued
aws sqs set-queue-attributes \
  --queue-url <queue-url> \
  --attributes VisibilityTimeout=300
```

---

## Issue 2: SQS Queue Depth Growing / Messages Not Consumed

**Symptoms:**
- SQS `ApproximateNumberOfMessagesVisible` rising
- Consumer pods running but not draining the queue
- Processing rate lower than produce rate

**Diagnosis:**
```bash
# Check queue metrics
aws sqs get-queue-attributes \
  --queue-url <queue-url> \
  --attribute-names ApproximateNumberOfMessagesVisible \
                    ApproximateNumberOfMessagesNotVisible \
                    ApproximateNumberOfMessagesDelayed

# ApproximateNumberOfMessagesNotVisible = in-flight (being processed)
# If NotVisible is high = consumers picking up but not completing

# Check consumer pod status and logs
kubectl get pods -l app=<consumer> -n <namespace>
kubectl logs <consumer-pod> --tail=100 | grep -i "error\|timeout\|fail"

# Check if SQS endpoint is reachable from pod
kubectl exec -it <consumer-pod> -- \
  curl -s https://sqs.ap-south-1.amazonaws.com/ | head -5
```

**Root Cause Analysis:**
- Consumer crashing after receiving message — message returns to queue after visibility timeout
- Visibility timeout shorter than actual processing time — same message processed multiple times
- Consumer IAM role missing `sqs:ReceiveMessage` or `sqs:DeleteMessage` permissions
- Downstream dependency (DB, API) down — consumer receives messages but can't complete them

**Immediate Fix:**
```bash
# Increase visibility timeout to give consumers more time
aws sqs set-queue-attributes \
  --queue-url <queue-url> \
  --attributes VisibilityTimeout=300   # 5 minutes

# Scale consumers
kubectl scale deployment <consumer> --replicas=5

# Check if messages are in DLQ (processing is failing, not just slow)
aws sqs get-queue-attributes \
  --queue-url <dlq-url> \
  --attribute-names ApproximateNumberOfMessagesVisible
```

---

## Issue 3: Dead Letter Queue Filling Up

**Symptoms:**
- DLQ depth increasing
- Alerts for `DLQ messages > threshold`
- Application errors referencing message deserialization or processing failures

**Diagnosis:**
```bash
# Inspect a DLQ message to understand the failure pattern
aws sqs receive-message \
  --queue-url <dlq-url> \
  --max-number-of-messages 1 \
  --attribute-names All \
  --message-attribute-names All
# Read the message body — is it malformed JSON? Wrong schema? Valid but processing failed?

# Check consumer logs around the time DLQ messages arrived
kubectl logs <consumer-pod> --since=1h | grep -i "error\|dlq\|dead"

# Check how many receive attempts before DLQ (maxReceiveCount)
aws sqs get-queue-attributes \
  --queue-url <source-queue-url> \
  --attribute-names RedrivePolicy
```

**Root Cause Analysis:**
- Poison pill message — malformed/unexpected payload that always causes processing error
- Schema change — producer sending new format, consumer not updated to handle it
- Downstream service consistently failing — valid messages can't complete processing
- Bug introduced in consumer — specific message type triggers unhandled exception

**Immediate Fix:**
```bash
# If downstream service is down — pause consumption until it recovers
kubectl scale deployment <consumer> --replicas=0

# Once fixed, redrive DLQ messages back to source queue
aws sqs start-message-move-task \
  --source-arn <dlq-arn> \
  --destination-arn <source-queue-arn>

# For Kafka — skip the bad offset if it's a poison pill
kubectl exec -it <kafka-client-pod> -- \
  kafka-consumer-groups.sh \
  --bootstrap-server kafka.inframind.internal:9092 \
  --group <consumer-group> \
  --topic <topic>:<partition>:<bad-offset+1> \
  --reset-offsets --execute
```

---

## Issue 4: Kafka Broker Down / LeaderNotAvailableException

**Symptoms:**
- `LeaderNotAvailableException` or `NotLeaderForPartitionException` in logs
- Producers failing to write to specific partitions
- MSK broker showing unhealthy in AWS console

**Diagnosis:**
```bash
# Check broker health via MSK
aws kafka describe-cluster \
  --cluster-arn <msk-cluster-arn> \
  --query 'ClusterInfo.BrokerNodeGroupInfo'

# Check topic partition leaders
kubectl exec -it <kafka-client-pod> -- \
  kafka-topics.sh \
  --bootstrap-server kafka.inframind.internal:9092 \
  --describe --topic <topic-name>
# Leader=-1 means no leader elected — partition is unavailable

# Check under-replicated partitions
kubectl exec -it <kafka-client-pod> -- \
  kafka-topics.sh \
  --bootstrap-server kafka.inframind.internal:9092 \
  --describe --under-replicated-partitions
```

**Root Cause Analysis:**
- Broker node failed or restarting — leader election taking place (typically resolves in 30-60s)
- Network partition between brokers — split brain scenario
- MSK broker storage full — broker stops accepting writes

**Immediate Fix:**
```bash
# Wait 60 seconds — leader election usually resolves automatically

# Force metadata refresh in consumer/producer (client-side)
# Add to retry logic: catch LeaderNotAvailableException → sleep 5s → retry

# If MSK broker storage full
aws kafka update-broker-storage \
  --cluster-arn <msk-cluster-arn> \
  --current-version <cluster-version> \
  --target-broker-ebs-volume-info BrokerIds=1,2,3,VolumeSizeGB=1000

# Check MSK CloudWatch for disk usage
aws cloudwatch get-metric-statistics \
  --namespace AWS/Kafka \
  --metric-name KafkaDataLogsDiskUsed \
  --dimensions Name=Cluster\ Name,Value=inframind-kafka \
  --start-time $(date -u -d '1 hour ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 300 --statistics Maximum
```

---

## Issue 5: OFFSET_OUT_OF_RANGE — Consumer Offset Reset

**Symptoms:**
- `OffsetOutOfRangeException` in consumer logs
- Consumer cannot read from its saved offset
- This happens after log retention period passes or topic is compacted

**Diagnosis:**
```bash
# Check topic retention settings
kubectl exec -it <kafka-client-pod> -- \
  kafka-configs.sh \
  --bootstrap-server kafka.inframind.internal:9092 \
  --describe --entity-type topics --entity-name <topic>
# retention.ms and retention.bytes

# Check earliest available offset for the topic
kubectl exec -it <kafka-client-pod> -- \
  kafka-run-class.sh kafka.tools.GetOffsetShell \
  --broker-list kafka.inframind.internal:9092 \
  --topic <topic> --time -2   # -2 = earliest
```

**Root Cause Analysis:**
- Consumer was offline longer than topic retention period — saved offset no longer exists
- Topic was recreated or compacted — offsets reset
- `log.retention.hours` is shorter than consumer downtime

**Immediate Fix:**
```bash
# Reset consumer group offset to earliest available
kubectl exec -it <kafka-client-pod> -- \
  kafka-consumer-groups.sh \
  --bootstrap-server kafka.inframind.internal:9092 \
  --group <consumer-group> \
  --topic <topic> \
  --reset-offsets --to-earliest --execute

# Or reset to latest (skip all missed messages — use when backlog is acceptable to drop)
kubectl exec -it <kafka-client-pod> -- \
  kafka-consumer-groups.sh \
  --bootstrap-server kafka.inframind.internal:9092 \
  --group <consumer-group> \
  --topic <topic> \
  --reset-offsets --to-latest --execute
```

---

## Queue Health Monitoring
```bash
# SQS queue depth across all queues
aws sqs list-queues --queue-name-prefix inframind | \
  xargs -I{} aws sqs get-queue-attributes \
  --queue-url {} \
  --attribute-names ApproximateNumberOfMessagesVisible

# Kafka consumer lag (all groups)
kubectl exec -it <kafka-client-pod> -- \
  kafka-consumer-groups.sh \
  --bootstrap-server kafka.inframind.internal:9092 \
  --list | xargs -I{} kafka-consumer-groups.sh \
  --bootstrap-server kafka.inframind.internal:9092 \
  --describe --group {}

# MSK broker health
aws kafka list-nodes --cluster-arn <msk-cluster-arn>
```

---

## Escalation Contacts
- **Infrastructure Team:** infra-oncall@company.com
- **Backend Engineering:** backend-team@company.com
- **Data Engineering:** data-team@company.com
- **Severity 1 Incidents:** +1-555-0123
