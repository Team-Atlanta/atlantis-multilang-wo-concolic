# syntax=docker/dockerfile:1
ARG NIX_BUILDER_IMAGE=nixos/nix@sha256:e623d73af9cac82d1b50784c83e0cf2a4b83bfd2cfe8d5b67809a2fc94e043ac
FROM ${NIX_BUILDER_IMAGE} AS build

COPY oss-crs/deps /build/oss-crs/deps
COPY libs/libCRS /build/oss-crs/deps/libs/libCRS
COPY libs/multilspy /build/oss-crs/deps/libs/multilspy

RUN nix build /build/oss-crs/deps#default -o /out/runtime \
      --extra-experimental-features 'nix-command flakes' \
 && mkdir -p /rootfs/nix/store /rootfs/usr/local/bin /rootfs/opt/atlantis-lsp \
 && for p in $(nix-store -qR /out/runtime); do cp -a "$p" /rootfs/nix/store/; done \
 && runtime=$(readlink -f /out/runtime) \
 && ln -s "$runtime/bin/python3" /rootfs/usr/local/bin/python3 \
 && ln -s "$runtime/bin/python3" /rootfs/usr/local/bin/lsp-python \
 && for command in git pigz socat sqlite3 uuidgen xxd; do \
      ln -s "$runtime/bin/$command" "/rootfs/usr/local/bin/$command"; \
    done \
 && ln -s "$runtime/share/atlantis-lsp/eclipse-jdtls" \
      /rootfs/opt/atlantis-lsp/eclipse-jdtls

FROM scratch
COPY --from=build /rootfs/ /
