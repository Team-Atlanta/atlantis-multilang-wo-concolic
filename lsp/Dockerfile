FROM ghcr.io/aixcc-finals/base-builder:v1.3.0
EXPOSE 3303

RUN apt update && \
    apt upgrade -y && \
    apt install -y pigz socat

RUN apt update && apt install -y curl git build-essential libssl-dev zlib1g-dev libffi-dev uuid-runtime\
    sqlite3 libsqlite3-dev xxd \
    && curl https://pyenv.run | bash

ENV PATH="/root/.pyenv/bin:/root/.pyenv/shims:$PATH"
RUN pyenv install 3.11.8 && pyenv global 3.11.8

# Build libCRS
COPY ./libs/libCRS /home/crs/libs/libCRS
RUN pip3 install /home/crs/libs/libCRS

# Build multilspy
COPY ./libs/multilspy /home/crs/libs/multilspy
RUN pip3 install /home/crs/libs/multilspy

COPY ./lsp/download.py /download.py

RUN chmod +x /download.py
RUN python3 /download.py

COPY bin/* /usr/local/bin/
COPY ./lsp/main.py /main.py

ENTRYPOINT ["/bin/bash", "/usr/local/bin/run_lsp"]
