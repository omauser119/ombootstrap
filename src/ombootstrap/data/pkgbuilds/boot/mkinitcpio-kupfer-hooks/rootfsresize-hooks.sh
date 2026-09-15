#!/usr/bin/ash

run_hook() {
  printf 'rootfs-resize\n' > /run/initramfs-stage
  # rootfsdetect must be run first, or otherwise a root variable must be defined in the boot cmdline
  if [ -z "${root:-}" ]; then
    echo "root variable undefined. Aborting..."
    initramfs_error=rootfs-not-found
    printf 'rootfs-not-found\n' > /run/initramfs-stage
    return 1
  fi

  # Sets $LOOPDEV and $SUBPARTNUMBER
  get_loopdev_partinfo $root

  # Check if root is inside of a nested partition table and fall over to resizing fs if not
  if [ -n $LOOPDEV ] && [ -n $SUBPARTNUMBER ] ; then
    if parted -s "$LOOPDEV" print free | tail -n2 | head -n1 | grep -qi "free space"; then
      echo "Found unallocated space on a subpartition. Resizing..."
      parted -s "$LOOPDEV" resizepart "$SUBPARTNUMBER" 100%
      partprobe
    fi
  fi

  unset LOOPDEV
  unset SUBPARTNUMBER

  # Filesystem checks are intentionally disabled for this fast boot image.
  if ! resize2fs "$root"; then
    initramfs_error=resize-error
    printf 'resize-error\n' > /run/initramfs-stage
    return 1
  fi
  printf 'rootfs-ready\n' > /run/initramfs-stage
}
