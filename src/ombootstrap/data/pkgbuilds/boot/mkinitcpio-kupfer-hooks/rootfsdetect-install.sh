#!/bin/bash

build() {
  add_binary losetup
  # fdisk is provided by the Kupfer BusyBox and is used as a fallback when
  # blkid cannot expose the nested DOS partition table.
  add_file /etc/kupfer/deviceinfo
  add_file /usr/lib/initcpio/hooks/kupfer-functions.sh

  add_runscript
}

help() {
  cat <<HELPEOF
This hook detects the rootfs image
HELPEOF
}
