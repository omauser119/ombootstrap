#!/bin/bash

build() {
    # This hook is deliberately placed after the base hook.  Re-adding these
    # paths replaces mkinitcpio's generic init with the instrumented Kupfer
    # version that has no fsck_root call and records boot stages.
    add_file /usr/lib/initcpio/init-kupfer-debug /init 755
    add_file /usr/lib/initcpio/init_functions-kupfer-debug /init_functions 644
    add_runscript
    add_binary /usr/bin/initramfs-usb-telnet
    add_binary /usr/bin/initramfs-green-screen

    # qcom_wcnss_ctrl requests this exact relative path before the rootfs
    # services have prepared their firmware symlink tree. Include the device
    # calibration blob in the initramfs so Wi-Fi can start immediately.
    for nv in \
        /usr/lib/firmware/kupfer/wlan/prima/WCNSS_qcom_wlan_nv.bin \
        /usr/lib/firmware/postmarketos/wlan/prima/WCNSS_qcom_wlan_nv.bin; do
        if [ -f "$nv" ]; then
            add_file "$nv" /usr/lib/firmware/kupfer/wlan/prima/WCNSS_qcom_wlan_nv.bin 644
            break
        fi
    done

    for image in /usr/share/initramfs/green-*.ppm.gz; do
        name=${image##*/}
        name=${name%.gz}
        gzip -cd "$image" | add_file - "/usr/share/initramfs/$name" 644
    done

    # These are modules in the msm8953 kernel.  Keep them optional so the
    # generic debug flavour still builds for devices with a different kernel.
    add_module 'libcomposite?'
    add_module 'usb_f_ecm?'
    add_module 'u_ether?'
    add_module 'led-class-flash?'
    add_module 'sm5708-power?'
    add_module 'panel-samsung-s6e3fa7-ams604nl01?'
}

help() {
    cat <<HELPEOF
This hook spins up a telnet server in the early boot phase to debug stuff
HELPEOF
}
