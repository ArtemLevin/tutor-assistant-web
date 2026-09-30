FROM alpine:3.22 AS fetch

ARG TARGETARCH
ARG MINIO_RELEASE=RELEASE.2025-04-22T22-12-26Z
ARG MC_RELEASE=RELEASE.2025-04-16T18-13-26Z
ARG MINIO_MINISIGN_PUBKEY=RWTx5Zr1tiHQLwG9keckT0c45M3AGeHD6IvimQHpyRywVWGbP1aVSGav

RUN apk add --no-cache ca-certificates curl minisign

RUN set -eux; \
    arch="${TARGETARCH:-}"; \
    if [ -z "$arch" ]; then arch="$(uname -m)"; fi; \
    case "$arch" in \
      amd64|x86_64) arch=amd64 ;; \
      arm64|aarch64) arch=arm64 ;; \
      ppc64le) arch=ppc64le ;; \
      s390x) arch=s390x ;; \
      *) echo "Unsupported MinIO architecture: $arch" >&2; exit 1 ;; \
    esac; \
    mkdir -p /out; \
    curl -fsSL --retry 3 \
      "https://github.com/minio/minio/releases/download/${MINIO_RELEASE}/minio.linux-$arch.${MINIO_RELEASE}" \
      -o /out/minio; \
    curl -fsSL --retry 3 \
      "https://github.com/minio/minio/releases/download/${MINIO_RELEASE}/minio.linux-$arch.${MINIO_RELEASE}.minisig" \
      -o /out/minio.minisig; \
    curl -fsSL --retry 3 \
      "https://github.com/minio/mc/releases/download/${MC_RELEASE}/mc.linux-$arch.${MC_RELEASE}" \
      -o /out/mc; \
    curl -fsSL --retry 3 \
      "https://github.com/minio/mc/releases/download/${MC_RELEASE}/mc.linux-$arch.${MC_RELEASE}.minisig" \
      -o /out/mc.minisig; \
    minisign -Vqm /out/minio -x /out/minio.minisig -P "${MINIO_MINISIGN_PUBKEY}"; \
    minisign -Vqm /out/mc -x /out/mc.minisig -P "${MINIO_MINISIGN_PUBKEY}"

FROM alpine:3.22

RUN apk add --no-cache ca-certificates

COPY --from=fetch /out/minio /usr/local/bin/minio
COPY --from=fetch /out/mc /usr/local/bin/mc

RUN chmod 0755 /usr/local/bin/minio /usr/local/bin/mc \
    && mkdir -p /data /tmp/.mc \
    && chmod 0777 /tmp/.mc

ENV MC_CONFIG_DIR=/tmp/.mc

EXPOSE 9000 9001
VOLUME ["/data"]

ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data"]
