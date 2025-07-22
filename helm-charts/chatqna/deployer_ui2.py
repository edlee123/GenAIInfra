import gradio as gr
import subprocess
import os
import yaml
from pathlib import Path
import time
import threading
import webbrowser
import signal
import psutil

# Configuration based on README_rh_demo.md
COMPUTE_OPTIONS = ["Xeon", "Gaudi"]

MODEL_OPTIONS = {
    "Xeon": ["smol", "qwen", "granite", "deepseek"],
    "Gaudi": ["granite", "qwen", "llama"]
}

# Xeon smol = 
# Xeon qwen = 
# Xeon granite =
# Xeon deepseek = 

# Gaudi granite =
# Gaudi qwen =
# Gaudi llama = 
# Gaudi mistral =

DB_OPTIONS = ["milvus", "qdrant", "redis"]

# Get the directory where this script is located
HELM_CHART_PATH = Path(os.path.dirname(os.path.abspath(__file__)))

def get_value_file_path(compute, model):
    """Get the path to the values file based on compute and model."""
    return HELM_CHART_PATH / f"rh-{compute.lower()}-{model}-values.yaml"

def get_db_value_file_path(db):
    """Get the path to the database values file."""
    # For Redis, return None as it's the default and doesn't need a separate values file
    if db == "redis":
        return None
    
    # For other databases, check if the values file exists
    db_value_file = HELM_CHART_PATH / f"rh-{db}-values.yaml"
    if os.path.exists(db_value_file):
        return db_value_file
    
    # If file doesn't exist, log warning and return None
    print(f"Warning: DB values file for {db} not found at {db_value_file}")
    return None

def update_model_choices(compute):
    """Update model choices based on selected compute platform."""
    return gr.Dropdown(choices=MODEL_OPTIONS[compute])

def get_port_forwards():
    """Get a list of all kubectl port-forward processes."""
    try:
        cmd = "ps aux | grep 'kubectl.*port-forward' | grep -v grep"
        output = subprocess.check_output(cmd, shell=True, text=True)
        forwards = []
        for line in output.splitlines():
            parts = line.split()
            pid = parts[1]
            # Extract namespace and port info from the command
            cmd_parts = line[line.find('kubectl'):]
            forwards.append({
                'pid': pid,
                'command': cmd_parts,
                'full': line
            })
        return forwards
    except subprocess.CalledProcessError:
        return []

def cleanup_port_forwards(port=8080):
    """Clean up any existing port-forwards to the specified port."""
    try:
        # First try lsof for the specific port
        cmd = f"lsof -i :{port} | grep kubectl"
        try:
            print(f"Looking for processes using port {port}...")
            output = subprocess.check_output(cmd, shell=True, text=True)
            if output:
                print(f"Found processes using port {port}:")
                print(output)
                # Extract PIDs and kill them
                for line in output.splitlines():
                    if 'kubectl' in line:
                        pid = int(line.split()[1])
                        print(f"Terminating kubectl process {pid}")
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(1)  # Give it a moment to clean up
                print(f"Cleaned up all processes on port {port}")
            else:
                print(f"No processes found using port {port}")
        except subprocess.CalledProcessError:
            print(f"No processes found using port {port} (lsof returned non-zero)")
            pass
    except Exception as e:
        print(f"Warning: Error cleaning up port forwards: {str(e)}")

