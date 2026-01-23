# Build on top of parent_image - source code is already at /src
ARG parent_image
FROM ${parent_image}

ARG CRS_TARGET
ENV PROJECT_NAME=${CRS_TARGET}

ENV TZ=US \
    DEBIAN_FRONTEND=noninteractive

# Install additional dependencies for run.py
# Docker CLI needed for run.py build_crs (builds internal CRS images)
RUN apt-get update -y && apt-get install -y \
    python3-pip curl pigz rsync \
    ca-certificates gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update -y \
    && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install coloredlogs pyyaml python-on-whales

# Copy full crs-multilang source
COPY . /crs-multilang

# Copy oss-fuzz project files from additional_contexts (provided by oss-crs)
COPY --from=project . /crs-multilang/libs/oss-fuzz/projects/${CRS_TARGET}/

# Keep WORKDIR as parent's (e.g., /src/PROJECT_NAME) - oss-crs may copy local source here
# build.sh does `cd /crs-multilang` to run CRS code

COPY oss-crs/build.sh /build.sh
RUN chmod +x /build.sh

CMD ["/build.sh"]
