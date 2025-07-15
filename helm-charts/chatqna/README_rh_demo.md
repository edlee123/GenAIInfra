# Red Hat Demo Deployment Guide


## Setup Notes

1. Above was tested with privileged service account:
   - `oc create serviceaccount <serviceaccount>`
   - `oc adm policy add-scc-to-user privileged -z <serviceaccount> -n <namespace>`

2. Added docker hub secret 'regcred', since images from docker hub can have rate limits:
   - `kubectl create secret docker-registry regcred --docker-username=<your-name> --docker-password=<your-pword> --docker-email=<your-email> -n <your-namespace>`

3. Set up model cars in OpenShift AI (redhat-ai-services/modelcar-catalog).
   - Gaudi – make sure to set: kserve time out, and gpu-memory-utilization, model-max-len.
   - Xeon – consider setting VLLM_CPU_KVCACHE_SPACE for long context models like granite.

4. Set up the persistent nfspvc.yaml e.g., storageClassName: "nfs-engg". See the [README.md](https://github.com/edlee123/GenAIInfra/blob/redhat_demo/helm-charts/README.md?plain=1#L150)

```bash
cat << EOF | kubectl apply -n ed-chatqna -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: model-volume
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: "lvms-vg1"
  resources:
    requests:
      storage: 100Gi
EOF
```

5. Update dependency: cd GenAIInfra/helm-charts && chmod +x update_dependency.sh && ./update_dependency.sh  && helm dependency update chatqna



## Deployment Values Matrix

| Compute | Vector DB | Model |
|---------|-----------|-------|
| **Xeon** | rh-milvus-values.yaml<br>rh-qdrant-values.yaml<br>values.yaml (redis default) | rh-xeon-granite-values.yaml<br>rh-xeon-qwen-values.yaml<br>rh-xeon-llama-values.yaml<br>rh-xeon-deepseek-values.yaml |
| **Gaudi** | rh-milvus-values.yaml<br>rh-qdrant-values.yaml<br>values.yaml (redis default) | rh-gaudi-granite-values.yaml<br>rh-gaudi-qwen-values.yaml<br>rh-gaudi-llama-values.yaml<br>rh-gaudi-mistral-values.yaml |

## Deployment Process

1. Choose your vector database configuration:
   - `rh-milvus-values.yaml` - For Milvus vector database
   - `rh-qdrant-values.yaml` - For Qdrant vector database
   - `values.yaml` - For Redis vector database (default)

2. Choose your compute platform and model:
   - For Xeon: `rh-xeon-llama-values.yaml`, `rh-xeon-qwen-values.yaml`, etc.
   - For Gaudi: `rh-gaudi-llama-values.yaml`, `rh-gaudi-qwen-values.yaml`, etc.

3. Deploy with Helm:

   First, set your Hugging Face token:
   ```bash
   export HFTOKEN=<huggingface token>
   ```

   Example deployments:

   a) Using Qdrant vector DB with Xeon and SMOL model: 
   ```bash
   cd GenAIInfra/helm-charts/chatqna
   helm upgrade <release-name> . -f rh-qdrant-values.yaml -f rh-xeon-smol-values.yaml \
     --set global.HUGGINGFACEHUB_API_TOKEN=$HFTOKEN,global.HF_TOKEN=$HFTOKEN -n <namespace>
   ```

   b) Using Redis Vector DB (default) with Xeon and SMOL model:
   ```bash
   cd GenAIInfra/helm-charts/chatqna
   helm upgrade <release-name> . -f rh-xeon-smol-values.yaml \
     --set global.HUGGINGFACEHUB_API_TOKEN=$HFTOKEN,global.HF_TOKEN=$HFTOKEN -n <namespace>
   ```

   c) Using Milvus with Xeon and any other model:
   ```bash
   cd GenAIInfra/helm-charts/chatqna
   helm upgrade <release-name> . -f rh-milvus-values.yaml -f rh-xeon-<model>-values.yaml \
     --set global.HUGGINGFACEHUB_API_TOKEN=$HFTOKEN,global.HF_TOKEN=$HFTOKEN -n <namespace>
   ```
   
   **Notes:**
   - `<release-name>` is a name you choose for this specific deployment (e.g., "chatqna-demo", "llama-milvus")
   - Always specify the namespace (`-n <namespace>`) where you've set up your service account and Docker registry secret

4. Check deployment status:
   ```bash
   helm list -n <namespace>
   kubectl get pods -n <namespace>
   ```

5. To uninstall:
   ```bash
   helm uninstall <release-name> -n <namespace>
   ```