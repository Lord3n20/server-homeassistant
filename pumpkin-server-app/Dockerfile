FROM alpine:3.22

# Server binary and WASM plugins from the Stierzunge release of the Pumpkin fork
ARG PUMPKIN_RELEASE=stierzunge-0.2.0-2
RUN apk add --no-cache bash jq python3 tzdata ca-certificates \
    && mkdir -p /opt/pumpkin/plugins && cd /opt/pumpkin \
    && base="https://github.com/Lord3n20/pumpkin-fork/releases/download/${PUMPKIN_RELEASE}" \
    && for f in checksums.sha256 pumpkin-X64-Linux-musl pumpkin_voice.wasm pumpkin_pregen.wasm; do \
         wget -q "$base/$f"; done \
    && sha256sum -c checksums.sha256 \
    && mv pumpkin-X64-Linux-musl pumpkin && chmod +x pumpkin && mv ./*.wasm plugins/ && rm checksums.sha256

COPY run.sh konfig.py konsole.py /
RUN chmod +x /run.sh
ENTRYPOINT [ "/run.sh" ]
