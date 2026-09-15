# Quickstart

1. [Install](install) Ombootstrap.
2. Build an image:
   `ombs image build`

   If the config file is missing, `ombs` creates it and asks only for the
   target device. Omarchy Mobile and the other profile values use defaults.
3. Flash it with the appropriate Android boot target:
   `ombs image flash abootimg` or `ombs image flash full userdata`

The default profile selects `msm8953-a6plte` and
`omarchy-mobile`. See [Configuration](config) to select another device or
profile.
