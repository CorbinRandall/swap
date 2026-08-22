# Swap

Swap is a tiny native Windows and macOS app that switches between profiles
already stored in a Logitech mouse's onboard memory.

Open Swap, choose a profile, and the mouse switches immediately. There is no
preset editor, library manager, tray menu, cloud service, or G HUB database
integration.

> Independent project. Not affiliated with or endorsed by Logitech.

## Supported hardware

- G502 Proteus Spectrum (`046D:C332`)
- G502 Hero (`046D:C08B`)
- G502 Lightspeed over USB (`046D:C08D`)
- G502 Lightspeed receiver (`046D:C539`) when its firmware supports a real
  onboard profile-index switch

Swap never emulates receiver switching by overwriting another onboard slot. If
a receiver rejects a true slot switch, connect the mouse over USB.

## Why discovery is reliable

G502 firmware can emit unsolicited HID++ reports on the same endpoint used for
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

macOS requires one-time Input Monitoring permission for Swap or the Python
interpreter used during source development.

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
```

Output: `dist/Swap.app`. Distribution outside your own Mac requires normal
Apple Developer ID signing and notarization.

## Test

```bash
python -m unittest discover -s tests -v
```

## License

MIT. The HID++ implementation is derived from the open-source G Hub Preset
Toolkit and related HID++ research; see `LICENSE`.
