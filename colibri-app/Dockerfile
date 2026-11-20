# colibri (github.com/JustVugg/colibri, Apache-2.0) mit der Qwen3.6-Engine, dazu ki.py als Pförtner.
# Fester Stand, mit dem die App getestet wurde.
ARG COLIBRI_COMMIT=0a5504a1dc64f86fcd0fe7db298b0448c730e7e6

FROM debian:stable-slim AS bau
ARG COLIBRI_COMMIT
RUN apt-get update && apt-get install -y --no-install-recommends build-essential make curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fsSL "https://github.com/JustVugg/colibri/archive/${COLIBRI_COMMIT}.tar.gz" | tar xz --strip-components=1
# x86-64-v3 = AVX2/FMA, läuft auf dem i7-4770S (nicht "native", sonst passt das Image nur zum Baurechner).
RUN make -C c qwen36 ARCH=x86-64-v3 -j4 && rm -f c/*.o c/*.d

FROM debian:stable-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=bau /src/c /app/c
COPY --from=bau /src/LICENSE /src/NOTICE /app/
COPY ki.py /app/
CMD ["python3", "-u", "/app/ki.py"]
