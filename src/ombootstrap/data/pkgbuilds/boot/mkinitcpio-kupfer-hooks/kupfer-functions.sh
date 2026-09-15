#!/usr/bin/ash

panic() {
	echo "$@" >&2
	exit 1
}

# Scan a partition $1 for type $2 and set result
# $2 must be in the format 'TYPE=query', for example 'LABEL=kupfer_boot'
check_partition_type()
{
	unset RESULT
	local partition="$1"
	local query="$2"
	local probe token value

	busybox partprobe "$partition" 2>/dev/null || true

	# The initramfs blkid returns success for a readable partition table even
	# when the requested token is absent. Probe the metadata and compare the
	# requested token explicitly, otherwise a container such as userdata can
	# be mistaken for the root filesystem.
	probe="$(blkid -p -o full "$partition" 2>/dev/null)" || return 1
	token="${query%%=*}"
	value="${query#*=}"
	case "$probe" in
		*"$token=\"$value\""*)
		echo "check_partition_type found $query at: $partition"
		export RESULT="$partition"
		return 0
		;;
	esac
	return 1
}

# Function will search for an available loopdev and export $LOOPDEV as a result after setup
# $1 = Partition to scan
setup_loopdev()
{
	local partition="$1"

	# Get sector size from deviceinfo
	eval "$(cat /etc/kupfer/deviceinfo)"
	deviceinfo_rootfs_image_sector_size="${deviceinfo_rootfs_image_sector_size:-512}"

	local _loopdev="$(losetup -f)"
	# BusyBox losetup in this initramfs has no -b option. The device uses
	# 512-byte sectors, which is also the default required for -P scanning.
	losetup -P "$_loopdev" "$partition" || return 1
	busybox partprobe "$_loopdev"

	if [ -n "$(ls "${_loopdev}"p?*)" ]; then
		export LOOPDEV="$_loopdev"
		return 0
	else
		echo "loopdev $_loopdev for $partition didn't show any subpartitions"
		losetup -d "$_loopdev"
		return 1
	fi
}

# Probe partitions inside of a partition
subpartition_probe()
{
	local partition="$1"
	local query="$2"

	setup_loopdev "$partition" || return 1

	# Scan all subpartitions
	for subpart in "$LOOPDEV"p?*; do
		! check_partition_type "$subpart" "$query" || return 0
	done

	return 1
}


# Arguments:
	# $1 = Partition to scan
	# $2 = grep $2 from blkid

# $2 must be in the format 'TYPE=query', for example 'LABEL=kupfer_boot'
scan_partitions()
{
	local partition="$1"
	local query="$2"

	echo "scanning $partition for $query"
	# Probe partition directly first
	! check_partition_type "$partition" "$query" || return 0

	# Then scan for subpartitions
	part_table_info="$(blkid -p -o full "$partition" 2>/dev/null)"
	case "$part_table_info" in
		*'PTTYPE="gpt"'*) part_table_type=gpt ;;
		*'PTTYPE="dos"'*) part_table_type=dos ;;
		*) part_table_type= ;;
	esac
	# Some kernels expose the nested partition table to fdisk but not to
	# blkid's low-level probe. Detect that form by its child partition names.
	if [ -z "$part_table_type" ] &&
		fdisk -l "$partition" 2>/dev/null | grep -q "^${partition}p[0-9]"; then
		part_table_type=nested
	fi
	if [ -n "$part_table_type" ]; then
		echo "Found partition table ($part_table_type) at: $partition"
		! subpartition_probe "$partition" "$query" || return 0
	fi

	return 1
}

# $1 is the partition that you want to detect loop device and subpartition number
get_loopdev_partinfo()
{
	if [ "$(echo $1 | grep /dev/loop)" ]; then
		export SUBPARTNUMBER="$(echo $1 | grep -Eo '[0-9]+$')"
		export LOOPDEV="${1%%p$SUBPARTNUMBER}"
	fi
}
