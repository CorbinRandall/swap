# Swap for macOS

The Mac app is the working G Dock app under Swap branding. Its source was
imported from g-onboard commit `5e8cedb` and compared with the installed G app.
Only visible UI names, the icon, platform dispatch, and packaging change.

## Compatibility boundaries

- `gcore/` and `ghub_presets/` are copied unchanged. Do not replace them with
  the Windows engine or refactor HID behavior during branding work.
- `gui/app_mac.py` retains G's active-profile dropdown, Preset Management,
  Quit G Hub, and Quit App behavior. The last button now says Quit Swap.
- `swap/app_win.py` and the Windows backend remain unchanged.
- `swap/app.py` dispatches macOS to `gui.app`; py2app builds that entry directly.
- Windows packaging excludes the imported Mac packages.
- Existing internal names and older diagnostic text may still say G. This is
  deliberate compatibility, not an additional application.

## Identity and existing presets

Keep bundle ID `io.bytecode.g-onboard` and sign with the same certificate used
by G. Both the launcher and nested Python executable use that identifier.
The display name and icon do not require changing the permission identity.
Do not reset TCC or create a replacement certificate as part of a rebrand.

Keep `~/Library/Application Support/G`, `presets.dir`, archives, and existing
environment overrides. No presets or device state are migrated or rewritten.
Local presets, signing keys, and captured mouse data are excluded from Git.

## Build and install

Install the `macos,build` extras, then run `scripts/build_macos.sh` and
`scripts/install_macos.sh`. `SWAP_PYTHON` optionally selects the build Python;
`SWAP_SIGN_IDENTITY` optionally selects an existing certificate. Changing the
certificate can require a new permission grant. The installer archives prior
G/Swap apps under `~/Library/Application Support/G/app-backups` before replacing
them with one `/Applications/Swap.app`. Preset data is left in place.

For a rollback, quit Swap, remove it from Applications, extract the latest G
archive back into Applications, and open G. No mouse writes are needed.

## Validation

Run `python3 -m unittest discover -s tests -v` for both engines' offline tests.
Verify the signed app with `codesign --verify --deep --strict dist/Swap.app`.
On a Mac with Input Monitoring granted, open Swap with G HUB closed and confirm
the existing slot names and selected profile appear. Switching, bulk push, and
bulk pull retain G's existing implementation; never overwrite a mouse simply
to test a logo/name change. Windows UI testing still requires Windows hardware.
