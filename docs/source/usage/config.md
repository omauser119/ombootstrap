# Configuration

Ombootstrap uses TOML. Manage the file with `ombs config` or edit it
directly.

## File locations

The configuration is `~/.config/ombootstrap/ombootstrap.toml`. Caches and
the materialized PKGBUILD tree are under `~/.cache/ombootstrap/` by default.

The initial package section is:

```toml
[pkgbuilds]
git_repo = "bundled"
git_branch = "dev"
```

The default wrapper is disabled so image builds run on the host:

```toml
[wrapper]
type = "none"
```

Set `type = "docker"` to use the container wrapper explicitly.

The bundled tree contains the Omarchy AArch64 recipes and the a6plte device
recipes. Set `git_repo` to a compatible fork only when you intentionally
want an external recipe tree.

## Default profile

```toml
[profiles.default]
device = "msm8953-a6plte"
flavour = "omarchy-mobile"
hostname = "omarchy-phone"
username = "omarchy"
pkgs_include = []
pkgs_exclude = []
size_extra_mb = 0
```

The mobile profile installs Omarchy Hyprland and Quickshell. Its user config is
copied to `~/.config/hypr/` and `~/.config/omarchy/shell.json`; edit the
copies after first boot. Set `BACKLIGHT_DEVICE` in
`~/.config/ombootstrap/mobile.conf` only when automatic backlight discovery
does not find the correct DSI device.

Other profiles can inherit `profiles.default`; recovery images can use the
existing `debug-shell` flavour.
