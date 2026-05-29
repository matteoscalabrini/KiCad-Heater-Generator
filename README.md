# PCB Heater Generator for KiCad 9

This is a KiCad 9 PCB editor action plugin that generates copper heater traces from voltage and wattage targets.

The plugin computes the required resistance as `R = V^2 / P`, then estimates copper trace length from copper resistivity, trace width, and copper thickness. It can generate serpentine, coil/spiral, and Hilbert-style traces inside rectangle, square, or circle proportions. A wxPython preview dialog lets you tune dimensions before the generated `PCB_TRACK` segments are inserted into the board.

## Controls

- `Height` is used only for rectangle outlines. Square and circle outlines lock height to the width/diameter value, and the dialog disables the height field to make that clear.
- `Adaptive fill` treats trace width and clearance as minimum manufacturable values. It searches for a width/clearance combination that fills the whole selected outline and gets as close as possible to the requested resistance.
- `Outline fit` reports whether the copper trace or terminal pads exceed the selected heater outline. The preview outline turns red if there is spill outside the constraint.
- `Terminal pad side` controls whether the end pads sit inside the heater path, centered on the path endpoint, or outside the endpoint.

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
- Resistance is an electrical estimate at nominal copper resistivity. Manufacturing tolerances, copper plating variation, temperature coefficient, solder mask, airflow, substrate temperature limits, and current density still need engineering review.
- Generated terminal pads are wide copper track stubs, not schematic-linked footprint pads.
