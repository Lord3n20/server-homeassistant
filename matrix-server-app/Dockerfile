# Continuwuity (Rust Matrix homeserver) as a static binary from the official
# release, plus bash and jq for run.sh. Nothing is compiled on the HA host.
#
# To update: bump CONTINUWUITY_VERSION and both checksums here and `version` in config.yaml.
FROM alpine:3.22

ARG BUILD_ARCH=amd64
ARG CONTINUWUITY_VERSION=v26.9.1
ARG SHA256_AMD64=7beff208482cf9c7bb35c4a393cb7a7b2f241d2e2c0c7fe3c0b6a8d4add6b2d7
ARG SHA256_ARM64=f70e20f17a3032b37db3f00eee3814b125dd0f9ab8c5dc72812c8264dc2548c5

RUN apk add --no-cache bash jq ca-certificates tzdata \
    && case "$BUILD_ARCH" in \
        aarch64) ARCH=arm64; SHA256="$SHA256_ARM64" ;; \
        *) ARCH=amd64; SHA256="$SHA256_AMD64" ;; \
    esac \
    && wget -q -O /usr/bin/continuwuity \
        "https://forgejo.ellis.link/continuwuation/continuwuity/releases/download/${CONTINUWUITY_VERSION}/conduwuit-linux-static-${ARCH}" \
    && echo "${SHA256}  /usr/bin/continuwuity" | sha256sum -c - \
    && chmod 755 /usr/bin/continuwuity

COPY run.sh /run.sh
RUN chmod 755 /run.sh

EXPOSE 8008
ENTRYPOINT ["/run.sh"]
