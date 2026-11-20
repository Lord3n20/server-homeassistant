# KI (colibri)

Lokale KI für die Budget-App: [colibri](https://github.com/JustVugg/colibri) (Apache-2.0) mit
Qwen3.6-35B-A3B (Apache-2.0) als GGUF `UD-IQ3_XXS` von unsloth (13,2 GB). Erreichbar nur im App-Netz
(`http://91c9b2fc-colibri:8100`), kein Port nach außen.

## Was die App tut

- Beim ersten Start lädt sie das Modell nach `/share/colibri/` (nicht im HA-Backup) und verlinkt es mit
  `coli import` (es wird nichts kopiert). Stand unter `GET /status`.
- `POST /v1/chat/completions` wie OpenAI, eine Anfrage nach der anderen. Die Engine startet erst bei einer Anfrage
  und hört nach `leerlauf_minuten` ohne Anfrage wieder auf. Ohne Anfrage braucht die App kaum RAM.

## Drosselung

| Option | Vorgabe | Wirkung |
|---|---|---|
| `ram_reserve_gb` | 1.5 | Start nur, wenn danach noch so viel frei bleibt (Bedarf ≈ 2,8 GB). Fällt der freie RAM während des Laufs unter 60 % davon, wird die Engine sofort beendet und die Anfrage scheitert. |
| `threads` | 2 | CPU-Threads der Engine (dazu `nice 19`, `ionice idle`). |
| `experten_je_schicht` | 4 | Experten pro Schicht im RAM; der Rest kommt bei Bedarf von der SSD (nur Lesen). Mehr = schneller, aber mehr RAM (≈ 4,3 MB je Schritt über 40 Schichten). |
| `leerlauf_minuten` | 10 | Danach wird die Engine beendet. |

Gemessen auf dem Laptop mit 2 Kernen: 12 Bankumsätze einordnen ≈ 35 s, Monatsbericht ≈ 10 min, 2,5 GB RSS.
Auf dem OptiPlex (i7-4770S, SATA-SSD) ist es langsamer; der Monatsbericht darf Stunden dauern.

## Bauen

Das Dockerfile holt colibri auf einem festen Commit und baut nur die Qwen3.6-Engine (`make qwen36 ARCH=x86-64-v3`).
