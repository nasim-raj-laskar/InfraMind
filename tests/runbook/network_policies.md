# Network Policies Runbook

## Overview
This runbook covers network-related incidents, connectivity issues, and security policy troubleshooting for InfraMind infrastructure.

## Common Network Issues

### Pod-to-Pod Communication Failures
**Symptoms:**
- `connection refused` errors between services
- Intermittent connectivity issues
- DNS resolution failures

**Immediate Actions:**
1. Check network policies: `kubectl get networkpolicy -A`
2. Verify pod connectivity: `kubectl exec -it pod -- nc -zv target-service 80`
3. Test DNS resolution: `kubectl exec -it pod -- nslookup service-name`

**Root Cause Analysis:**
- Restrictive network policies blocking traffic
- DNS configuration issues
- Service mesh configuration problems

### Ingress Controller Issues
**Symptoms:**
- External traffic not reaching services
- 502/503 gateway errors
- SSL/TLS certificate problems

**Immediate Actions:**
1. Check ingress status: `kubectl get ingress -A`
2. Verify controller logs: `kubectl logs -n ingress-nginx deployment/ingress-nginx-controller`
3. Test backend connectivity: `kubectl port-forward service/app 8080:80`

### Load Balancer Problems
**Symptoms:**
- Uneven traffic distribution
- Health check failures
- Backend service unavailable

**Immediate Actions:**
1. Check service endpoints: `kubectl get endpoints service-name`
2. Verify health checks: `kubectl describe service service-name`
3. Review load balancer logs in cloud provider console

## Network Policy Debugging

### Allow All Traffic (Emergency)
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-all-emergency
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - {}
  egress:
  - {}
```

### Test Connectivity
```bash
# Test pod-to-pod connectivity
kubectl run test-pod --image=busybox --rm -it -- sh
nc -zv target-service 80

# Check service discovery
kubectl exec -it pod -- nslookup kubernetes.default.svc.cluster.local

# Trace network path
kubectl exec -it pod -- traceroute target-service
```

## Security Policy Management

### Common Network Policies
```yaml
# Deny all ingress traffic
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all-ingress
spec:
  podSelector: {}
  policyTypes:
  - Ingress

# Allow specific service communication
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-frontend-to-backend
spec:
  podSelector:
    matchLabels:
      app: backend
  policyTypes:
  - Ingress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: frontend
    ports:
    - protocol: TCP
      port: 8080
```

## Troubleshooting Commands

### Network Diagnostics
```bash
# Check cluster networking
kubectl get nodes -o wide
kubectl get pods -o wide --all-namespaces

# Verify CNI plugin status
kubectl get daemonset -n kube-system

# Check network policies
kubectl describe networkpolicy policy-name

# Test service connectivity
kubectl run debug --image=nicolaka/netshoot --rm -it -- bash
```

### DNS Troubleshooting
```bash
# Check CoreDNS status
kubectl get pods -n kube-system -l k8s-app=kube-dns

# Test DNS resolution
kubectl exec -it pod -- dig kubernetes.default.svc.cluster.local

# Check DNS configuration
kubectl get configmap coredns -n kube-system -o yaml
```

## Recovery Procedures

### Network Policy Rollback
```bash
# List recent changes
kubectl get events --sort-by=.metadata.creationTimestamp

# Remove problematic policy
kubectl delete networkpolicy problematic-policy

# Apply emergency allow-all policy
kubectl apply -f emergency-allow-all.yaml
```

### Service Mesh Reset
```bash
# Restart Istio components
kubectl rollout restart deployment/istiod -n istio-system

# Recreate service mesh sidecars
kubectl delete pods -l app=your-app
```

## Monitoring and Alerts

### Key Metrics to Monitor
- Network policy violations
- DNS query failures
- Service endpoint availability
- Ingress controller error rates

### Alert Thresholds
- DNS failure rate > 5%
- Service connectivity < 99%
- Network policy denials > 100/min

## Escalation Contacts
- **Network Team:** network-team@company.com
- **Security Team:** security-oncall@company.com
- **Platform Engineering:** platform-team@company.com