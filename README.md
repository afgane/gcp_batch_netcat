# GCP Batch Netcat Galaxy Tool

A Galaxy tool that submits a job to Google Cloud Platform (GCP) Batch service to test connectivity to an NFS server using `netcat`. This tool is predominantly intended for use with Galaxy deployments using the Galaxy Helm chart, where it can verify network connectivity between GCP Batch workers and NFS storage systems.

## Overview

This tool creates and submits a GCP Batch job that runs a simple network connectivity test to an NFS server using `netcat` (nc). It's particularly useful for:
- Testing network connectivity between GCP Batch compute nodes and NFS storage
- Validating that firewall rules allow communication on port 2049 (NFS)
- Troubleshooting connectivity issues in Galaxy deployments on Kubernetes

The tool is available in the Main Tool Shed at:
https://toolshed.g2.bx.psu.edu/view/enis/gcp_batch_netcat/

## For use with the Galaxy Helm Chart

This tool is specifically designed for Galaxy deployments using the Galaxy Helm chart on Google Kubernetes Engine (GKE). A sample deployment can be obtained using the [galaxy-k8s-boot repository](https://github.com/galaxyproject/galaxy-k8s-boot/).

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
- **Important**: The network must allow communication between Batch workers and the Galaxy NFS server

#### **GCP Subnet name**
- **Galaxy Label**: "GCP Subnet name"
- **Description**: The name of the subnet in which Galaxy runs
- **Examples**: `default`

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

#### **NFS Server Address**
- **Galaxy Label**: "NFS Server Address"
- **Description**: IP address or hostname of the NFS server to test connectivity to. This is the same address as Galaxy is using.
- **Auto-detection**: If not supplied, the tool attempts to detect the NFS server from Galaxy's database mount. This is the preferred mode of operation.
- **Example**: `10.0.0.100`
- **When to specify**: Use when auto-detection fails or when testing a different NFS server

#### **GCP Project ID**
- **Galaxy Label**: "GCP Project ID"
- **Description**: The ID of the GCP project where the Batch job should be created
- **Auto-extraction**: If left blank, the project ID is automatically extracted from the service account key file
- **Example**: `my-galaxy-project`

## Using the Tool in Galaxy

### What Happens

The tool will:
- Submit a lightweight job to GCP Batch in your specified region and network
- Test connectivity to the NFS server on port 2049 using `netcat`
- Return a report showing whether the connection was successful

## Setup Requirements

Before using this tool in Galaxy, ensure you have:

### GCP Prerequisites
- A GCP project with the Batch API enabled
- A VPC network and subnet where both Galaxy and the NFS server can communicate
- A service account with "Batch Job Editor" role
- Downloaded JSON key file for the service account

### Network Configuration
- Firewall rule allowing traffic from the Batch subnet to NFS server on port 2049 for the specified network:
```
gcloud compute firewall-rules create allow-nfs-from-batch \
  --network=NETWORK_NAME \
  --allow=tcp:2049
```

### NFS server Setup
- The Ganesha NFS service needs to use an internal LoadBalancer
```
apiVersion: v1
kind: Service
metadata:
  name: nfs-provisioner-nfs-server-provisioner
  namespace: nfs-provisioner
  annotations:
    cloud.google.com/load-balancer-type: "Internal"
  ...
spec:
  type: LoadBalancer
  ...
```
