#!/bin/bash

set -e

if [ "$(id -u)" -ne "0" ]; then
  echo "This script requires root."
  exit 1
fi

if [ ! -f /etc/kupfer/deviceinfo ]; then
  echo "No deviceinfo found at /etc/kupfer/deviceinfo. This might be intentional"
  exit 1
fi

eval "$(cat /etc/kupfer/deviceinfo)"

unset initramfs_file
if [ -f "/boot/initramfs-initsquared.img" ]; then
  initramfs_file="/boot/initramfs-initsquared.img"
elif [ -f "/boot/initramfs-linux.img" ]; then
  initramfs_file="/boot/initramfs-linux.img"
elif [ -z "$1" ]; then
  echo "/boot/initramfs-initsquared.img or /boot/initramfs-linux.img not found. initramfs location must be specified via argument."
  exit 1
else
  initramfs_file="$1"
fi

dir=$(mktemp -d)

echo "Generating new aboot.img with initramfs $initramfs_file"

MKBOOTIMG_ARGS=(
  --header_version "${deviceinfo_header_version:-0}"
  --ramdisk "$initramfs_file"
  --base "$deviceinfo_flash_offset_base"
  --second_offset "$deviceinfo_flash_offset_second"
  --kernel_offset "$deviceinfo_flash_offset_kernel"
  --ramdisk_offset "$deviceinfo_flash_offset_ramdisk"
  --tags_offset "$deviceinfo_flash_offset_tags"
  --pagesize "$deviceinfo_flash_pagesize"
  --cmdline "kupfer earlymodules=${deviceinfo_modules_initfs// /,} console=tty0 $deviceinfo_kernel_cmdline"
  -o /boot/aboot.img
)

if [ "${deviceinfo_header_version:-0}" -ge 2 ]; then
  HEADER_DEPENDENT_ARGS=(--kernel "/boot/Image.gz" --dtb_offset "$deviceinfo_flash_offset_dtb" --dtb "/boot/dtbs/$deviceinfo_dtb.dtb")
else
  cat /boot/Image.gz "/boot/dtbs/$deviceinfo_dtb.dtb" >"$dir/Image.gz-dtb"
  HEADER_DEPENDENT_ARGS=(--kernel "$dir/Image.gz-dtb")
fi

mkbootimg "${MKBOOTIMG_ARGS[@]}" "${HEADER_DEPENDENT_ARGS[@]}"

qhypstub_offset="508KiB"
qhypstub_size="4KiB"
lk2nd_bootimg_offset="512KiB"

flash_file="/boot/aboot.img"
if [[ "$deviceinfo_lk2nd" == "true" ]]; then
  echo "Generating new aboot.bin for lk2nd device"
  flash_file="/boot/aboot.bin"
  dd if=/dev/zero of="$flash_file" bs="$lk2nd_bootimg_offset" count=1
  dd of="$flash_file" bs="$qhypstub_offset" count=1 conv=notrunc if=/boot/lk2nd.img
  dd of="$flash_file" bs="$qhypstub_size" count=1 conv=notrunc seek="$qhypstub_offset" oflag=seek_bytes if=/boot/qhypstub.bin
  dd of="$flash_file" conv=notrunc oflag=append if=/boot/aboot.img
fi

AB_SLOT_SUFFIX=$(grep -o 'androidboot\.slot_suffix=..' /proc/cmdline | cut -d "=" -f2)
ABOOT_PART="/dev/disk/by-partlabel/boot${AB_SLOT_SUFFIX}"

if [ -e "$ABOOT_PART" ]; then
  echo "Running on the device"

  ABOOT_PART="$(readlink -f "$ABOOT_PART")"

  dd if="$ABOOT_PART" of=/tmp/aboot.bin
  if [[ "$deviceinfo_lk2nd" == "true" ]]; then
    dd if=/tmp/aboot.bin of=/tmp/aboot.img skip="$lk2nd_bootimg_offset" iflag=skip_bytes
  else
    cp /tmp/aboot.bin /tmp/aboot.img
  fi

  permanent=false
  if [[ "$(file /tmp/aboot.img)" != "/tmp/aboot.img: data" ]]; then
    cmdline=$(unpack_bootimg --boot_img /tmp/aboot.img --out "$dir" | grep -E "^command line args")
    if [[ "$cmdline" == *"kupfer"* ]]; then
      permanent=true
    fi
  fi

  if [[ "$permanent" == "true" ]]; then
    echo "Running with permanent aboot.img"
    echo "Flashing new aboot.img"
    dd if="$flash_file" of="$ABOOT_PART"
  elif [[ "$1" == "-f" ]]; then
    echo "Flashing new aboot.img"
    dd if="$flash_file" of="$ABOOT_PART"
  else
    echo "Running with temporary aboot.img"
    echo "Skip flashing new aboot.img"
  fi
else
  echo "Not running on the device"
  echo "Skip flashing new aboot.img"
fi

rm -rf "$dir" /tmp/aboot.bin /tmp/aboot.img

