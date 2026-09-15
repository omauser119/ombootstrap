#!/usr/bin/ash

run_hook() {
  printf 'rootfs-detect\n' > /run/initramfs-stage
  source /usr/lib/initcpio/hooks/kupfer-functions.sh

  print_rootfs_type() {
    local probe fstype
    probe="$(blkid -p -o full "$1" 2>/dev/null)"
    fstype="$(echo "$probe" | sed -n 's/.* TYPE="\([^"]*\)".*/\1/p')"
    echo "Rootfs type: ${fstype:-unknown}"
  }

  # Some bootloaders mess with those options (e.g. OnePlus 6), therefore we need to overwrite them
  # This is not nice, but there is no other way (at least right now)
  export rwopt=rw
  export init=/sbin/init

  eval "$(cat /etc/kupfer/deviceinfo)"
  deviceinfo_partitions_microsd="$deviceinfo_partitions_microsd"
  deviceinfo_partitions_data="$deviceinfo_partitions_data"

  if [ "${root:-}" = "/dev/mapper/cryptroot" ]; then
    echo "rootfsdetect found root=/dev/mapper/cryptroot. Skipping..."
    return 0
  fi

  for _root in "$root" "$deviceinfo_partitions_microsd" "$deviceinfo_partitions_data" "/dev/disk/by-label/kupfer_root"; do
    if [ -e "$_root" ] && scan_partitions "$_root" "LABEL=kupfer_root"; then
      echo "Found kupfer_root at: $RESULT"
      print_rootfs_type "$RESULT"
      export root="$RESULT"
      printf 'rootfs-ready\n' > /run/initramfs-stage
      return 0
    fi
  done

  # Fall back to scanning for partitions

  echo -n "kupfer_root not found as a raw partition"
  [ -z "$deviceinfo_partitions_microsd" ] || echo -n " nor in data partition '$deviceinfo_partitions_microsd'"
  [ -z "$deviceinfo_partitions_data" ] || echo -n " nor in data partition '$deviceinfo_partitions_data'"
  echo -e ".\nScanning more partitions..."

  unset RESULT

  for part in /dev/sd?[0-9]* /dev/mmcblk[0-9]p* /dev/nvme[0-9]n[0-9]p* /dev/vd?[0-9]* /dev/hd?[0-9]* ; do
    ! scan_partitions "$part" "LABEL=kupfer_root" || break
  done

  if [ -n "$RESULT" ]; then
    echo "Found kupfer_root at: $RESULT (bruteforce)"
    print_rootfs_type "$RESULT"
    export root="$RESULT"
    printf 'rootfs-ready\n' > /run/initramfs-stage
  else
    echo "Failed to find a partition labeled kupfer_root."
    initramfs_error=rootfs-not-found
    printf 'rootfs-not-found\n' > /run/initramfs-stage
    unset root
  fi

  unset SUBPARTNUMBER
  unset LOOPDEV
}
