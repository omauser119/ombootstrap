#!/usr/bin/ash

# Start the USB debug transport in the background.  The normal initramfs
# sequence continues immediately; a successful rootfs handoff cleans it up.
run_hook() {
    /usr/bin/initramfs-usb-telnet
}
