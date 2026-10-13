#!/bin/bash
# Without Bluetooth: connect the phone over USB as mass storage, the card is /dev/sda1.
mkdir -p ~/mnt && sudo mount /dev/sda1 ~/mnt && sudo cp "$(dirname "$0")"/dist/* ~/mnt/. && sudo umount ~/mnt
