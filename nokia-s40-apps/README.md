# nokia-s40-apps

Small Java ME apps (CLDC 1.1 / MIDP 2.0) for a Nokia 6303i classic (Series 40) that talk to today's
world through their own bridge running in Home Assistant. The bridges live in
[Lord3n20/server-homeassistant](https://github.com/Lord3n20/server-homeassistant).
The apps themselves are in German.

| App | Folder | Bridge | What |
|---|---|---|---|
| 1 Signal | `signal/` | `nokia-signal-app` | Read and write Signal messages, view and send pictures, take photos with the camera. Encrypted (ChaCha20 + HMAC), because the phone cannot do modern TLS. |
| 2 Home Assistant | `homeassistant/` | `nokia-bridge-app` | A Home Assistant dashboard as text and small graphs. |

## Building

You need Java, `jar`, `openssl`, `curl` and the MIDP/CLDC libraries from
[MicroEmulator](https://github.com/barteo/microemu) (on Arch: AUR package `microemulator`; other
location via `MICROEMU_LIB=/path/to/lib`). The compiler (ecj 4.5.1) is downloaded from Maven
Central on the first build and checked by SHA-256.

    signal/build.sh          # → signal/dist/Signal.jad + Signal.jar
    homeassistant/build.sh

`ecj -target cldc1.1` writes classes with StackMap, so no `preverify` step is needed.

## Your own data: `private/`

Everything personal goes into `private/`, which is excluded by `.gitignore`:

| File | Purpose |
|---|---|
| `signal-url.txt`, `signal-key.txt` | URL and key of the Signal bridge, baked in as `SG-Url` / `SG-Key` |
| `homeassistant-url.txt`, `homeassistant-key.txt` | the same for the Home Assistant bridge (`HA-Url` / `HA-Key`) |
| `certificate/certificate.pem`, `certificate/key.pem` | own certificate for signing (optional) |
| `gammu-bluetooth.rc` | gammu settings for uploading, template in `tools/gammu-bluetooth.rc.example` |

Without these files the apps still build; URL and key are then typed into the settings on the
phone, and the apps are unsigned.

## Getting it onto the phone

    ./upload.sh signal     # over Bluetooth with gammu, reads the files back to check

Close the app on the phone first. gammu's final "file does not exist" message is wrong on the 6303i.
Without Bluetooth: connect the phone over USB as mass storage and run `<app>/COPY.sh`.
Signed apps need the `.jad` and the `.jar` on the card together; copy both again after every build.

## Signing, and what the 6303i allows

Unsigned apps ask for permission on every network access. An own certificate stops that: put the
certificate (DER) into the phone's folder `d:/predefhiddenfolder/certificates/user`, and make its
entry in `ext_info.sys` list the usage "code signing" (OID 1.3.6.1.5.5.7.3.3). After that the
phone trusts apps signed with this certificate.

Limits that stay even when signed:

- Java apps do not run in the background; `Nokia-MIDlet-No-Exit` is ignored.
- Auto start (PushRegistry alarm) and network access cannot both be set to "always allowed"
  (a MIDP 2 security rule), so waking the app up always shows a prompt.
- No access to the call log, the cell ID or the location.

That is why the Signal bridge notifies about new messages with a short SIP call (e.g. through an
IP phone on a FritzBox) and hangs up before anyone answers.

## License

Public domain ([Unlicense](LICENSE)): do whatever you want with it.
