#!/bin/sh

# 使用传参
if [ "$1" != "" ]; then
    wpa_conf=$1

# 使用环境变量
elif [ "${env_wifi_wpa_supplicant_conf}" != "" ]; then
    wpa_conf=${env_wifi_wpa_supplicant_conf}

# 使用默认配置
elif [ -f /usr/data/wpa_supplicant.conf ]; then
    wpa_conf=/usr/data/wpa_supplicant.conf
fi

# plan B2: the boot-time MAC setup is backgrounded; make sure the MAC is
# set now, while the interface is still down.
wifi_set_mac.sh start >/dev/null 2>&1

rfkill unblock wifi
ifconfig wlan0 up

if [ "$wpa_conf" != "" ]; then
    wpa_supplicant -B -i wlan0 -c $wpa_conf &
    udhcpc -i wlan0 &
fi
