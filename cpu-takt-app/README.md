# CPU-Mindesttakt

Setzt beim Start des Servers den Mindesttakt aller CPU-Kerne (`scaling_min_freq`) und beendet sich danach.
Der Regler (`schedutil`) bleibt; im Leerlauf schlafen die Kerne weiter, unter Last geht der Takt wie bisher
bis zum Höchsttakt. Nach einem Neustart des Servers gilt wieder der Standard, bis die App erneut gelaufen ist.

## Warum

Der Server taktet im Leerlauf auf 0,8 GHz herunter. Kurze Anfragen (z. B. Fußwege von Valhalla für RWaysTH,
~5–20 ms Rechenzeit) sind vorbei, bevor der Regler hochtaktet, und laufen dadurch 3–4× langsamer.
Messung 27.10.2026 (RWaysTH, erste/letzte Meile, 120 Anfragen von außen):

| Mindesttakt | Median | p95 |
|---|---|---|
| 800 MHz (Standard) | 55 ms | 70 ms |
| 1600 MHz | 39 ms | 45 ms |
| **2000 MHz** | **31 ms** | **38 ms** |
| 2400 MHz | 26 ms | 29 ms |
| hochgetaktet (Last) | 19 ms | 27 ms |

2000 MHz ist der Mittelweg: Ziel (< 50 ms) mit Reserve, ohne die CPU dauerhaft auf vollen Takt zu zwingen.

## Optionen

| Option | Bedeutung |
|---|---|
| `min_mhz` | Mindesttakt in MHz (800 = Standard, 3900 = immer voller Takt) |

Die App braucht die Fähigkeit `SYS_ADMIN` und läuft ohne AppArmor-Profil, weil `/sys` im Container sonst
schreibgeschützt ist; sie hängt dafür ein eigenes sysfs ein. Sie läuft nur einmal kurz beim Start, hat keinen
Gerätezugriff und öffnet keine Ports.