def verify_port_available(port=8080):
    """Verify if the port is available."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('localhost', port))
        sock.close()
        print(f"Port {port} is available")
        return True
    except socket.error:
        print(f"Port {port} is still in use")
        return False

def start_port_forward(namespace, release_name=None, port=8080):
    """
    Start port forwarding to the deployed application.
    
    Args:
        namespace: The Kubernetes namespace with the deployment
        release_name: The Helm release name (default to None, which will use "chatqna")
        port: The local port to forward to
        
    Returns:
        tuple: (success, message, forward_process)
    """
    try:
        # Clean up existing port forwards
        cleanup_port_forwards(port)
        
        # Verify port is available
        if not verify_port_available(port):
            return False, f"Port {port} is already in use by another process", None
        
        # Use provided release_name or default to "chatqna"
        release = release_name if release_name else "chatqna"
        
        # Check for the frontend service first
        frontend_svc = f"{release}-frontend"
        print(f"Looking for service {frontend_svc} in namespace {namespace}...")
        frontend_check = subprocess.run(
            ["kubectl", "get", "svc", frontend_svc, "-n", namespace],
            capture_output=True, text=True
        )
        
        target_type = None
        target_name = None
        target_port = 80  # Default target port
        
        if frontend_check.returncode == 0:
            print(f"Found frontend service: {frontend_svc}")
            target_type = "service"
            target_name = frontend_svc
        else:
            # Look for any service with common web service names or ports
            print("Looking for alternative services...")
            services = subprocess.run(
                ["kubectl", "get", "services", "-n", namespace, "-o", "json"],
                capture_output=True, text=True
            )
            
            if services.returncode == 0:
                import json
                try:
                    services_data = json.loads(services.stdout)
                    for service in services_data.get("items", []):
                        service_name = service["metadata"]["name"]
                        # Look for service with port 80 or with names that suggest web service
                        for port_spec in service.get("spec", {}).get("ports", []):
                            if port_spec.get("port") == 80 or port_spec.get("targetPort") == 80:
                                print(f"Found HTTP service: {service_name}")
                                target_type = "service"
                                target_name = service_name
                                target_port = port_spec.get("port", 80)
                                break
                        
                        # Check if name suggests web/http service
                        if not target_type and any(x in service_name.lower() for x in ["http", "web", "nginx", "ui", "frontend", "gateway"]):
                            print(f"Found potential HTTP service by name: {service_name}")
                            target_type = "service"
                            target_name = service_name
                            # Get first port
                            if service.get("spec", {}).get("ports", []):
                                target_port = service["spec"]["ports"][0].get("port", 80)
                                break
                except json.JSONDecodeError:
                    print("Error parsing services JSON")
            
            # If we still don't have a service target, try to find nginx pod
            if not target_type:
                print("Looking for nginx pod...")
                pod_cmd = ["kubectl", "get", "pods", "-n", namespace, "-l", "app.kubernetes.io/name=nginx", 
                           "--field-selector", "status.phase=Running", "--no-headers", 
                           "-o", "custom-columns=:metadata.name"]
                pod_result = subprocess.run(pod_cmd, capture_output=True, text=True)
                
                if pod_result.returncode == 0 and pod_result.stdout.strip():
                    pod_name = pod_result.stdout.strip()
                    print(f"Found nginx pod: {pod_name}")
                    target_type = "pod"
                    target_name = pod_name
        
        # If we still don't have a target, fail
        if not target_type or not target_name:
            print("No suitable port-forward target found. Checking all pods in namespace:")
            all_pods_cmd = ["kubectl", "get", "pods,services", "-n", namespace]
            all_pods = subprocess.run(all_pods_cmd, capture_output=True, text=True)
            print(all_pods.stdout)
            return False, "No suitable port-forward target found", None
        
        # Build port forward command based on target type
        if target_type == "pod":
            port_forward_cmd = [
                "kubectl", "port-forward",
                f"pod/{target_name}",
                f"{port}:{target_port}",  # Map container port to local port
                "-n", namespace
            ]
        else:  # service
            port_forward_cmd = [
                "kubectl", "port-forward",
                f"service/{target_name}",
                f"{port}:{target_port}",
                "-n", namespace
            ]
        
        print(f"Starting port-forward to {target_type}/{target_name}: {port}:{target_port}")
        process = subprocess.Popen(
            port_forward_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait for port-forward to establish
        time.sleep(3)
        print("Checking port forward status...")
        
        if process.poll() is None:  # Process is running
            # Verify port is no longer available (meaning it's in use by port-forward)
            if verify_port_available(port):
                print(f"Warning: Port {port} is not in use by port-forward")
                return False, f"Port-forward process started but port {port} is not in use", None
            print("Port forward successfully established")
            return True, f"Port forwarding started to {target_type}/{target_name}", process
        else:
            stderr = process.stderr.read().decode()
            stdout = process.stdout.read().decode()
            print(f"Port forward failed with error: {stderr}")
            print(f"Port forward output: {stdout}")
            return False, f"Port-forward failed: {stderr}", None
    except Exception as e:
        return False, f"Error setting up port-forward: {str(e)}", None

def validate_deployment_config(namespace, compute, model, db, release_name=None):
    """
    Validate the deployment configuration and check prerequisites.
    
    Args:
        namespace: The Kubernetes namespace to deploy to
        compute: The compute platform (Xeon/Gaudi)
        model: The model to deploy
        db: The vector database to use
        release_name: The Helm release name (optional)
        
    Returns:
        tuple: (is_valid, message)
    """
    results = []
    is_valid = True
    
    # Check if namespace is specified
    if not namespace:
        is_valid = False
        results.append("Error: Namespace must be specified")
        
    # Check if release_name is specified
    if release_name is None or not release_name.strip():
        results.append("Warning: Release name not specified, will default to 'chatqna'")
    
    # Check for invalid characters in namespace
    if namespace and not all(c.isalnum() or c == '-' for c in namespace):
        is_valid = False
        results.append("Error: Namespace must contain only alphanumeric characters or hyphens")
    
    # Check if model is compatible with compute platform
    if model not in MODEL_OPTIONS.get(compute, []):
        is_valid = False
        results.append(f"Error: Model '{model}' is not compatible with '{compute}' compute platform")
    
    # Check if value files exist
    value_file_path = get_value_file_path(compute, model)
    if not os.path.exists(value_file_path):
        is_valid = False
        results.append(f"Error: Values file not found at {value_file_path}")
    
    # For database values files
    db_value_file_path = get_db_value_file_path(db)
    if db_value_file_path is not None and not os.path.exists(db_value_file_path):
        is_valid = False
        results.append(f"Error: DB values file not found at {db_value_file_path}")
    
    # Check kubectl connection to cluster
    try:
        result = subprocess.run(
            ["kubectl", "cluster-info"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            is_valid = False
            results.append("Error: Cannot connect to Kubernetes cluster. Please check your kubeconfig.")
    except Exception as e:
        is_valid = False
        results.append(f"Error: Failed to run kubectl: {str(e)}")
    
    # Check if namespace already exists
    if namespace:
        try:
            result = subprocess.run(
                ["kubectl", "get", "namespace", namespace],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                # Namespace exists, check if Helm release exists
                helm_result = subprocess.run(
                    ["helm", "list", "-n", namespace],
                    capture_output=True, text=True
                )
                # Check for existing releases in the namespace
                if helm_result.stdout.strip():
                    release_to_check = release_name if release_name else "chatqna"
                    if release_to_check in helm_result.stdout:
                        results.append(f"Warning: Helm release '{release_to_check}' already exists in namespace {namespace}. Deployment will upgrade existing release.")
        except Exception:
            # Namespace doesn't exist, we'll create it
            pass
    
    return is_valid, "\n".join(results) if results else "Configuration is valid"

def execute_helm_deployment(namespace, value_file_path, db_value_file_path, hf_token, release_name=None, progress=None):
    """
    Execute the Helm deployment using the specified configuration.
    
    Args:
        namespace: The Kubernetes namespace to deploy to
        value_file_path: Path to the model values file
        db_value_file_path: Path to the database values file
        hf_token: Hugging Face API token
        release_name: The name for the Helm release (defaults to "chatqna" if not provided)
        progress: Progress indicator for UI
        
    Returns:
        tuple: (success, message, output)
    """
    try:
        # Use provided release_name or default to "chatqna"
        release = release_name if release_name else "chatqna"
        
        # Create namespace if it doesn't exist
        result = subprocess.run(
            ["kubectl", "get", "namespace", namespace],
            capture_output=True, text=True
        )
        
        if result.returncode != 0:
            print(f"Creating namespace {namespace}...")
            create_result = subprocess.run(
                ["kubectl", "create", "namespace", namespace],
                capture_output=True, text=True
            )
            
            if create_result.returncode != 0:
                return False, f"Failed to create namespace: {create_result.stderr}", create_result.stderr
        
        # Execute Helm upgrade/install command
        print(f"Deploying {release} to namespace {namespace}")
        print(f"- Model values file: {value_file_path}")
        print(f"- DB values file: {db_value_file_path}")
        
        # Start with base command
        cmd = [
            "helm", "upgrade", "--install", release, ".",
            "--namespace", namespace,
        ]
        
        # For Redis, we don't need to specify any additional values file since it's the default
        if db != "redis" and db_value_file_path is not None and os.path.exists(db_value_file_path) and os.path.getsize(db_value_file_path) > 0:
            # Apply database values first (lower precedence)
            cmd.extend(["--values", str(db_value_file_path)])
        
        # Then apply the compute/model specific values (higher precedence)
        # This ensures model settings override any database settings
        cmd.extend(["--values", str(value_file_path)])
        
        # Add Hugging Face tokens
        cmd.extend([
            "--set", f"global.HUGGINGFACEHUB_API_TOKEN={hf_token}",
            "--set", f"global.HF_TOKEN={hf_token}"
        ])
        
        print(f"Executing command: {' '.join(cmd)}")
        helm_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=HELM_CHART_PATH
        )
        
        # Capture output with timeout
        try:
            stdout, stderr = helm_process.communicate(timeout=90)  # 90 seconds timeout
            
            if helm_process.returncode != 0:
                return False, f"Helm deployment failed: {stderr}", stderr
            
            return True, "Helm deployment initiated successfully", stdout
            
        except subprocess.TimeoutExpired:
            # If it's taking too long, assume it's working but continue
            helm_process.kill()
            return True, "Helm deployment initiated (timeout waiting for completion)", ""
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Error during deployment: {str(e)}\n{error_trace}")
        return False, f"Error during deployment: {str(e)}", error_trace

def wait_for_pods_ready(namespace, progress=None, progress_text=None, max_wait_time=900):
    """
    Wait for all pods in the specified namespace to be ready.
    
    Args:
        namespace: The Kubernetes namespace to check
        progress: The Gradio progress bar component
        progress_text: The Gradio text component for progress updates
        max_wait_time: Maximum time to wait in seconds (default 15 minutes)
        
    Returns:
        tuple: (success, message, pod_status_message)
    """
    start_time = time.time()
    last_status_update = ""
    current_progress = 0.0  # Track progress as a float between 0.0 and 1.0
    
    # Safely update progress
    def update_progress(value, message):
        nonlocal current_progress
        current_progress = value
        try:
            if progress is not None:
                progress(value, desc=message)
        except Exception as e:
            print(f"Warning: Error updating progress bar: {str(e)}")
    
    # Initialize progress
    update_progress(0.0, "Initializing deployment...")
    
    if progress_text:
        progress_text("Starting deployment and waiting for pods to become ready...")
    
    # Wait for pods to be created
    time.sleep(10)  # Initial wait for Kubernetes to start creating resources
    
    while True:
        if time.time() - start_time > max_wait_time:
            message = f"Timeout after {max_wait_time // 60} minutes waiting for pods to be ready."
            update_progress(0.8, "Deployment timed out")  # Show 80% as timeout
            if progress_text:
                progress_text(message + "\n\nSome pods may still be starting. Check pod status for details.")
            pod_status = get_pod_status_for_ui(namespace)
            return False, message, pod_status
        
        # Check if pods exist in the namespace
        cmd = ["kubectl", "get", "pods", "-n", namespace]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            # Namespace might not be created yet or there's an error
            message = f"Waiting for namespace and pods to be created..."
            update_progress(0.1, message)
            if progress_text:
                progress_text(message)
            time.sleep(5)
            continue
        
        if "No resources found" in result.stdout:
            message = "Waiting for pods to be created..."
            update_progress(0.2, message)
            if progress_text:
                progress_text(message)
            time.sleep(5)
            continue
        
        # Get pod status and check if all are running
        all_pods_ready = True
        total_pods = 0
        running_pods = 0
        pending_pods = []
        error_pods = []
        
        # Use jsonpath to get detailed pod status
        cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", 
               "jsonpath={range .items[*]}{.metadata.name},{.status.phase},{.status.containerStatuses[*].ready}{\"\\n\"}{end}"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            if progress_text:
                progress_text(f"Error getting pod status: {result.stderr}")
            time.sleep(5)
            continue
        
        # Parse pod status
        pod_lines = result.stdout.strip().split("\n")
        for line in pod_lines:
            if not line:
                continue
                
            parts = line.split(",")
            if len(parts) < 2:
                continue
                
            pod_name = parts[0]
            phase = parts[1]
            ready = False
            
            if len(parts) > 2:
                # Check if all containers are ready
                container_status = parts[2]
                ready = all(status.lower() == "true" for status in container_status.split())
            
            total_pods += 1
            
            if phase == "Running" and ready:
                running_pods += 1
            elif phase == "Pending":
                pending_pods.append(pod_name)
            elif phase in ["Failed", "Unknown", "CrashLoopBackOff"]:
                error_pods.append(pod_name)
        
        # Calculate completion percentage
        if total_pods > 0:
            completion_percent = running_pods / total_pods
            progress_value = 0.4 + (0.5 * completion_percent)  # Scale between 40% and 90%
        else:
            progress_value = 0.1  # At least pods exist
        
        # Update progress bar
        update_progress(progress_value, f"Pods ready: {running_pods}/{total_pods}")
        
        # Prepare status message
        status_message = f"Pods ready: {running_pods}/{total_pods}"
        if pending_pods:
            status_message += f"\nPending pods: {', '.join(pending_pods)}"
        if error_pods:
            status_message += f"\nProblem pods: {', '.join(error_pods)}"
        
        # Only update status text if it changed
        if status_message != last_status_update:
            if progress_text:
                progress_text(status_message)
            last_status_update = status_message
        
        # Check if all pods are running and ready
        all_pods_ready = running_pods == total_pods and total_pods > 0
        
        if all_pods_ready:
            # Get final pod status for UI
            pod_status = get_pod_status_for_ui(namespace)
            
            # Complete progress
            update_progress(1.0, "Deployment complete! All pods are running.")
            if progress_text:
                progress_text("Deployment complete! All pods are running.\n\n" + pod_status)
            
            return True, "All pods are running and ready!", pod_status
        
        # Wait before checking again
        time.sleep(5)

def deploy_helm(compute, db, model, namespace, release_name, progress=gr.Progress()):
    """Deploy using Helm with the selected configuration."""
    output_messages = []
    
    # Log deployment information
    output_messages.append(f"Starting deployment with:")
    output_messages.append(f"- Compute: {compute}")
    output_messages.append(f"- Model: {model}")
    output_messages.append(f"- Database: {db}")
    output_messages.append(f"- Namespace: {namespace}")
    
    # Progress tracking
    progress_text = lambda text: output_messages.append(text)
    
    # Define a safe progress update function
    def update_progress(value, message):
        try:
            if progress is not None:
                progress(value, desc=message)
        except Exception as e:
            print(f"Warning: Error updating progress bar: {str(e)}")
    
    # Step 1: Validate configuration
    update_progress(0.1, "Validating configuration...")
    is_valid, validation_message = validate_deployment_config(namespace, compute, model, db, release_name)
    
    if not is_valid:
        output_messages.append(f"Configuration validation failed:")
        output_messages.append(validation_message)
        return "\n".join(output_messages)
    
    output_messages.append(validation_message)
    
    # Step 2: Prepare value files
    update_progress(0.2, "Preparing deployment files...")
    value_file_path = get_value_file_path(compute, model)
    db_value_file_path = get_db_value_file_path(db)
    
    output_messages.append(f"Using values files:")
    output_messages.append(f"- Model: {value_file_path}")
    if db_value_file_path:
        output_messages.append(f"- Database: {db_value_file_path}")
    else:
        output_messages.append("- Database: Using default configuration (Redis)")
    
    # Step 3: Execute Helm deployment
    update_progress(0.3, "Deploying with Helm...")
    # Check for HF token in environment
    hf_token = os.environ.get("HFTOKEN") or os.environ.get("HUGGINGFACEHUB_API_TOKEN") or ""
    if not hf_token:
        output_messages.append("Warning: No Hugging Face token found in environment variables. Set HFTOKEN or HUGGINGFACEHUB_API_TOKEN.")
    
    deployment_success, deployment_message, deployment_output = execute_helm_deployment(
        namespace, value_file_path, db_value_file_path, hf_token, release_name, progress
    )
    
    if not deployment_success:
        output_messages.append(f"Deployment failed:")
        output_messages.append(deployment_message)
        if deployment_output:
            output_messages.append(deployment_output)
        return "\n".join(output_messages)
    
    output_messages.append(deployment_message)
    
    # Step 4: Wait for pods to be ready
    update_progress(0.4, "Waiting for pods to become ready...")
    pods_ready, pods_message, pod_status = wait_for_pods_ready(
        namespace, progress=progress, progress_text=progress_text
    )
    
    output_messages.append(pods_message)
    
    # Step 5: Set up port forwarding (only if pods are ready)
    if pods_ready:
        update_progress(0.9, "Setting up port forwarding...")
        port_forward_success, port_forward_message, port_forward_process = start_port_forward(namespace, release_name)
        
        global app_url, current_namespace
        # Store deployment information in global variables
        current_namespace = namespace
        
        if port_forward_success:
            update_progress(1.0, "Deployment complete!")
            # Store URL in a global variable for the URL component to use
            app_url = f"http://localhost:8080"
            
            output_messages.append("Deployment successful!")
            output_messages.append(f"Application is ready at: {app_url}")
            output_messages.append("\nPod Status Summary:")
            output_messages.append(pod_status)
            
            return "\n".join(output_messages)
        else:
            update_progress(0.95, "Deployment complete with warnings...")
            app_url = None
            
            output_messages.append("Deployment successful, but port-forwarding failed:")
            output_messages.append(port_forward_message)
            output_messages.append("\nYou can try manually with:")
            output_messages.append(f"kubectl port-forward svc/{release_name}-frontend 8080:80 -n {namespace}")
            output_messages.append("\nPod Status Summary:")
            output_messages.append(pod_status)
            
            return "\n".join(output_messages)
    else:
        update_progress(0.95, "Deployment incomplete - some pods not running")
        
        output_messages.append("Deployment incomplete - some pods are not running properly.")
        output_messages.append("Check the pod status below for more details:")
        output_messages.append("\nPod Status Summary:")
        output_messages.append(pod_status)
        output_messages.append("\nDiagnostics:")
        output_messages.append(get_deployment_diagnostics(namespace))
        
        # Set app_url to None since deployment was not successful
        app_url = None
        
        output_messages.append("\nTroubleshooting tips:")
        output_messages.append("- Check pod logs: kubectl logs -n {namespace} <pod-name>")
        output_messages.append("- Check pod events: kubectl describe pod -n {namespace} <pod-name>")
        output_messages.append("- Check storage: kubectl get pvc -n {namespace}")
        
        return "\n".join(output_messages)

# Global variables to store deployment information
app_url = None
current_namespace = None

def get_app_url_html():
    """Generate HTML for the app URL link."""
    global app_url, current_namespace
    
    html = []
    
    # No app URL available
    if not app_url:
        html.append("<div style='padding: 10px; background-color: #fff3cd; border: 1px solid #ffeeba; border-radius: 5px;'>")
        html.append("<p style='color: #856404; margin: 0;'><strong>⚠️ No application URL available.</strong> Please wait if deployment is in progress.</p>")
        
        # Add troubleshooting tips if we have namespace info
        if current_namespace:
            html.append("<p style='margin-top: 10px; margin-bottom: 0;'>Try manually with:</p>")
            html.append("<pre style='background: #f8f9fa; padding: 5px; border-radius: 3px; margin-top: 5px;'>")
            # Use release name if available, otherwise fall back to "chatqna"
            html.append(f"kubectl port-forward svc/[RELEASE_NAME]-frontend 8080:80 -n {current_namespace}")
            html.append("</pre>")
            html.append("<p><small>Replace [RELEASE_NAME] with your release name</small></p>")
            html.append("<p style='margin-top: 10px; margin-bottom: 0;'>To diagnose issues:</p>")
            html.append("<pre style='background: #f8f9fa; padding: 5px; border-radius: 3px; margin-top: 5px;'>")
            html.append(f"kubectl get pods -n {current_namespace}")
            html.append("</pre>")
        
        html.append("</div>")
    else:
        # We have an app URL
        html.append("<div style='padding: 10px; background-color: #d4edda; border: 1px solid #c3e6cb; border-radius: 5px;'>")
        html.append(f"<p style='color: #155724; margin-bottom: 10px;'><strong>✅ Application is ready!</strong></p>")
        html.append(f"<a href='{app_url}' target='_blank' class='app-link' style='display: inline-block; background-color: #007bff; color: white; padding: 10px 15px; border-radius: 5px; text-decoration: none; font-weight: bold; margin-bottom: 10px;'>")
        html.append(f"Open Application ↗")
        html.append("</a>")
        
        # Add URL text separately for easy copying
        html.append(f"<p style='margin-bottom: 0; color: #155724;'><small>URL: {app_url}</small></p>")
        html.append("</div>")
    
    return "".join(html)

def get_detailed_pod_status(namespace, pod_name=None):
    """Get detailed status of pods including container states."""
    try:
        if pod_name:
            # Get details for a specific pod
            cmd = ["kubectl", "describe", "pod", pod_name, "-n", namespace]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                return f"Error getting pod details: {result.stderr}"
            return result.stdout
        else:
            # Get basic info for all pods
            cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "wide"]
            basic_info = subprocess.run(cmd, capture_output=True, text=True)
            
            # Get more detailed container status for each pod
            cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", 
                   "jsonpath={range .items[*]}{.metadata.name}{': '}{range .status.containerStatuses[*]}{.name}{':'}{.state}{','}{end}{'\n'}{end}"]
            container_status = subprocess.run(cmd, capture_output=True, text=True)
            
            return f"Pod Overview:\n{basic_info.stdout}\n\nContainer States:\n{container_status.stdout}"
    except Exception as e:
        return f"Error retrieving pod status: {str(e)}"

# Function to get pod status for the UI
def get_pod_status_for_ui(namespace):
    """Get pod status for the UI"""
    try:
        if not namespace:
            return "No namespace specified"
            
        # Get pod status
        pod_status = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "wide"],
            capture_output=True, text=True
        )
        
        if pod_status.returncode != 0:
            return f"Error getting pod status: {pod_status.stderr}"
            
        return pod_status.stdout
    except Exception as e:
        return f"Error retrieving pod status: {str(e)}"

# Function to get detailed diagnostics for troubleshooting
def get_deployment_diagnostics(namespace):
    """Get detailed diagnostics for troubleshooting"""
    try:
        if not namespace:
            return "No namespace specified"
            
        # Combine multiple diagnostic commands
        results = []
        
        # 1. Get pod status with details
        results.append("=== POD STATUS ===")
        pod_status = get_detailed_pod_status(namespace)
        results.append(pod_status)
        
        # 2. Get service information
        try:
            results.append("\n=== SERVICES ===")
            services = subprocess.run(
                ["kubectl", "get", "services", "-n", namespace, "-o", "wide"],
                capture_output=True, text=True
            )
            results.append(services.stdout)
        except Exception as e:
            results.append(f"Error getting services: {str(e)}")
            
        # 3. Get PVC information
        try:
            results.append("\n=== PERSISTENT VOLUME CLAIMS ===")
            pvcs = subprocess.run(
                ["kubectl", "get", "pvc", "-n", namespace],
                capture_output=True, text=True
            )
            results.append(pvcs.stdout)
        except Exception as e:
            results.append(f"Error getting PVCs: {str(e)}")
            
        # 4. Find any pods in problem state and get their events and logs
        try:
            pod_cmd = ["kubectl", "get", "pods", "-n", namespace, "--field-selector=status.phase!=Running", "-o", "name"]
            problem_pods = subprocess.run(pod_cmd, capture_output=True, text=True).stdout.strip().split("\n")
            
            if problem_pods and problem_pods[0]:  # If not empty list or just empty string
                results.append("\n=== PROBLEM POD DETAILS ===")
                
                # Get details for first problematic pod
                pod_name = problem_pods[0].replace("pod/", "")
                results.append(f"Details for pod {pod_name}:")
                
                pod_describe = subprocess.run(
                    ["kubectl", "describe", "pod", pod_name, "-n", namespace],
                    capture_output=True, text=True
                )
                results.append(pod_describe.stdout)
                
                # Try to get logs if available
                results.append(f"\nLogs for pod {pod_name}:")
                try:
                    logs = subprocess.run(
                        ["kubectl", "logs", pod_name, "-n", namespace, "--tail=50"],
                        capture_output=True, text=True
                    )
                    results.append(logs.stdout if logs.stdout else logs.stderr)
                except Exception as e:
                    results.append(f"Error getting logs: {str(e)}")
        except Exception as e:
            results.append(f"Error getting problem pod details: {str(e)}")
            
        return "\n".join(results)
    except Exception as e:
        return f"Error retrieving diagnostics: {str(e)}"

# Create the Gradio interface
with gr.Blocks(title="Red Hat Demo Deployer", css="""
    .output-link {
        margin-top: 10px;
        padding: 10px;
        border-radius: 5px;
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
    }
    .output-link a {
        font-size: 16px;
        font-weight: bold;
        color: #007bff;
    }
    .output-link a:hover {
        text-decoration: underline;
    }
    .status-box {
        margin-top: 10px;
        padding: 10px;
        border-radius: 5px;
        background-color: #f8f9fa;
        border: 1px solid #dee2e6;
        font-family: monospace;
    }
    .container {
        margin-top: 20px;
    }
    .footer {
        margin-top: 20px;
        text-align: center;
        color: #6c757d;
    }
    .controls-row {
        display: flex;
        gap: 10px;
        margin-bottom: 10px;
    }
