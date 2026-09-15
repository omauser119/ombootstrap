-- Hyprland >= 0.56. Keep mobile overrides after Omarchy defaults and toggles.
hl.config({
  general = { gaps_out = 20 },
  input = { touchdevice = { enabled = true } },
  gestures = {
    workspace_swipe_touch = true,
    workspace_swipe_cancel_ratio = 0.5,
    workspace_swipe_min_speed_to_force = 5,
  },
  render = { direct_scanout = 0 },
  misc = { key_press_enables_dpms = false, mouse_move_enables_dpms = false },
})
-- The old text-config workaround was `explicit_sync = 0` and
-- `explicit_sync_kms = 0`.  Hyprland 0.56's Lua API no longer accepts those
-- options, so the supported equivalent here is direct scanout disabled below.
hl.env("GSK_RENDERER", "ngl")

-- Override Omarchy's desktop power/volume actions instead of double-binding.
for _, key in ipairs({ "XF86PowerOff", "XF86AudioLowerVolume", "XF86AudioRaiseVolume" }) do
  hl.unbind(key)
end
o.bind("XF86PowerOff", "Screen and touch on/off", "ombs-mobile-screen toggle", { locked = true, ignore_mods = true })
o.bind("XF86AudioLowerVolume", "Toggle on-screen keyboard", "ombs-mobile-keyboard toggle", { locked = true, ignore_mods = true })
o.bind("XF86AudioRaiseVolume", "Applications", "omarchy-menu toggle apps", { ignore_mods = true })

-- 2 allows interaction above the lock screen in the current Lua API.
hl.layer_rule({ match = { namespace = "^wvkbd$" }, above_lock = 2 })
hl.on("hyprland.start", function()
  hl.exec_cmd("systemctl --user import-environment WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE XDG_CURRENT_DESKTOP OMARCHY_PATH")
  hl.exec_cmd("dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP OMARCHY_PATH")
  hl.exec_cmd("omarchy-launch-shell")
  hl.exec_cmd("ombs-mobile-keyboard start")
end)
