#!/bin/sh

# plan B6: the batd branch is dead (no batd binary in the rootfs), the
# second killall -9 is redundant noise at boot, and the player's own
# debug stream (MGR/GUI thread prints, the lg_dlna loop) goes to the
# 3 Mbaud console — plan B5 disconnects it from the serial fds.

killall hiby_player &>/dev/null

read UP _ < /proc/uptime && echo "[boot] player exec $UP"
# plan D1: the player owns the UI; keep it ahead of the background
# daemons (root may set negative nice).
nice -n -10 /usr/bin/hiby_player >/dev/null 2>&1
sleep 1
reboot
