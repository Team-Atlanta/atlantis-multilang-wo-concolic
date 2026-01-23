FROM ghcr.io/aixcc-finals/base-runner:v1.3.0
EXPOSE 9909

WORKDIR /tmp

ENV JOERN_INSTALL_DIR=/joern
ENV JOERN_VERSION=v4.0.258

RUN apt-get update && \
    apt-get install -y openjdk-21-jdk libssl1.1 curl && \
    apt-get clean

RUN apt-get install -y pigz

RUN apt update && apt install -y curl git build-essential libssl-dev zlib1g-dev libffi-dev\
    sqlite3 libsqlite3-dev xxd \
    && curl https://pyenv.run | bash

RUN apt-get install -y --reinstall libssl1.1 libcurl4 && \
    ln -sf /usr/lib/x86_64-linux-gnu/libssl.so.1.1 /usr/local/lib/libssl.so.1.1 && \
    ln -sf /usr/lib/x86_64-linux-gnu/libcrypto.so.1.1 /usr/local/lib/libcrypto.so.1.1 && \
    ldconfig

ENV PATH="/root/.pyenv/bin:/root/.pyenv/shims:$PATH"
RUN pyenv install 3.10.14 && pyenv global 3.10.14

# Build libCRS
COPY ./libs/libCRS /home/crs/libs/libCRS
RUN pip3 install /home/crs/libs/libCRS

RUN wget https://github.com/joernio/joern/releases/download/$JOERN_VERSION/joern-install.sh --no-check-certificate
RUN chmod +x joern-install.sh
RUN ./joern-install.sh --install-dir=$JOERN_INSTALL_DIR --version=$JOERN_VERSION --reinstall

COPY ./joern/requirements.txt /requirements.txt
RUN pip3 install -r /requirements.txt

COPY bin/* /usr/local/bin/

COPY ./joern/mljoern/server.py /server.py
COPY ./joern/mljoern/client.py /client.py


ENTRYPOINT ["/bin/bash", "/usr/local/bin/run_joern"]
