# signal-cli as a native build (no Java needed), only published for x86_64.
FROM debian:trixie-slim

ARG SIGNAL_CLI_VERSION=0.14.8
ARG SIGNAL_CLI_SHA256=36569af20c709e0c5e6e677b74f50f147b21f3740620b8a7affde70f6027f82a

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pil python3-qrcode python3-cryptography \
        tzdata ca-certificates curl zlib1g \
    && curl -fsSL -o /tmp/signal-cli.tar.gz \
        "https://github.com/AsamK/signal-cli/releases/download/v${SIGNAL_CLI_VERSION}/signal-cli-${SIGNAL_CLI_VERSION}-Linux-native.tar.gz" \
    && echo "${SIGNAL_CLI_SHA256}  /tmp/signal-cli.tar.gz" | sha256sum -c - \
    && tar xzf /tmp/signal-cli.tar.gz -C /usr/local/bin \
    && rm /tmp/signal-cli.tar.gz \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY bridge.py /bridge.py

EXPOSE 8080 8099
CMD ["python3", "-u", "/bridge.py"]