""") as app:
    gr.Markdown("# Red Hat Demo Deployment Interface")
    
    def list_port_forwards():
        forwards = get_port_forwards()
        if not forwards:
            return "No active port forwards found"
        
        result = "Active port forwards:\n"
        for fwd in forwards:
            result += f"PID {fwd['pid']}: {fwd['command']}\n"
        return result
    
    def kill_port_forward(pid):
        try:
            pid = int(pid)
            os.kill(pid, signal.SIGTERM)
            time.sleep(1)
            return f"Successfully terminated process {pid}"
        except Exception as e:
            return f"Error terminating process {pid}: {str(e)}"
    
    with gr.Row():
        # Left column for input controls
        with gr.Column(scale=1):
            with gr.Group():
                gr.Markdown("### Deployment Configuration")
                compute = gr.Dropdown(choices=COMPUTE_OPTIONS, label="Compute Platform", value=COMPUTE_OPTIONS[0])
                model = gr.Dropdown(choices=MODEL_OPTIONS["Xeon"], label="Model", value=MODEL_OPTIONS["Xeon"][0])
                db = gr.Dropdown(choices=DB_OPTIONS, label="Vector Database", value="redis")
                namespace = gr.Textbox(
                    label="Namespace",
                    placeholder="e.g., ed-chatqna",
                    info="Kubernetes namespace - isolates resources in the cluster"
                )
                release_name = gr.Textbox(
                    label="Release Name",
                    placeholder="e.g., ed-chatqna",
                    info="Unique name for this deployment - will prefix all resources"
                )
                
                deploy_btn = gr.Button("Deploy", variant="primary")
                
            
            # Pod status section
            # # Add some space before Pod Status section    
            gr.Markdown("<br>")
            with gr.Group():
                gr.Markdown("### Pod Status")
                status_namespace = gr.Textbox(
                    label="Namespace",
                    placeholder="Enter namespace to check",
                    show_label=True
                )
                with gr.Row():
                    status_btn = gr.Button("Get Status", variant="primary", size="sm")
                    diagnose_btn = gr.Button("Detailed Diagnostics", variant="secondary", size="sm")
                pod_status = gr.Textbox(
                    lines=8,
                    label="Pod Status",
                    placeholder="Pod status will appear here...",
                    elem_classes=["status-box"]
                )
        
        # Right column for output
        with gr.Column(scale=1):
            # Split into separate groups to isolate progress bars
            with gr.Group():
                gr.Markdown("### Deployment Output")
                output = gr.Textbox(lines=10, label="Deployment Progress")
            
            # Separate group for Application Access to avoid shared progress bar
            with gr.Group():
                gr.Markdown("### Application Access")
                link_output = gr.HTML(
                    value="<p>Deploy an application to see the access URL here</p>",
                    elem_classes=["output-link"]
                )
                
    # Update model choices when compute changes
    compute.change(update_model_choices, inputs=[compute], outputs=[model])
    
    # Update the URL link independently
    def update_url_link():
        return get_app_url_html()
    
    # Copy namespace value to status_namespace when it changes
    def sync_namespace(ns):
        return ns
        
    namespace.change(sync_namespace, inputs=[namespace], outputs=[status_namespace])
    
    # Status button handler
    status_btn.click(
        fn=get_pod_status_for_ui,
        inputs=[status_namespace],
        outputs=[pod_status]
    )
    
    # Diagnostics button handler
    diagnose_btn.click(
        fn=get_deployment_diagnostics,
        inputs=[status_namespace],
        outputs=[pod_status]
    )
    
    # Deploy button click handler - connected to the deployment output and status
    deploy_btn.click(
        fn=deploy_helm,
        inputs=[compute, db, model, namespace, release_name],
        outputs=[output],
        api_name="deploy"
    )
    
    # Set up events to update other UI elements after deployment
    deploy_btn.click(fn=update_url_link, inputs=[], outputs=[link_output])
    deploy_btn.click(fn=sync_namespace, inputs=[namespace], outputs=[status_namespace])
    deploy_btn.click(
        fn=lambda ns: time.sleep(2) and get_pod_status_for_ui(ns),  # Small delay to ensure pods appear
        inputs=[namespace],
        outputs=[pod_status]
    )
    
    # Add footer with helpful info
    with gr.Row():
        gr.HTML(
            """
            <div class="footer">
                <p>Red Hat Demo Deployer v1.0</p>
                <p><small>If you encounter issues, check the pod status and logs. For networking issues, check NetworkPolicies.</small></p>
                <p><small>© 2023 Red Hat | <a href="https://github.com/intel/GenAIInfra" target="_blank">GitHub</a></small></p>
            </div>
            """
        )

if __name__ == "__main__":
    app.launch(server_port=7777, show_api=False, share=True)
