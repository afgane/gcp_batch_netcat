# GCP Batch NFS and CVMFS Connectivity Test Tool

A Galaxy tool that submits a job to Google Cloud Platform (GCP) Batch service to
test comprehensive network connectivity to NFS storage and CVMFS repositories.
This tool is specifically designed for Galaxy deployments using the Galaxy Helm
chart, where it validates connectivity between GCP Batch workers and critical
Galaxy infrastructure services.

## Overview

This tool creates and submits a GCP Batch job that runs comprehensive
connectivity tests using a custom VM image with CVMFS support. It's particularly
useful for:
- Testing network connectivity and mounting capabilities for NFS storage servers
- Validating access to Galaxy's CVMFS reference data repositories
- Verifying NFSv4.2 mounting with proper security contexts
- Testing specific Galaxy reference data file access (e.g., Arabidopsis TAIR10)
- Troubleshooting connectivity issues in Galaxy deployments on Kubernetes
- Debugging firewall rules, NFS export configurations, and CVMFS client setup
- Comprehensive Network Diagnostics: DNS resolution, routing, and external connectivity
- Custom VM Integration: Uses galaxy-k8s-boot-v2025-08-12 image with pre-configured CVMFS client

The tool is available in the Main Tool Shed at:
https://toolshed.g2.bx.psu.edu/view/enis/gcp_batch_netcat/

## For use with the Galaxy Helm Chart

This tool is specifically designed for Galaxy deployments using the Galaxy Helm
chart. A sample deployment can be obtained using the [galaxy-k8s-boot
repository](https://github.com/galaxyproject/galaxy-k8s-boot/).

## Input Parameters Reference

The Galaxy tool interface presents the following parameters:

### Required Parameters

#### **GCP Batch Region**
- **Galaxy Label**: "GCP Batch Region"
- **Description**: The GCP region where the Batch job will be submitted
- **Example**: `us-central1`
- **Note**: Choose the region as the Galaxy deployment

#### **GCP Network name**
- **Galaxy Label**: "GCP Network name"
- **Description**: The name of the GCP VPC network in which Galaxy runs
- **Examples**: `default`, `galaxy-vpc`
- **Important**: The network must allow communication between Batch workers and the target services

#### **GCP Subnet name**
- **Galaxy Label**: "GCP Subnet name"
- **Description**: The name of the subnet in which Galaxy runs
- **Examples**: `default`

#### **NFS Server Address**
- **Galaxy Label**: "NFS Server Address"
- **Description**: LoadBalancer external IP address of the NFS service that GCP Batch jobs should connect to. This is typically a private IP within your VPC network that's accessible to Batch jobs but not the public internet. This must be the LoadBalancer external IP, not the ClusterIP that Galaxy pods use internally.
- **Example**: `10.150.0.60` (LoadBalancer external IP - private within VPC)
- **Important**: This is different from the internal ClusterIP (e.g., `10.43.86.187`) that Galaxy pods use
- **How to find**: `kubectl get svc -n nfs-provisioner` and look for the EXTERNAL-IP of the LoadBalancer service

#### **GCP Service Account Key File**
- **Galaxy Label**: "GCP Service Account Key File"
- **Format**: JSON file
- **Description**: Upload the JSON key file for a GCP service account with Batch API permissions
- **Required Permissions**:
  - Batch Job Editor role (or equivalent permissions)
  - Access to the specified network and subnet
- **How to Create**:
  1. Go to GCP Console → IAM & Admin → Service Accounts
  2. Create a new service account or select existing one
  3. Assign "Batch Job Editor" role
  4. Create and download JSON key

### Optional Parameters

#### **GCP Project ID**
- **Galaxy Label**: "GCP Project ID"
- **Description**: The ID of the GCP project where the Batch job should be created
- **Auto-extraction**: If left blank, the project ID is automatically extracted from the service account key file
- **Example**: `my-galaxy-project`

## Using the Tool in Galaxy

### What Happens

The tool will:
1. **Submit a GCP Batch job** using a custom VM image (galaxy-k8s-boot-v2025-08-12) with CVMFS client pre-installed
2. **Trigger CVMFS mount** on the host VM by accessing the Galaxy data repository
3. **Run a container** with bind-mounted CVMFS from the host to test comprehensive connectivity
4. **Test NFS connectivity** including:
   - Basic network connectivity to port 2049
   - NFS volume mounting via GCP Batch (avoiding container capability issues)
   - Directory listing and dynamic PVC discovery
   - Search for actual Galaxy directories (jobs_directory, shed_tools, objects, tools, cache)
   - Write access testing to verify Batch job permissions
5. **Test CVMFS access** including:
   - Repository accessibility and directory listing
   - Specific file access (Arabidopsis TAIR10 reference genome)
   - Galaxy reference data directory validation
6. **Comprehensive network diagnostics** including:
   - DNS resolution testing
   - Network routing analysis
   - External connectivity verification
7. **Generate detailed report** with success/failure status and troubleshooting information

## Setup Requirements

Before using this tool in Galaxy, ensure you have:

### GCP Prerequisites
- A GCP project with the Batch API enabled
- A VPC network and subnet where both Galaxy and the NFS server can communicate
- A service account with "Batch Job Editor" role
- Downloaded JSON key file for the service account
- Access to the custom VM image: e.g., `galaxy-k8s-boot-v2025-08-12`

### Network Configuration
- Firewall rule allowing traffic from the Batch subnet to NFS server:
```
gcloud compute firewall-rules create allow-nfs-from-batch \
  --network=NETWORK_NAME \
  --allow=tcp:2049
```

### NFS Server Setup
- The NFS service must be accessible via LoadBalancer with external IP (typically private within VPC)
- NFS server should support NFSv4.2 with sec=sys security
- Exports should be configured to allow access from GCP Batch subnet
```
apiVersion: v1
kind: Service
metadata:
  name: nfs-provisioner-nfs-server-provisioner
  namespace: nfs-provisioner
  annotations:
    cloud.google.com/load-balancer-type: "Internal"
spec:
  type: LoadBalancer
  ports:
  - name: nfs
    port: 2049
    protocol: TCP
```

### CVMFS Requirements
- The custom VM image includes a pre-configured CVMFS client
- CVMFS repositories are mounted via autofs on first access
- Galaxy data repository (`data.galaxyproject.org`) should be accessible
- Network connectivity to CVMFS stratum servers

## Key Features

### NFS Volume Mounting
The tool uses GCP Batch's native NFS volume support instead of attempting to mount from within containers. This approach:
- **Eliminates capability issues**: No need for privileged containers or CAP_SYS_ADMIN
- **Improves reliability**: Batch handles the mount with full host privileges
- **Simplifies debugging**: Clear separation between network connectivity and mount issues

### Dynamic Galaxy Directory Discovery
The tool automatically discovers Galaxy installations regardless of PVC structure:
- **Smart PVC detection**: Finds all `pvc-*` directories under `/export`
- **Flexible directory mapping**: Adapts to actual Galaxy directory structure (`jobs_directory`, `shed_tools`, etc.)
- **Comprehensive validation**: Tests both read and write access to Galaxy directories
- **Detailed reporting**: Shows directory contents, sizes, and file counts for verification

### Comprehensive Testing
- **Network connectivity**: Basic port testing and DNS resolution
- **NFS functionality**: Mount verification and directory access
- **CVMFS access**: Repository mounting and file availability
- **Galaxy structure**: Specific directory and file validation
- **Write permissions**: Actual file creation tests to verify Batch job capabilities
