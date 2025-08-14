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

    if args.test_type == 'nfs':
        # NFS server address is required
        if not args.nfs_address:
            raise ValueError("NFS server address is required. Please provide --nfs_address parameter with the LoadBalancer external IP.")

        nfs_address = args.nfs_address
        logger.info(f"Using provided NFS address: {nfs_address}")
        return nfs_address, 2049

    else:
        raise ValueError(f"Unsupported test type: {args.test_type}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--nfs_address', required=True, help='NFS server LoadBalancer external IP address (required)')
    parser.add_argument('--output', required=True)
    parser.add_argument('--project', required=False, help='GCP Project ID (if not provided, will be extracted from service account key)')
    parser.add_argument('--region', required=True)
    parser.add_argument('--network', default='default', help='GCP Network name')
    parser.add_argument('--subnet', default='default', help='GCP Subnet name')
    parser.add_argument('--service_account_key', required=True)
    args = parser.parse_args()

    # Default to NFS test type since that's what this tool is for
    args.test_type = 'nfs'

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

    # Create a comprehensive test script
    test_script = f'''#!/bin/bash
set -e
echo "=== GCP Batch NFS Connectivity Test ==="
echo "Target: {target_host}:{target_port}"
echo "Timestamp: $(date)"
echo "Container hostname: $(hostname)"
echo "Host VM Image: galaxy-k8s-boot-v2025-08-12"
echo "Container Image: afgane/gcp-batch-netcat:0.3.0"
echo ""

# Basic system info
echo "=== System Information ==="
echo "OS Release:"
cat /etc/os-release | head -5 2>/dev/null || echo "OS release info not available"
echo "Kernel version:"
uname -r
echo "Architecture:"
uname -m
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
echo "=== Primary NFS Connectivity Test ==="
echo "Testing connection to NFS server {target_host}:{target_port}..."
timeout 30 nc -z -v -w 10 {target_host} {target_port}
nc_result=$?
echo "Netcat result: $nc_result"
echo ""

# NFS client capabilities
echo "=== NFS Client Information ==="
echo "NFS client version:"
/sbin/mount.nfs -V 2>/dev/null || echo "mount.nfs not available"
echo "RPC services:"
rpcinfo -p 2>/dev/null || echo "rpcinfo not available"
echo ""

# Additional connectivity tests
echo "=== Additional Connectivity Tests ==="
echo "Testing external connectivity (Google DNS 8.8.8.8:53):"
timeout 10 nc -z -v -w 5 8.8.8.8 53 && echo "✓ External DNS reachable" || echo "✗ External DNS unreachable"

echo ""
echo "=== Network Troubleshooting ==="
echo "Route table:"
ip route
echo ""

# NFS Mount Test - Check if Batch mounted it for us
echo "=== NFS Mount Test (via Batch Volume) ==="
NFS_MOUNT_POINT="/mnt/nfs"
mount_result=1

echo "Checking if NFS is mounted by Batch at $NFS_MOUNT_POINT..."
if [ -d "$NFS_MOUNT_POINT" ]; then
    echo "✓ NFS mount point exists"

    # Check if it's actually mounted
    if mount | grep "$NFS_MOUNT_POINT"; then
        mount_result=0
        echo "✓ NFS mounted by Batch successfully!"

        echo ""
        echo "=== NFS Share Contents ==="
        echo "Long listing of NFS share:"
        ls -la "$NFS_MOUNT_POINT" 2>/dev/null || echo "Could not list directory contents"

        echo ""
        echo "Disk usage of NFS share:"
        df -h "$NFS_MOUNT_POINT" 2>/dev/null || echo "Could not get disk usage"

        # Look for export subdirectories
        echo ""
        echo "=== Looking for export directories ==="
        if [ -d "$NFS_MOUNT_POINT/export" ]; then
            echo "✓ Found: export directory"
            ls -la "$NFS_MOUNT_POINT/export" | head -10 2>/dev/null || echo "Could not list export contents"

            # Look for PVC subdirectories
            echo "Looking for PVC directories in export..."
            find "$NFS_MOUNT_POINT/export" -name "pvc-*" -type d | head -5 2>/dev/null || echo "No PVC directories found"
        else
            echo "✗ No export directory found"
        fi

        # Try to find common Galaxy directories
        echo ""
        echo "=== Looking for Galaxy directories ==="

        # First check if they exist directly in the NFS root
        galaxy_dirs_in_root=0
        for dir in "jobs_directory" "shed_tools" "objects" "tools" "cache" "config"; do
            if [ -d "$NFS_MOUNT_POINT/$dir" ]; then
                echo "✓ Found in root: $dir"
                ls -la "$NFS_MOUNT_POINT/$dir" | head -5
                galaxy_dirs_in_root=$((galaxy_dirs_in_root + 1))
            fi
        done

        if [ $galaxy_dirs_in_root -eq 0 ]; then
            echo "✗ No Galaxy directories found in NFS root"
        else
            echo "✓ Found $galaxy_dirs_in_root Galaxy directories in NFS root"
        fi

        # Then check inside any PVC directories under export
        if [ -d "$NFS_MOUNT_POINT/export" ]; then
            echo ""
            echo "=== Checking PVC directories for Galaxy structure ==="

            # Find all PVC directories
            pvc_count=0
            for pvc_dir in $(find "$NFS_MOUNT_POINT/export" -name "pvc-*" -type d 2>/dev/null); do
                pvc_count=$((pvc_count + 1))
                echo ""
                echo "Checking PVC ($pvc_count): $(basename $pvc_dir)"
                echo "  Full path: $pvc_dir"

                # Show directory listing of PVC
                echo "  Contents:"
                ls -la "$pvc_dir" | head -10 | sed 's/^/    /'

                # Check for Galaxy directories inside this PVC
                galaxy_dirs_found=0
                for dir in "jobs_directory" "shed_tools" "objects" "tools" "cache" "config" "deps" "tmp"; do
                    if [ -d "$pvc_dir/$dir" ]; then
                        echo "  ✓ Found Galaxy directory: $dir"
                        # Show a sample of contents
                        ls -la "$pvc_dir/$dir" 2>/dev/null | head -3 | sed 's/^/      /'
                        galaxy_dirs_found=$((galaxy_dirs_found + 1))
                    fi
                done

                # Check for Galaxy-specific files
                galaxy_files_found=0
                for file in "galaxy.yml" "universe_wsgi.ini" "config/galaxy.yml" "results.sqlite" "celery-beat-schedule"; do
                    if [ -f "$pvc_dir/$file" ]; then
                        echo "  ✓ Found Galaxy file: $file"
                        galaxy_files_found=$((galaxy_files_found + 1))
                    fi
                done

                total_indicators=$((galaxy_dirs_found + galaxy_files_found))
                if [ $total_indicators -gt 0 ]; then
                    echo "  🎯 This PVC contains $galaxy_dirs_found Galaxy directories and $galaxy_files_found Galaxy files"

                    # Test write access
                    test_file="$pvc_dir/.batch_test_file_$(date +%s)"
                    if echo "test" > "$test_file" 2>/dev/null; then
                        echo "  ✓ Write access confirmed"
                        rm -f "$test_file" 2>/dev/null
                    else
                        echo "  ✗ No write access"
                    fi

                    # Test specific Galaxy directories access
                    if [ -d "$pvc_dir/jobs_directory" ]; then
                        echo "  � Jobs directory details:"
                        du -sh "$pvc_dir/jobs_directory" 2>/dev/null | sed 's/^/      /' || echo "      Could not get size"
                        job_count=$(find "$pvc_dir/jobs_directory" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
                        echo "      Job subdirectories: $job_count"
                    fi

                    if [ -d "$pvc_dir/shed_tools" ]; then
                        echo "  🔧 Shed tools directory details:"
                        du -sh "$pvc_dir/shed_tools" 2>/dev/null | sed 's/^/      /' || echo "      Could not get size"
                        tool_count=$(find "$pvc_dir/shed_tools" -name "*.py" -o -name "*.xml" 2>/dev/null | wc -l)
                        echo "      Tool files (py/xml): $tool_count"
                    fi
                else
                    echo "  ✗ No Galaxy directories or files found in this PVC"
                fi
            done

            if [ $pvc_count -eq 0 ]; then
                echo "✗ No PVC directories found in export"
            else
                echo ""
                echo "📊 Summary: Found $pvc_count PVC directories in export"
            fi
        else
            echo ""
            echo "✗ No export directory found in NFS mount"
        fi
    else
        echo "✗ NFS mount point exists but is not mounted"
        echo "This suggests Batch volume configuration may be incorrect"
    fi
else
    echo "✗ NFS mount point $NFS_MOUNT_POINT does not exist"
    echo "This suggests Batch volume was not configured"
fi

# CVMFS Mount Test
echo ""
echo "=== CVMFS Access Test ==="
echo "Checking if CVMFS is bind-mounted from host VM..."
if [ -d "/cvmfs" ]; then
    echo "✓ /cvmfs directory exists (bind-mounted from host)"
    ls -la /cvmfs 2>/dev/null || echo "Could not list /cvmfs contents"

    echo ""
    echo "Checking for Galaxy CVMFS repository..."
    cvmfs_result=1
    if [ -d "/cvmfs/data.galaxyproject.org" ]; then
        cvmfs_result=0
        echo "✓ Galaxy CVMFS repository accessible!"

        echo ""
        echo "=== CVMFS Repository Contents ==="
        echo "Long listing of CVMFS repository root:"
        ls -la "/cvmfs/data.galaxyproject.org" 2>/dev/null | head -10 || echo "Could not list directory contents"

        echo ""
        echo "Listing Galaxy reference data directories:"
        for dir in "byhand" "managed"; do
            if [ -d "/cvmfs/data.galaxyproject.org/$dir" ]; then
                echo "✓ Found CVMFS directory: $dir"
                ls "/cvmfs/data.galaxyproject.org/$dir" | head -5 2>/dev/null || echo "Could not list contents"
            else
                echo "✗ Not found: $dir"
            fi
        done

        echo ""
        echo "=== CVMFS File Access Test ==="
        echo "Testing access to specific Galaxy reference file..."
        echo "File: /cvmfs/data.galaxyproject.org/byhand/Arabidopsis_thaliana_TAIR10/seq/Arabidopsis_thaliana_TAIR10.fa.fai"

        CVMFS_TEST_FILE="/cvmfs/data.galaxyproject.org/byhand/Arabidopsis_thaliana_TAIR10/seq/Arabidopsis_thaliana_TAIR10.fa.fai"
        if [ -f "$CVMFS_TEST_FILE" ]; then
            echo "✓ File exists, reading first 10 lines:"
            head "$CVMFS_TEST_FILE" 2>/dev/null || echo "Could not read file contents"
        else
            echo "✗ File not found"
            echo "Checking if parent directories exist:"
            [ -d "/cvmfs/data.galaxyproject.org/byhand/Arabidopsis_thaliana_TAIR10" ] && echo "  ✓ Arabidopsis_thaliana_TAIR10 directory exists" || echo "  ✗ Arabidopsis_thaliana_TAIR10 directory missing"
            [ -d "/cvmfs/data.galaxyproject.org/byhand/Arabidopsis_thaliana_TAIR10/seq" ] && echo "  ✓ seq directory exists" || echo "  ✗ seq directory missing"
        fi

        echo ""
        echo "CVMFS mount information from host:"
        mount | grep cvmfs || echo "CVMFS mount info not visible from container"
    else
        echo "✗ Galaxy CVMFS repository not found at /cvmfs/data.galaxyproject.org"
        echo "This may indicate:"
        echo "- CVMFS client not running on host VM"
        echo "- Repository not mounted on host"
        echo "- Bind mount not properly configured"
    fi
else
    echo "✗ /cvmfs directory not found"
    echo "This indicates the bind mount from host VM failed"
    echo "Expected: /cvmfs from host VM bind-mounted into container"
fi


echo ""
echo "=== Final Result ==="
if [ $nc_result -eq 0 ] && [ $mount_result -eq 0 ]; then
    echo "✓ SUCCESS: Both network connectivity and NFS mount to {target_host}:{target_port} successful"
    if [ $cvmfs_result -eq 0 ]; then
        echo "✓ BONUS: CVMFS repository mount also successful"
    else
        echo "ℹ INFO: CVMFS mount failed (may not be available in this image)"
    fi
    exit 0
elif [ $nc_result -eq 0 ]; then
    echo "⚠ PARTIAL SUCCESS: Network connectivity successful but NFS mount failed"
    echo "Network connection to {target_host}:{target_port} works, but NFS mounting failed."
    echo "This suggests:"
    echo "- NFS server is reachable but may not be properly configured"
    echo "- NFS export permissions may be incorrect"
    echo "- Firewall may allow port 2049 but block other NFS ports (111, 20048)"
    if [ $cvmfs_result -eq 0 ]; then
        echo "✓ CVMFS repository mount was successful"
    fi
    exit 1
else
    echo "✗ FAILED: Network connectivity to NFS server {target_host}:{target_port} failed"
    echo "This suggests a network connectivity issue between GCP Batch and the NFS server."
    echo "Common causes:"
    echo "- Firewall rules blocking NFS traffic (port 2049)"
    echo "- NFS service not accessible from external networks (only ClusterIP)"
    echo "- NFS server not properly exposed via LoadBalancer"
    echo ""
    echo "Solutions:"
    echo "- Ensure NFS service has type LoadBalancer with external IP"
    echo "- Check GCP firewall rules allow traffic from Batch subnet to NFS"
    echo "- Verify the IP address is the LoadBalancer external IP, not ClusterIP"
    if [ $cvmfs_result -eq 0 ]; then
        echo ""
        echo "✓ CVMFS repository mount was successful (good network connectivity to external services)"
    fi
    exit 1
fi
'''

    # Define the job using the Python client library objects
    logger.info("Building job specification...")

    # Escape the test script for use in docker command (outside f-string to avoid backslash issues)
    escaped_test_script = test_script.replace("'", "'\"'\"'")

    # Create a host script that triggers CVMFS mount and then runs the container
    host_script = f'''#!/bin/bash
set -e
echo "=== Pre-Container Host Script ==="
echo "Timestamp: $(date)"
echo "Host VM Image: galaxy-k8s-boot-v2025-08-12"
echo "Running on host before container starts..."
echo ""

echo "=== Triggering CVMFS Mount ==="
echo "Checking CVMFS autofs status:"
mount | grep cvmfs || echo "No CVMFS mounts yet"

echo ""
echo "Triggering CVMFS mount by accessing repository:"
ls /cvmfs/data.galaxyproject.org/ || echo "Could not access CVMFS repository"

echo ""
echo "After access - checking CVMFS mounts:"
mount | grep cvmfs || echo "Still no CVMFS mounts visible"

echo ""
echo "Testing specific file access from host:"
if [ -f "/cvmfs/data.galaxyproject.org/byhand/Arabidopsis_thaliana_TAIR10/seq/Arabidopsis_thaliana_TAIR10.fa.fai" ]; then
    echo "✓ CVMFS file accessible from host"
    head -3 "/cvmfs/data.galaxyproject.org/byhand/Arabidopsis_thaliana_TAIR10/seq/Arabidopsis_thaliana_TAIR10.fa.fai"
else
    echo "✗ CVMFS file not accessible from host"
fi

echo ""
echo "=== Starting Container ==="
echo "Running container with bind-mounted CVMFS and NFS..."

# Run the container with the test script and volume mounts
docker run --rm \\
    -v /cvmfs:/cvmfs:ro \\
    -v /mnt/nfs:/mnt/nfs:rw \\
    afgane/gcp-batch-netcat:0.3.0 \\
    /bin/bash -c '{escaped_test_script}'
'''

    runnable = batch_v1.Runnable()
    runnable.script = batch_v1.Runnable.Script()
    runnable.script.text = host_script
    logger.debug(f"Host script configured to trigger CVMFS mount and run container")

    task = batch_v1.TaskSpec()
    task.runnables = [runnable]
    task.compute_resource = batch_v1.ComputeResource()
    task.compute_resource.cpu_milli = 1000
    task.compute_resource.memory_mib = 1024
    logger.debug(f"Compute resources: CPU={task.compute_resource.cpu_milli}m, Memory={task.compute_resource.memory_mib}MiB")

    # Configure NFS volume in the task
    volume = batch_v1.Volume()
    volume.nfs = batch_v1.NFS()
    volume.nfs.server = target_host
    volume.nfs.remote_path = "/"  # Root of the NFS export
    volume.mount_path = "/mnt/nfs"

    task.volumes = [volume]
    logger.debug(f"NFS volume configured: {target_host}:/ -> /mnt/nfs")

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

    # Instance policy with custom VM image
    instance_policy = batch_v1.AllocationPolicy.InstancePolicy()
    instance_policy.machine_type = "e2-medium"  # Specify machine type for custom image
    instance_policy.boot_disk = batch_v1.AllocationPolicy.Disk()
    instance_policy.boot_disk.image = f"projects/{project_id}/global/images/galaxy-k8s-boot-v2025-08-12"
    instance_policy.boot_disk.size_gb = 99
    logger.debug(f"Using custom VM image: {instance_policy.boot_disk.image}")

    # Wrap the instance policy in InstancePolicyOrTemplate
    instance_policy_or_template = batch_v1.AllocationPolicy.InstancePolicyOrTemplate()
    instance_policy_or_template.policy = instance_policy

    allocation_policy = batch_v1.AllocationPolicy()
    allocation_policy.network = network_policy
    allocation_policy.instances = [instance_policy_or_template]

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
            f.write(f"NFS Target: {target_host}:{target_port}\n")
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
            f.write(f"NFS Target: {target_host}:{target_port}\n")
            f.write(f"Traceback:\n")
            f.write(traceback.format_exc())

if __name__ == '__main__':
    main()
