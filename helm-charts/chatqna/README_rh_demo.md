# Red Hat Demo Deployment Guide

## Deployment Values Matrix

| Compute | Vector DB | Model |
|---------|-----------|-------|
| **Xeon** | rh-milvus.yaml<br>rh-qdrant.yaml<br>values.yaml (redis default) | rh-xeon-granite.yaml<br>rh-xeon-qwen.yaml<br>rh-xeon-llama.yaml<br>rh-xeon-mistral.yaml |
| **Gaudi** | rh-milvus.yaml<br>rh-qdrant.yaml<br>values.yaml (redis default) | rh-gaudi-granite.yaml<br>rh-gaudi-qwen.yaml<br>rh-gaudi-llama.yaml<br>rh-gaudi-mistral.yaml |

## Example

```bash
export HFTOKEN=<huggingface token>
helm install <name> -f rh-milvus.yaml -f rh-xeon-xxx1.yaml --set global.HUGGINGFACEHUB_API_TOKEN=$HFTOKEN --set global.HF_TOKEN=$HFTOKEN
```

## Setup Notes

1. Above was tested with privileged service account:
   - `oc create serviceaccount chatqna-sa`
   - `oc adm policy add-scc-to-user privileged -z <user> -n <namespace>`

2. Added docker hub secret 'regcred', since images from docker hub can have rate limits:
   - `kubectl create secret docker-registry regcred --docker-username=<your-name> --docker-password=<your-pword> --docker-email=<your-email> -n <your-namespace>`

3. Set up model cars in OpenShift AI (redhat-ai-services/modelcar-catalog).
   - Gaudi – make sure to set: kserve time out, and gpu- model-max-len.
   - Xeon – cpu kv cache.

4. Set up the persistent nfspvc.yaml e.g., storageClassName: "nfs-engg". See the README.md