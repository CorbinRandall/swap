# Swap

Swap is a native Windows and macOS app for Logitech onboard mouse profiles.

Open Swap and choose an active profile. Windows keeps its existing compact
profile selector. macOS uses the proven G Dock app, with Swap's name and icon:
an active-profile selector plus Preset Management for bulk import/export and
choosing a preset folder. Neither app edits presets or requires G HUB at runtime.

The Mac port preserves G's backend, settings paths, and permission identity.
See [macOS compatibility and installation](docs/MACOS.md).

> Independent project. Not affiliated with or endorsed by Logitech.

## Supported hardware

- G502 Proteus Spectrum (`046D:C332`)
- G502 Hero (`046D:C08B`)
- G502 Lightspeed over USB (`046D:C08D`)
- G502 Lightspeed receiver (`046D:C539`) when its firmware supports a real
  onboard profile-index switch

The Windows backend never emulates receiver switching by overwriting another onboard slot. If
a receiver rejects a true slot switch, connect the mouse over USB.
The preserved Mac backend retains G's existing Lightspeed overlay behavior.

## Why discovery is reliable

On Windows, G502 firmware can emit unsolicited HID++ reports on the same endpoint used for
commands. Swap ignores unrelated reports and waits for the response matching
the current command. At launch it also performs bounded automatic acquisition
retries, so USB initialization races require no Retry button or user action.

## Run from source

### Windows

```powershell
py -3 -m pip install -e .
scripts\run_windows.bat
```

### macOS

```bash
python3 -m pip install -e ".[macos]"
./scripts/run_macos.sh
```

macOS requires Input Monitoring permission. Existing G installs retain the
same bundle identifier and signing identity; settings may still call it G.
Source development uses the Python interpreter's permissions.

## Build

### Windows

```powershell
py -3 -m pip install -e ".[build]"
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

Output: `dist/Swap/Swap.exe`.

### macOS

```bash
python3 -m pip install -e ".[macos,build]"
./scripts/build_macos.sh
./scripts/install_macos.sh
```

Output: `dist/Swap.app`, installed as `/Applications/Swap.app`. The build reuses
the existing `G Onboard Local` certificate, or `SWAP_SIGN_IDENTITY` if supplied.
It stops if that identity is missing rather than silently changing signatures.
Distribution outside your own Mac requires Apple Developer ID signing and notarization.

## Test

```bash
python -m unittest discover -s tests -v
```

## License

MIT. The HID++ implementation is derived from the open-source G Hub Preset
Toolkit and related HID++ research; see `LICENSE`.
