import argparse
import json
import logging
import os
import sys
import uuid
from google.cloud import batch_v1

# Configure logging to go to stdout instead of stderr to avoid Galaxy marking job as failed
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

def determine_test_target(args):
    """Determine the target host and port based on test type"""

    if args.test_type == 'custom':
        if not args.custom_host:
            raise ValueError("custom_host is required when test_type is 'custom'")
        return args.custom_host, args.custom_port

    elif args.test_type == 'nfs':
        # Extract NFS server address if not provided
        if args.nfs_address:
            nfs_address = args.nfs_address
            logger.info(f"Using provided NFS address: {nfs_address}")
        else:
            try:
                # Try to detect NFS server from /galaxy/server/database/ mount
                import subprocess
                result = subprocess.run(['mount'], capture_output=True, text=True)
                nfs_address = None

                for line in result.stdout.split('\n'):
                    if '/galaxy/server/database' in line and ':' in line:
                        # Look for NFS mount pattern: server:/path on /galaxy/server/database
                        parts = line.split()
                        for part in parts:
                            if ':' in part and part.count(':') == 1:
                                nfs_address = part.split(':')[0]
                                break
                        if nfs_address:
                            logger.info(f"Detected NFS address from mount: {nfs_address}")
                            break

                if not nfs_address:
                    # Fallback: try to parse /proc/mounts
                    try:
                        with open('/proc/mounts', 'r') as f:
                            for line in f:
                                if '/galaxy/server/database' in line and ':' in line:
                                    parts = line.split()
                                    if len(parts) > 0 and ':' in parts[0]:
                                        nfs_address = parts[0].split(':')[0]
                                        logger.info(f"Detected NFS address from /proc/mounts: {nfs_address}")
                                        break
                    except:
                        pass

                if not nfs_address:
                    raise ValueError("Could not auto-detect NFS server address from /galaxy/server/database/ mount")

                logger.info(f"Auto-detected NFS address from mount: {nfs_address}")
            except Exception as e:
                logger.error(f"Failed to auto-detect NFS address: {e}")
                raise
        return nfs_address, 2049

    elif args.test_type == 'galaxy_web':
        # Try to detect Galaxy web service
        try:
            import subprocess
            result = subprocess.run(['kubectl', 'get', 'svc', '-o', 'json'], capture_output=True, text=True)
            if result.returncode == 0:
                services = json.loads(result.stdout)
                for item in services.get('items', []):
                    name = item.get('metadata', {}).get('name', '')
                    if 'galaxy' in name.lower() and ('web' in name.lower() or 'nginx' in name.lower()):
                        # Found a Galaxy web service
                        spec = item.get('spec', {})
                        if spec.get('type') == 'LoadBalancer':
                            ingress = item.get('status', {}).get('loadBalancer', {}).get('ingress', [])
                            if ingress:
                                ip = ingress[0].get('ip')
                                if ip:
                                    port = 80
                                    for port_spec in spec.get('ports', []):
                                        if port_spec.get('port'):
                                            port = port_spec['port']
                                            break
                                    logger.info(f"Found Galaxy web service LoadBalancer: {ip}:{port}")
                                    return ip, port
                        # Fallback to ClusterIP
                        cluster_ip = spec.get('clusterIP')
                        if cluster_ip and cluster_ip != 'None':
                            port = 80
                            for port_spec in spec.get('ports', []):
                                if port_spec.get('port'):
                                    port = port_spec['port']
                                    break
                            logger.info(f"Found Galaxy web service ClusterIP: {cluster_ip}:{port}")
                            return cluster_ip, port
        except Exception as e:
            logger.warning(f"Could not auto-detect Galaxy web service: {e}")

        # Fallback: try common Galaxy service names
        common_hosts = ['galaxy-web', 'galaxy-nginx', 'galaxy']
        logger.info(f"Trying common Galaxy service name: {common_hosts[0]}")
        return common_hosts[0], 80

    elif args.test_type == 'k8s_dns':
        # Test Kubernetes DNS resolution
        return 'kubernetes.default.svc.cluster.local', 443

    elif args.test_type == 'google_dns':
        # Test external connectivity
        return '8.8.8.8', 53

    else:
        raise ValueError(f"Unsupported test type: {args.test_type}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--nfs_address', required=False, help='NFS server address (if not provided, will be auto-detected from /galaxy/server/database/ mount)')
    parser.add_argument('--output', required=True)
    parser.add_argument('--project', required=False, help='GCP Project ID (if not provided, will be extracted from service account key)')
    parser.add_argument('--region', required=True)
    parser.add_argument('--network', default='default', help='GCP Network name')
    parser.add_argument('--subnet', default='default', help='GCP Subnet name')
    parser.add_argument('--service_account_key', required=True)
    parser.add_argument('--test_type', default='nfs', choices=['nfs', 'galaxy_web', 'k8s_dns', 'google_dns', 'custom'],
                       help='Type of connectivity test to perform')
    parser.add_argument('--custom_host', required=False, help='Custom host to test (required if test_type is custom)')
    parser.add_argument('--custom_port', type=int, default=80, help='Custom port to test (default: 80)')
    args = parser.parse_args()

    # Set up authentication using the service account key
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = args.service_account_key
    logger.info(f"Authentication configured with service account: {args.service_account_key}")

    # Extract GCP project ID from service account key if not provided
    if args.project:
        project_id = args.project
        logger.info(f"Using provided project ID: {project_id}")
    else:
        try:
            with open(args.service_account_key, 'r') as f:
                service_account_data = json.load(f)
            project_id = service_account_data.get('project_id')
            if not project_id:
                raise ValueError("project_id not found in service account key file")
            logger.info(f"Extracted project ID from service account key: {project_id}")
        except Exception as e:
            logger.error(f"Failed to extract project ID from service account key: {e}")
            raise

    # Determine target host and port based on test type
    try:
        target_host, target_port = determine_test_target(args)
        logger.info(f"Target determined: {target_host}:{target_port}")
    except Exception as e:
        logger.error(f"Failed to determine target: {e}")
        raise

    job_name = f'netcat-job-{uuid.uuid4()}'
    logger.info(f"Generated job name: {job_name}")

    # Create Batch client
    logger.info("Creating Batch client...")
    client = batch_v1.BatchServiceClient()
    logger.info("Batch client created successfully")

    # Define the job using the Python client library objects
    logger.info("Building job specification...")
    runnable = batch_v1.Runnable()
    runnable.container = batch_v1.Runnable.Container()
    runnable.container.image_uri = "afgane/gcp-batch-netcat:0.2.0"

    # Create a comprehensive test script
    test_script = f'''#!/bin/bash
set -e
echo "=== GCP Batch Connectivity Test ==="
echo "Test Type: {args.test_type}"
echo "Target: {target_host}:{target_port}"
echo "Timestamp: $(date)"
echo "Container hostname: $(hostname)"
echo ""

# Basic network info
echo "=== Network Information ==="
echo "Container IP addresses:"
hostname -I
echo "Default route:"
ip route | grep default || echo "No default route found"
echo ""

# DNS configuration
echo "=== DNS Configuration ==="
echo "DNS servers:"
cat /etc/resolv.conf | grep nameserver || echo "No nameservers found"
echo ""

# Test DNS resolution of target
echo "=== DNS Resolution Test ==="
echo "Resolving {target_host}:"
nslookup {target_host} || {{
    echo "DNS resolution failed for {target_host}"
    echo "Trying with Google DNS (8.8.8.8):"
    nslookup {target_host} 8.8.8.8 || echo "DNS resolution failed even with Google DNS"
}}
echo ""

# Basic connectivity test
echo "=== Primary Connectivity Test ==="
echo "Testing connection to {target_host}:{target_port}..."
timeout 30 nc -z -v -w 10 {target_host} {target_port}
nc_result=$?
echo "Netcat result: $nc_result"
echo ""

# Additional connectivity tests
echo "=== Additional Connectivity Tests ==="
echo "Testing Google DNS (8.8.8.8:53):"
timeout 10 nc -z -v -w 5 8.8.8.8 53 && echo "✓ External DNS reachable" || echo "✗ External DNS unreachable"

echo "Testing Kubernetes API (if accessible):"
timeout 10 nc -z -v -w 5 kubernetes.default.svc.cluster.local 443 2>/dev/null && echo "✓ Kubernetes API reachable" || echo "✗ Kubernetes API unreachable"

echo ""
echo "=== Network Troubleshooting ==="
echo "Route table:"
ip route
echo ""
echo "ARP table:"
arp -a 2>/dev/null || echo "ARP command not available"
echo ""

echo "=== Final Result ==="
if [ $nc_result -eq 0 ]; then
    echo "✓ SUCCESS: Connection to {target_host}:{target_port} successful"
    exit 0
else
    echo "✗ FAILED: Connection to {target_host}:{target_port} failed"
    echo "This suggests a network connectivity issue between GCP Batch and the target service."
    echo "Common causes:"
    echo "- Firewall rules blocking traffic"
    echo "- Service not accessible from external networks"
    echo "- Target service only accepting internal cluster traffic"
    exit 1
fi
'''

    runnable.container.entrypoint = "/bin/bash"
    runnable.container.commands = ["-c", test_script]
    logger.debug(f"Container config: image={runnable.container.image_uri}, entrypoint={runnable.container.entrypoint}")

    task = batch_v1.TaskSpec()
    task.runnables = [runnable]
    task.compute_resource = batch_v1.ComputeResource()
    task.compute_resource.cpu_milli = 1000
    task.compute_resource.memory_mib = 1024
    logger.debug(f"Compute resources: CPU={task.compute_resource.cpu_milli}m, Memory={task.compute_resource.memory_mib}MiB")

    task_group = batch_v1.TaskGroup()
    task_group.task_count = 1
    task_group.parallelism = 1
    task_group.task_spec = task
    logger.debug(f"Task group: count={task_group.task_count}, parallelism={task_group.parallelism}")

    # Network configuration: Batch job should run in the same network as the NFS server
    network_interface = batch_v1.AllocationPolicy.NetworkInterface()
    network_interface.network = f"global/networks/{args.network}"
    network_interface.subnetwork = f"regions/{args.region}/subnetworks/{args.subnet}"
    logger.debug(f"Network: {network_interface.network}")
    logger.debug(f"Subnet: {network_interface.subnetwork}")

    network_policy = batch_v1.AllocationPolicy.NetworkPolicy()
    network_policy.network_interfaces = [network_interface]

    allocation_policy = batch_v1.AllocationPolicy()
    allocation_policy.network = network_policy

    job = batch_v1.Job()
    job.task_groups = [task_group]
    job.allocation_policy = allocation_policy
    job.logs_policy = batch_v1.LogsPolicy()
    job.logs_policy.destination = batch_v1.LogsPolicy.Destination.CLOUD_LOGGING
    logger.info("Job specification built successfully")

    create_request = batch_v1.CreateJobRequest()
    create_request.parent = f"projects/{project_id}/locations/{args.region}"
    create_request.job_id = job_name
    create_request.job = job
    logger.debug(f"Create request parent: {create_request.parent}")
    logger.debug(f"Create request job_id: {create_request.job_id}")

    logger.info(f"Submitting job with name: {job_name}")
    logger.info(f"Target project: {project_id}")
    logger.info(f"Target Batch region: {args.region}")
    logger.info(f"Test target: {target_host}:{target_port}")

    # Proceed with job submission
    try:
        logger.info("Calling client.create_job()...")
        job_response = client.create_job(request=create_request)
        logger.info("Job submitted successfully!")
        logger.info(f"Job name: {job_response.name}")
        logger.info(f"Job UID: {job_response.uid}")

        with open(args.output, 'w') as f:
            f.write("Job submitted successfully using Python client.\n")
            f.write(f"Job name: {job_name}\n")
            f.write(f"Job response name: {job_response.name}\n")
            f.write(f"Job UID: {job_response.uid}\n")
            f.write(f"Project: {project_id}\n")
            f.write(f"Region: {args.region}\n")
            f.write(f"Test Type: {args.test_type}\n")
            f.write(f"Target: {target_host}:{target_port}\n")
            f.write(f"\nTo view job logs, run:\n")
            f.write(f"gcloud logging read 'resource.type=gce_instance AND resource.labels.instance_id={job_name}' --project={project_id}\n")

    except Exception as e:
        logger.error(f"Error submitting job: {type(e).__name__}: {e}")
        logger.error(f"Error details: {str(e)}")
        import traceback
        logger.error("Traceback:", exc_info=True)

        with open(args.output, 'w') as f:
            f.write(f"Error submitting job: {type(e).__name__}: {e}\n")
            f.write(f"Error details: {str(e)}\n")
            f.write(f"Job name: {job_name}\n")
            f.write(f"Project: {project_id}\n")
            f.write(f"Region: {args.region}\n")
            f.write(f"Test Type: {args.test_type}\n")
            f.write(f"Target: {target_host}:{target_port}\n")
            f.write(f"Traceback:\n")
            f.write(traceback.format_exc())

if __name__ == '__main__':
    main()
