#!/bin/sh
# Setzt scaling_min_freq aller CPU-Policies auf min_mhz (höchstens den Höchsttakt) und beendet sich.
MHZ=$(grep -o '"min_mhz": *[0-9]*' /data/options.json | grep -o '[0-9]*$')
[ -n "$MHZ" ] || MHZ=2000
SYS=/sys
if ! [ -w /sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq ]; then  # /sys schreibgeschützt eingehängt
  SYS=/tmp/sys
  mkdir -p $SYS && mount -t sysfs sysfs $SYS || { echo "Fehler: sysfs nicht beschreibbar einhängbar"; exit 1; }
fi
n=0
for p in $SYS/devices/system/cpu/cpufreq/policy*; do
  max=$(cat "$p/cpuinfo_max_freq")
  khz=$((MHZ * 1000))
  [ "$khz" -gt "$max" ] && khz=$max
  if ! echo "$khz" > "$p/scaling_min_freq"; then
    echo "Fehler: $p/scaling_min_freq nicht schreibbar"
    exit 1
  fi
  n=$((n + 1))
done
p=$SYS/devices/system/cpu/cpufreq/policy0
echo "Mindesttakt $(($(cat $p/scaling_min_freq) / 1000)) MHz für $n Kerne gesetzt (Regler $(cat $p/scaling_governor), Höchsttakt $(($(cat $p/cpuinfo_max_freq) / 1000)) MHz)"
