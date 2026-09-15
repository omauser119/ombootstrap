# Porting

## Porting a device

1. Study the Android and postmarketOS port for the device.
2. Add a device recipe under `device/`, a kernel under `linux/`, and
   firmware under `firmware/`.
3. Start from the closest SoC recipe. The a6plte port is a reference for
   MSM8953 devices.
4. Set `_mode` in every PKGBUILD and validate it with:
   `ombs packages check`.
5. Build the device package and image before submitting changes.

Device metadata follows postmarketOS codenames. Android boot images still use
the existing boot pipeline, so new boot modes need a separate deployer.

## Porting the mobile shell

The `ombootstrap-mobile-config` package should contain user overrides in
`config/hypr/` and `config/omarchy/shell.json`. Keep the Omarchy
defaults under `/usr/share/omarchy` untouched and layer mobile behavior
after the defaults. Use Hyprland's Lua API (`hl.bind`,
`hl.config`, `hl.layer_rule`) instead of legacy text bindings.

Hardware-specific values such as the backlight name belong in
`mobile.conf`, not in the shared Lua files.

