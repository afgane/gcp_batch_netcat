import argparse
import json
import logging
import os
import sys
# import time
import uuid
from google.cloud import batch_v1

# Configure logging to go to stdout instead of stderr to avoid Galaxy marking job as failed
import sys
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--nfs_address', required=False, help='NFS server address (if not provided, will be auto-detected from /galaxy/server/database/ mount)')
    parser.add_argument('--output', required=True)
    parser.add_argument('--project', required=False, help='GCP Project ID (if not provided, will be extracted from service account key)')
    parser.add_argument('--region', required=True)
    parser.add_argument('--network', default='default', help='GCP Network name')
    parser.add_argument('--subnet', default='default', help='GCP Subnet name')
    parser.add_argument('--service_account_key', required=True)
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

    # time.sleep(10000)

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
    runnable.container.entrypoint = "/usr/bin/nc"
    runnable.container.commands = ["-z", "-v", nfs_address, "2049"]
    logger.debug(f"Container config: image={runnable.container.image_uri}, entrypoint={runnable.container.entrypoint}, commands={runnable.container.commands}")

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
    logger.info(f"NFS target: {nfs_address}:2049")

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
            f.write(f"NFS Address: {nfs_address}:2049\n")

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
            f.write(f"Traceback:\n")
            f.write(traceback.format_exc())

if __name__ == '__main__':
    main()
