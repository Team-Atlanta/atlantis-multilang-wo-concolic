ARG target_base_image
FROM atlantis-multilang-wo-concolic-deps:latest AS multilang-deps
FROM oss-crs-deps:latest AS oss-crs-deps

FROM ${target_base_image}
EXPOSE 3303

COPY --from=multilang-deps /nix/store /nix/store
COPY --from=multilang-deps /usr/local/bin/ /usr/local/bin/
COPY --from=multilang-deps /opt/atlantis-lsp /opt/atlantis-lsp
RUN ln -sfn /opt/atlantis-lsp/eclipse-jdtls /tmp/eclipse-jdtls

COPY bin/* /usr/local/bin/
COPY ./lsp/main.py /main.py

COPY --from=oss-crs-deps /nix/store /nix/store
COPY --from=oss-crs-deps /usr/local/bin/libCRS /usr/local/bin/libCRS
COPY --from=oss-crs-deps /usr/local/bin/rsync /usr/local/bin/rsync

ENTRYPOINT ["/bin/bash", "/usr/local/bin/run_lsp"]
