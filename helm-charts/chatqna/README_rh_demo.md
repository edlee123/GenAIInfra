# Red Hat Demo Deployment Guide

## Deployment Values Matrix

| Compute | Vector DB | Model |
|---------|-----------|-------|
| **Xeon** | rh-milvus.yaml<br>rh-qdrant.yaml<br>values.yaml (redis default) | rh-xeon-granite.yaml<br>rh-xeon-qwen.yaml<br>rh-xeon-llama.yaml<br>rh-xeon-deepseek.yaml |
| **Gaudi** | rh-milvus.yaml<br>rh-qdrant.yaml<br>values.yaml (redis default) | rh-gaudi-granite.yaml<br>rh-gaudi-qwen.yaml<br>rh-gaudi-llama.yaml<br>rh-gaudi-mistral.yaml |

## Example

```bash
export HFTOKEN=<huggingface token>
helm install <release-name> -f rh-milvus.yaml -f rh-xeon-xxx1.yaml --set global.HUGGINGFACEHUB_API_TOKEN=$HFTOKEN --set global.HF_TOKEN=$HFTOKEN -n <namespace>
```

**Notes:**
- `<release-name>` is a name you choose for this specific deployment (e.g., "chatqna-demo", "llama-milvus"). Helm uses this name to track the installation and manage updates/rollbacks.
- It's important to specify the namespace (`-n <namespace>`) where you want to deploy the application. This should be the same namespace where you've set up your service account and Docker registry secret.

## Deployment Process

1. Choose your vector database configuration:
   - `rh-milvus.yaml` - For Milvus vector database
   - `rh-qdrant.yaml` - For Qdrant vector database
   - `values.yaml` - For Redis vector database (default)

2. Choose your compute platform and model:
   - For Xeon: `rh-xeon-llama.yaml`, `rh-xeon-qwen.yaml`, etc.
   - For Gaudi: `rh-gaudi-llama.yaml`, `rh-gaudi-qwen.yaml`, etc.

3. Deploy with Helm:
   ```bash
   # Example for Milvus + Xeon + Llama
   helm install chatqna-milvus-llama -f rh-milvus.yaml -f rh-xeon-llama.yaml --set global.HUGGINGFACEHUB_API_TOKEN=$HFTOKEN --set global.HF_TOKEN=$HFTOKEN -n chatqna-demo
   
   # Example for Qdrant + Xeon + Qwen
   helm install chatqna-qdrant-qwen -f rh-qdrant.yaml -f rh-xeon-qwen.yaml --set global.HUGGINGFACEHUB_API_TOKEN=$HFTOKEN --set global.HF_TOKEN=$HFTOKEN -n chatqna-demo
   
   # Example for Redis (default) + Gaudi + Mistral
   helm install chatqna-redis-mistral -f rh-gaudi-mistral.yaml --set global.HUGGINGFACEHUB_API_TOKEN=$HFTOKEN --set global.HF_TOKEN=$HFTOKEN -n chatqna-demo
   ```

4. Check deployment status:
   ```bash
   helm list -n chatqna-demo
   kubectl get pods -n chatqna-demo
   ```

5. To uninstall:
   ```bash
   helm uninstall <release-name> -n <namespace>
   ```

## Setup Notes

1. Above was tested with privileged service account:
   - `oc create serviceaccount chatqna-sa`
   - `oc adm policy add-scc-to-user privileged -z <serviceaccount> -n <namespace>`

2. Added docker hub secret 'regcred', since images from docker hub can have rate limits:
   - `kubectl create secret docker-registry regcred --docker-username=<your-name> --docker-password=<your-pword> --docker-email=<your-email> -n <your-namespace>`

3. Set up model cars in OpenShift AI (redhat-ai-services/modelcar-catalog).
   - Gaudi – make sure to set: kserve time out, and gpu-memory-utilization, model-max-len.
   - Xeon – consider setting VLLM_CPU_KVCACHE_SPACE for long context models like granite.

4. Set up the persistent nfspvc.yaml e.g., storageClassName: "nfs-engg". See the [README.md](https://github.com/edlee123/GenAIInfra/blob/redhat_demo/helm-charts/README.md?plain=1#L150)