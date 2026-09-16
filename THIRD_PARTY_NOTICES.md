# Third-party notices

The HID++ onboard-profile implementation in `swap/hidpp/` is adapted from the
open-source [omm.py](https://github.com/lexr1/omm.py) project by lexr1.

Swap uses [hidapi](https://github.com/libusb/hidapi) for USB HID access.

The macOS implementation in `gui/`, `gcore/`, and `ghub_presets/` was imported
from Corbin Randall's G app at commit `5e8cedb` with branding-only UI changes.
Its vendored HID++ implementation in `ghub_presets/omm/` also derives from
omm.py. The original module names are retained for compatibility.

Logitech, G, G HUB, and related marks are trademarks of Logitech Europe S.A.
and/or Logitech, Inc. Swap is not affiliated with Logitech.
