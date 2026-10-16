#!/bin/sh
# plan C3: TF readahead. mdev fires this on mmcblk* add events (card
# insert at boot or hotplug); the queue knob lives on the parent device,
# so mmcblk1p1 maps to mmcblk1's queue. Kernel default is 128 KiB.
dev=${MDEV%%p*}
q="/sys/class/block/$dev/queue/read_ahead_kb"
[ -e "$q" ] && echo 512 > "$q"
exit 0
