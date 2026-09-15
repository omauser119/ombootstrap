# FAQ

## Which device is configured by default?

`msm8953-a6plte`, the Samsung Galaxy A6+ port adapted from
[legengemini/pkgbuilds-msm8953](https://gitlab.com/legengemini/pkgbuilds-msm8953).
Use `ombs devices list` to inspect all bundled device recipes.

## Which desktop is included?

The `omarchy-mobile` flavour imports Omarchy's Hyprland Lua defaults and
Quickshell shell. No GNOME, Phosh or Plasma flavour is selected in the default
graph.

## How do the hardware keys work?

Power toggles DPMS and touch input. Volume Down starts or signals
`wvkbd-mobintl -l simple,specialpad -H 160`. Volume Up opens the Omarchy
Quickshell application menu. The display scale is `2.666667` on `DSI-1`.

## How do I choose a backlight device?

Leave `BACKLIGHT_DEVICE=` empty for `brightnessctl` discovery. If a
phone exposes more than one backlight, put the name printed by
`brightnessctl -l` in `~/.config/ombootstrap/mobile.conf`.

## How do I build one package?

```sh
ombs packages build main/kupfer-config
ombs packages build omarchy/ombootstrap-mobile-config
```

The legacy `main/kupfer-config` path is retained because the boot stack
expects it; `base-kupfer` also provides the user-facing
`ombootstrap-config` alias.

