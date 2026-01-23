# Build jazzer
FROM ghcr.io/aixcc-finals/base-runner:v1.3.0 AS tracer-jazzer-builder

RUN apt-get update && apt-get install -y apt-transport-https curl gnupg && \
curl -fsSL https://bazel.build/bazel-release.pub.gpg | gpg --dearmor > /usr/share/keyrings/bazel-archive-keyring.gpg && \
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/bazel-archive-keyring.gpg] https://storage.googleapis.com/bazel-apt stable jdk1.8" | tee /etc/apt/sources.list.d/bazel.list

RUN apt-get update && apt-get install -y maven bazel-7.3.0

RUN ln -s /usr/bin/bazel-7.3.0 /usr/bin/bazel

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV JAVA_15_HOME=/usr/lib/jvm/java-15-openjdk-amd64
ENV JVM_LD_LIBRARY_PATH=$JAVA_HOME/lib/server
ENV PATH=$PATH:$JAVA_HOME/bin

ENV CRS_SARIF_TRACER_JAZZER_SRC=/jazzer

RUN useradd -m jazzer_user
COPY ./jazzer $CRS_SARIF_TRACER_JAZZER_SRC

RUN chown -R jazzer_user:jazzer_user $CRS_SARIF_TRACER_JAZZER_SRC

USER jazzer_user

WORKDIR $CRS_SARIF_TRACER_JAZZER_SRC

RUN echo "build --java_runtime_version=local_jdk_17" >> .bazelrc && \
    echo "build --cxxopt=-stdlib=libc++" >> .bazelrc && \
    echo "build --linkopt=-lc++" >> .bazelrc

RUN bazel build \
    //src/main/java/com/code_intelligence/jazzer:jazzer_standalone_deploy.jar \
    //deploy:jazzer-api \
    //deploy:jazzer-junit \
    //launcher:jazzer


USER root
RUN mkdir /jazzer_output
RUN chmod 777 /jazzer_output
USER jazzer_user

RUN cp $(bazel cquery --output=files //src/main/java/com/code_intelligence/jazzer:jazzer_standalone_deploy.jar) /jazzer_output/jazzer_standalone_deploy.jar
RUN cp $(bazel cquery --output=files //launcher:jazzer) /jazzer_output/jazzer_driver
RUN cp $(bazel cquery --output=files //deploy:jazzer-api) /jazzer_output/jazzer_api_deploy.jar
RUN cp $(bazel cquery --output=files //deploy:jazzer-junit) /jazzer_output/jazzer_junit.jar


# Build tracer image
FROM ghcr.io/aixcc-finals/base-runner:v1.3.0

RUN apt-get update && apt-get install -y python3 python3-pip
RUN python3 -m pip install loguru pydantic

# RUN ln -s /usr/bin/bazel-7.3.0 /usr/bin/bazel

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV JAVA_15_HOME=/usr/lib/jvm/java-15-openjdk-amd64
ENV JVM_LD_LIBRARY_PATH=$JAVA_HOME/lib/server
ENV PATH=$PATH:$JAVA_HOME/bin
ENV CRS_SARIF_TRACER_JAZZER_API_PATH="/usr/local/lib/jazzer_api_deploy.jar"
ENV CRS_SARIF_TRACER_JAZZER_JUNIT_PATH="/usr/local/bin/jazzer_junit.jar"
ENV CRS_SARIF_TRACER_JAZZER_DRIVER_PATH="/usr/local/bin/jazzer_driver"
ENV CRS_SARIF_TRACER_JAZZER_AGENT_PATH="/usr/local/bin/jazzer_agent_deploy.jar"


COPY --from=tracer-jazzer-builder /jazzer_output/jazzer_standalone_deploy.jar $CRS_SARIF_TRACER_JAZZER_AGENT_PATH
COPY --from=tracer-jazzer-builder /jazzer_output/jazzer_driver $CRS_SARIF_TRACER_JAZZER_DRIVER_PATH
COPY --from=tracer-jazzer-builder /jazzer_output/jazzer_api_deploy.jar $CRS_SARIF_TRACER_JAZZER_API_PATH
COPY --from=tracer-jazzer-builder /jazzer_output/jazzer_junit.jar $CRS_SARIF_TRACER_JAZZER_JUNIT_PATH

# USER root


# Tracer Utils

ENV CRS_SARIF_TRACER_TRACER_PATH=/tracer
ENV CRS_SARIF_TRACER_TRACER_WORKDIR=/tracer_workdir

COPY ./src /$CRS_SARIF_TRACER_TRACER_PATH
RUN mkdir $CRS_SARIF_TRACER_TRACER_WORKDIR

WORKDIR $CRS_SARIF_TRACER_TRACER_PATH

CMD ["python3", "run.py"]
