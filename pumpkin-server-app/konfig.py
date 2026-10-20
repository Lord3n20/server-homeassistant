#!/usr/bin/env python3
"""Writes /data/pumpkin.toml from the add-on options (HA UI) and puts the plugins in place.

Pumpkin adds every value missing here with its default when it starts, so only the
settings that differ from Pumpkin's defaults (or come from the options) are listed.
"""
import json
import os
import secrets
import shutil

DATA = "/data"
PLUGINS = "/opt/pumpkin/plugins"
RCON_PORT = 25575


def toml(value):
    """TOML literal for a bool, number, string or list of strings."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml(v) for v in value) + "]"
    # JSON escaping without \u surrogates is valid for TOML basic strings
    return json.dumps(str(value), ensure_ascii=False)


def seed_secret(welt):
    """Secret of this world; new worlds get one when seed protection is on.

    The file decides, not the option: changing the secret of an existing world would
    move structures in all chunks generated afterwards.
    """
    path = os.path.join(DATA, welt, "stierzunge-seed-secret")
    if os.path.isfile(path):
        with open(path) as f:
            return int(f.read().strip())
    if not opt("seed_schutz", True) or os.path.exists(os.path.join(DATA, welt, "level.dat")):
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    value = secrets.randbits(62) + 1
    with open(path, "w") as f:
        f.write(f"{value}\n")
    print(f"Neue Welt {welt}: Seed-Schutz eingerichtet ({path})")
    return value


def plugins():
    os.makedirs(os.path.join(DATA, "plugins"), exist_ok=True)
    wanted = {"pumpkin_pregen.wasm"} | ({"pumpkin_voice.wasm"} if opt("voice_chat", True) else set())
    for name in os.listdir(PLUGINS):
        target = os.path.join(DATA, "plugins", name)
        if name in wanted:
            shutil.copyfile(os.path.join(PLUGINS, name), target)
        elif os.path.exists(target):
            os.remove(target)


with open(os.path.join(DATA, "options.json")) as f:
    OPTIONS = json.load(f)


def opt(key, default):
    value = OPTIONS.get(key)
    return default if value is None else value


welt = opt("welt", "world")
rate = opt("rate_limit", 500)
sections = {
    "": {
        "default_level_name": welt,
        "default_difficulty": opt("schwierigkeit", "Normal"),
        "white_list": True,
        "enforce_whitelist": opt("enforce_whitelist", True),
        "enable_status": opt("enable_status", False),
    },
    "logging": {"color": False},
    "world": {"seed_secret": seed_secret(welt)},
    "networking.rcon": {
        "enabled": True,
        "address": f"127.0.0.1:{RCON_PORT}",
        "password": os.environ["RCON_PASSWORD"],
    },
    "networking.rcon.logging": {"logged_successfully": False, "quit": False},
    "networking.java": {
        "address": "0.0.0.0:25565",
        "motd": opt("motd", "Stierzunge"),
        "max_players": opt("max_players", 10),
        "view_distance": opt("view_distance", 12),
        "simulation_distance": opt("simulation_distance", 8),
    },
    "networking.java.authentication": {
        "prevent_proxy_connections": opt("prevent_proxy_connections", True),
    },
    "networking.java.packet_limiter": {
        "enabled": rate > 0,
        "max_packet_rate": float(max(rate, 1)),
        "burst_capacity": float(max(rate, 1)),
    },
    "networking.bedrock": {"enabled": False},
    "commands": {"use_console": False, "use_tty": False},
    "server_links": {"enabled": False},
    "telemetry": {"enabled": False},
    # No TTY for the permission prompt: grant exactly what the bundled plugins need
    "plugins": {
        "ask_permission_confirmation": False,
        "allowed_permissions": [
            "network.udp", "network.udp.bind", "network.udp.connect",
            "network.udp.outgoingdatagram", "network.outbound", "network.dns",
            "fs.read.data", "fs.write.data",
        ],
    },
    "anticheat": {"enabled": opt("anticheat", True)},
    "anti_xray": {"enabled": opt("anti_xray", True)},
}

lines = ["# Written by the add-on on every start – change the options in Home Assistant instead"]
for name, values in sections.items():
    if name:
        lines.append(f"\n[{name}]")
    lines += [f"{key} = {toml(value)}" for key, value in values.items()]
with open(os.path.join(DATA, "pumpkin.toml"), "w") as f:
    f.write("\n".join(lines) + "\n")

plugins()
