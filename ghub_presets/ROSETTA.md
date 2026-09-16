# G502 Rosetta Stone — G Hub built-in IDs & slot mapping

This file documents how Logitech G Hub encodes button assignments in `settings.db`.
The Python module `ghub_presets/rosetta.py` implements these rules for import/export.

## OMM onboard ↔ G Hub slots

| OMM array index | G Hub slot | Physical button (G502) |
|-----------------|------------|-------------------------|
| 0 | g1 | Left click |
| 1 | g2 | Right click |
| 2 | g3 | Middle click |
| 3 | g4 | Back thumb |
| 4 | g5 | Forward thumb |
| 5 | g6 | DPI Shift |
| 6 | g7 | DPI Down |
| 7 | g8 | DPI Up |
| 8 | g9 | Sniper / profile |
| 9 | g10 | Scroll tilt left (G Hub only) |
| 10 | g11 | Scroll tilt right (G Hub only) |

Use `ghub-presets compare --onboard … --ghub …` to validate pulls vs exports.

## Built-in preset card prefix

All factory/system actions use UUID-shaped ids:

```
0f82f693-5b78-4cf5-867e-<12 hex digits>
```

Defined in `convert.py` as `PRESET_PREFIX`. These often have **no row** in
`settings.cards` — G Hub resolves them at runtime.

## Critical: 02xx suffix overload (mouse vs F-keys)

The same suffix means **different things depending on slotId**:

| Suffix | On **g1 / g2 / g3** | On **any other slot** |
|--------|---------------------|------------------------|
| `020100000000` | **Left click** | **F1** keystroke |
| `020200000000` | **Right click** | **F2** keystroke |
| `020300000000` | **Middle click** | **F3** keystroke |

**Import rule:** never materialize g1/g2/g3 mouse clicks as `KEYSTROKE` macros —
keep the built-in id so G Hub treats them as primary/secondary/middle buttons.

**Export rule:** `_describe_card` must receive `slotId` when labeling built-ins,
otherwise left click on g1 is mislabeled as `F1` in `readable`.

## Other common built-in suffixes

| Suffix | Meaning |
|--------|---------|
| `090700000000` | Disabled / G-Shift layer default |
| `090500000000` | Cycle DPI |
| `090100000000` | Scroll up |
| `090600000000` | Scroll down |
| `014000000000` | G-Shift |
| `016800000000` | Back (thumb) |
| `016900000000` | Forward (thumb) |
| `01XX00000000` | Letter key (HID code = 0xXX) — e.g. `010800` = E on g5 |
| `04XX00000000` | Letter key (HID code = 0xXX + 3) — wireless profile variant |

## Device slot prefixes

| modelId in settings.db | Assignment prefix |
|------------------------|-------------------|
| `g502_spectrum` | `g502spectrum_` |
| `g502_wireless` / `g502wireless` | `g502wireless_` |

G Hub may register **both** prefixes for one profile. Import mirrors bindings
from the preset prefix to other known prefixes so wireless and wired layers match.

## Toolkit files using this reference

| File | Role |
|------|------|
| `rosetta.py` | Slot-aware decode + import rules |
| `builtin_cards.py` | Materialize letter-key built-ins only |
| `export.py` | Correct `readable` labels |
| `compare.py` | OMM index ↔ g-slot validation |
| `convert.py` | OMM → preset, `mouse_presets`, F-key ids |
