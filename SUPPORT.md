# Support

Use GitHub Discussions for installation help, configuration questions, and ideas. Use GitHub Issues for reproducible bugs and scoped feature requests.

Include these diagnostics after removing tokens, credentials, repository names, public IPs, and other sensitive data:

```bash
helm status devboxes -n devboxes
kubectl version
kubectl get deployment,service,pvc,pod -n devboxes -o wide
kubectl logs -n devboxes deployment/devboxes --tail=200
kubectl get events -n devboxes --sort-by=.lastTimestamp
devbox --version
devbox gpu profiles
```

For SSH problems, state whether the installation uses `LoadBalancer` or `NodePort`, whether the address is reachable from the CLI machine, and the exact OpenSSH error. For storage problems, include the StorageClass name, access mode, expansion support, and PVC events.

For GPU problems, include the selected profile, redacted `devbox status` output, the pod's scheduling events, Kubernetes RuntimeClass presence, and the exact extended resource shown in node allocatable capacity. State whether the vendor plugin uses dedicated, partitioned, or shared devices. Do not include private registry credentials, image pull Secrets, public node addresses, or proprietary workload output.

Security issues must follow [SECURITY.md](SECURITY.md) and must not be posted publicly.
