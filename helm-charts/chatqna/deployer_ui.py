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
    "Gaudi": ["granite", "qwen", "llama", "mistral"]
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

HELM_CHART_PATH = Path("/home/elee/Projects/forked/GenAIInfra/helm-charts/chatqna")

def get_value_file_path(compute, model):
    """Get the path to the values file based on compute and model."""
    return HELM_CHART_PATH / f"rh-{compute.lower()}-{model}-values.yaml"

def get_db_value_file_path(db):
    """Get the path to the database values file."""
    if db == "redis":
        return HELM_CHART_PATH / "values.yaml"
    return HELM_CHART_PATH / f"rh-{db}-values.yaml"

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

def start_port_forward(namespace, release_name):
    """Start port-forwarding for the nginx pod."""
    try:
        print(f"\nStarting port forward process for {release_name} in namespace {namespace}")
        
        # Clean up any existing port-forwards
        cleanup_port_forwards(8080)
        
        # Verify port is available
        retries = 3
        while retries > 0 and not verify_port_available(8080):
            print(f"Waiting for port to become available... ({retries} retries left)")
            time.sleep(2)
            retries -= 1
            
        if not verify_port_available(8080):
            return None, "Port 8080 is still in use after cleanup attempts"
            
        # Get the nginx pod name
        print("Looking for nginx pod...")
        pod_cmd = f"kubectl get pods -n {namespace} -l app.kubernetes.io/name=nginx --field-selector status.phase=Running --no-headers -o custom-columns=':metadata.name'"
        pod_name = subprocess.check_output(pod_cmd, shell=True, text=True).strip()
        
        if not pod_name:
            print("No running nginx pod found. Checking all pods in namespace:")
            all_pods_cmd = f"kubectl get pods -n {namespace}"
            all_pods = subprocess.check_output(all_pods_cmd, shell=True, text=True)
            print(all_pods)
            return None, "No running nginx pod found"
            
        print(f"Found nginx pod: {pod_name}")
            
        # Start port forwarding
        print("Starting port forward...")
        port_forward_cmd = [
            "kubectl", "port-forward",
            f"pod/{pod_name}",
            "8080:80",  # Map container port 80 to local port 8080
            "-n", namespace
        ]
        
        process = subprocess.Popen(
            port_forward_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait for port-forward to establish
        time.sleep(3)
        print("Checking port forward status...")
        
        if process.poll() is None:  # Process is running
            # Verify we can connect to the port
            if verify_port_available(8080):
                print("Warning: Port 8080 is not in use by port-forward")
                return None, "Port-forward process started but port is not in use"
            print("Port forward successfully established")
            return process, f"http://localhost:8080"
        else:
            stderr = process.stderr.read().decode()
            stdout = process.stdout.read().decode()
            print(f"Port forward failed with error: {stderr}")
            print(f"Port forward output: {stdout}")
            return None, f"Port-forward failed: {stderr}"
    except Exception as e:
        return None, f"Error setting up port-forward: {str(e)}"

def deploy_helm(compute, db, model, namespace, release_name, progress=gr.Progress(track_tqdm=True)):
    """Deploy using Helm with the selected configuration."""
    print(f"\nStarting deployment with:")
    print(f"- Compute: {compute}")
    print(f"- Model: {model}")
    print(f"- Database: {db}")
    print(f"- Namespace: {namespace}")
    print(f"- Release: {release_name}")

    if not namespace or not release_name:
        print("Error: Missing required fields")
        return "Error: Namespace and Release Name are required!", ""
    
    progress(0, desc="Validating configuration...")
    print("Validating configuration and files...")
    # Validate files exist
    model_values = get_value_file_path(compute, model)
    db_values = get_db_value_file_path(db)
    
    if not model_values.exists():
        print(f"Error: Model values file not found at {model_values}")
        return f"Error: Model values file not found: {model_values}", ""
    if not db_values.exists():
        print(f"Error: Database values file not found at {db_values}")
        return f"Error: Database values file not found: {db_values}", ""
    
    progress(0.2, desc="Checking Hugging Face token...")
    # Check for HFTOKEN
    hf_token = os.getenv("HFTOKEN")
    if not hf_token:
        return "Error: HFTOKEN environment variable not set!", ""

    print("Checking HFTOKEN environment variable...")
    # Check for HFTOKEN
    hf_token = os.getenv("HFTOKEN")
    if not hf_token:
        print("Error: HFTOKEN environment variable not set")
        return "Error: HFTOKEN environment variable not set!", ""

    progress(0.4, desc="Preparing Helm command...")
    print("Preparing Helm command...")

    # Construct the helm command
    cmd = [
        "helm", "upgrade", release_name, ".",
        "-f", str(db_values),
        "-f", str(model_values),
        "--set", f"global.HUGGINGFACEHUB_API_TOKEN={hf_token}",
        "--set", f"global.HF_TOKEN={hf_token}",
        "-n", namespace,
        "--install"  # This makes it work for both install and upgrade
    ]
    print(f"Helm command prepared: {' '.join(cmd)}")

    progress(0.6, desc="Executing Helm deployment...")
    
    try:
        print("\nStarting Helm deployment...")
        # Change to the helm chart directory
        print(f"Changing to chart directory: {HELM_CHART_PATH}")
        os.chdir(HELM_CHART_PATH)
        
        progress(0.6, desc="Running Helm deployment...")
        print("Executing Helm command...")
        # Run the helm command
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        print(f"Helm command output:\n{result.stdout}")
        if result.returncode != 0:
            print(f"Helm deployment failed with error:\n{result.stderr}")
            return f"Error during deployment:\n{result.stderr}", ""
        print("Helm deployment completed successfully")
        
        progress(0.8, desc="Checking pod status...")
        # Check pod status
        pod_status = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace],
            capture_output=True, text=True
        )
        
        progress(0.9, desc="Setting up port forwarding...")
        # Start port forwarding
        port_forward_process, url = start_port_forward(namespace, release_name)
        
        if port_forward_process:
            progress(1.0, desc="Deployment complete!")
            return (
                f"""Deployment successful!

                    Pod Status:
                    {pod_status.stdout}
                    
                    App URL: {url}""",
                ""
            )
        else:
            progress(1.0, desc="Deployment complete with warnings...")
            return (
                f"""Deployment successful, but port-forward failed!

                Pod Status:
                {pod_status.stdout}

                Port-forward error: {url}""",
                                ""
                )
    
    except subprocess.CalledProcessError as e:
        return f"Error executing command:\n{e.stderr}", ""
    except Exception as e:
        return f"Error: {str(e)}", ""

# Create the Gradio interface
with gr.Blocks(title="Red Hat Demo Deployer") as app:
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
            compute = gr.Dropdown(choices=COMPUTE_OPTIONS, label="Compute Platform")
            model = gr.Dropdown(choices=MODEL_OPTIONS["Xeon"], label="Model")
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
            deploy_btn = gr.Button("Deploy")
        
        # Right column for output
        with gr.Column(scale=1):
            # Single group for all output elements
            with gr.Group() as output_group:
                gr.Markdown("### Deployment Progress")
                output = gr.Textbox(lines=10, interactive=True)
            link_output = gr.HTML(elem_classes=["output-link"])
            
    # Update model choices when compute changes
    compute.change(update_model_choices, inputs=[compute], outputs=[model])
    
    # Deploy button click handler
    deploy_btn.click(
        fn=deploy_helm,
        inputs=[compute, db, model, namespace, release_name],
        outputs=[output, link_output],
        api_name="deploy"
    )

if __name__ == "__main__":
    app.launch(server_port=7777, show_api=False, share=True)
