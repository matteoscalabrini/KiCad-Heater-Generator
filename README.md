# PCB Heater Generator for KiCad 9

This is a KiCad 9 PCB editor action plugin that generates copper heater traces from voltage and wattage targets.

The plugin computes the required resistance as `R = V^2 / P`, then estimates copper trace length from copper resistivity, trace width, and copper thickness. It can generate serpentine, coil/spiral, and Hilbert-style traces inside rectangle, square, or circle proportions. A wxPython preview dialog lets you tune dimensions before the heater is inserted as a KiCad net-tie footprint or as direct board copper.

## Controls

- `Height` is used only for rectangle outlines. Square and circle outlines lock height to the width/diameter value, and the dialog disables the height field to make that clear.
- `Copper thickness` can be entered in oz or um. The dialog converts the displayed value when the unit selector changes and always computes with micrometers internally.
- `Adaptive fill` treats trace width and clearance as minimum manufacturable values. It searches for a width/clearance combination that fills the whole selected outline and gets as close as possible to the requested resistance.
- Adaptive fill results are shown in the status banner and metrics so the selected width/clearance are visible without changing the minimum inputs.
- `Output` selects between `Net-tie footprint` and `Board copper`. Net-tie footprint output creates one board-only footprint with pads `1` and `2` in a KiCad net-tie group, which is the DRC-safe mode for heaters whose start and end are intentionally connected by copper.
- `Start net` and `End net` assign the two terminal pads in net-tie footprint output. In board copper output, only the `Net` field is used and all generated board items are assigned to that net.
- `Outline fit` reports whether the copper trace or terminal pads exceed the selected heater outline. The preview outline turns red if there is spill outside the constraint.
- `Terminal pad side`, `Pad shape`, `Pad width`, and `Pad length` control whether the end pads sit inside the heater path, centered on the path endpoint, or outside the endpoint, and whether they are oval, round, or rectangular.
- Circle coil patterns are emitted as KiCad copper arc segments where possible, with shared endpoints so the route remains continuous. Serpentine and Hilbert routes stay as straight segments.
- `Terminal vias` adds through-vias at both heater endpoints in board copper output. In net-tie footprint output, the terminal pads become plated through-hole pads using the configured via diameter and drill.

## Install

For KiCad 9 on macOS, copy or symlink the `heater_generator` folder into:

```text
~/Documents/KiCad/9.0/scripting/plugins/
```

Then restart KiCad or refresh action plugins in the PCB editor preferences. The action appears as `PCB Heater Generator` under Tools > External Plugins and can be shown in the PCB editor toolbar.

For development on this machine:

```sh
mkdir -p ~/Documents/KiCad/9.0/scripting/plugins
ln -s "$PWD/heater_generator" ~/Documents/KiCad/9.0/scripting/plugins/heater_generator
```

## Notes

- The current implementation uses KiCad 9's documented `pcbnew.ActionPlugin` API because it runs directly inside the PCB editor and can use KiCad's embedded wxPython UI.
- KiCad 9 also has the newer IPC API. The IPC API is the long-term stable direction, but the SWIG `pcbnew` API is still documented and available in KiCad 9.
- Net-tie footprint output uses KiCad footprint net-tie pad groups so DRC treats the heater start and end as an intentional short inside the generated footprint.
- Resistance is an electrical estimate at nominal copper resistivity. Manufacturing tolerances, copper plating variation, temperature coefficient, solder mask, airflow, substrate temperature limits, and current density still need engineering review.
- Board copper output is a quick-generation mode and does not create a DRC net-tie boundary. Use net-tie footprint output when the heater bridges two different nets.
