FROM google/cloud-sdk:latest

# Install essential networking and debugging tools
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    netcat-openbsd \
    iproute2 \
    iputils-ping \
    dnsutils \
    curl \
    wget \
    telnet \
    traceroute \
    net-tools \
    tcpdump \
    nmap \
    vim \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages google-cloud-batch

RUN groupadd -g 10001 galaxy && useradd -u 10001 -g 10001 -m -s /bin/bash galaxy

# Add bash alias for ll
RUN echo "alias ll='ls -l'" >> /home/galaxy/.bashrc && \
    chown galaxy:galaxy /home/galaxy/.bashrc

USER galaxy
