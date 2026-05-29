from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Dict, List, Optional, Sequence, Tuple

import wx

from .generator import (
    HeaterParameters,
    HeaterResult,
    copper_oz_to_um,
    copper_um_to_oz,
    generate_heater,
    outline_overflow_mm,
    sample_path_segments,
    translated,
    translated_segments,
)


Point = Tuple[float, float]


@dataclass(frozen=True)
class TerminalPad:
    center: Point
    angle_deg: float
    width_mm: float
    length_mm: float
    shape: str


class PreviewPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent, size=(420, 320))
        self.result: Optional[HeaterResult] = None
        self.terminal_pads: List[TerminalPad] = []
        self.via_points: List[Point] = []
        self.via_diameter_mm = 0.0
        self.overflow_mm = 0.0
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.on_paint)

    def set_preview(
        self,
        result: HeaterResult,
        terminal_pads: Sequence[TerminalPad],
        via_points: Sequence[Point],
        via_diameter_mm: float,
        overflow_mm: float,
    ):
        self.result = result
        self.terminal_pads = list(terminal_pads)
        self.via_points = list(via_points)
        self.via_diameter_mm = via_diameter_mm
        self.overflow_mm = overflow_mm
        self.Refresh()

    def on_paint(self, event):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(wx.Colour(250, 250, 250)))
        dc.Clear()

        if self.result is None:
            return

        result = self.result
        params = result.params
        width, height = self.GetClientSize()
        pad = 20
        sx = (width - pad * 2) / max(params.width_mm, 0.1)
        sy = (height - pad * 2) / max(params.height_mm, 0.1)
        scale = min(sx, sy)
        ox = (width - params.width_mm * scale) / 2.0
        oy = (height - params.height_mm * scale) / 2.0

        def tx(point):
            return wx.Point(int(ox + point[0] * scale), int(oy + point[1] * scale))

        outline_colour = wx.Colour(185, 40, 32) if self.overflow_mm > 0.001 else wx.Colour(120, 120, 120)
        dc.SetPen(wx.Pen(outline_colour, 1, wx.PENSTYLE_DOT))
        dc.SetBrush(wx.TRANSPARENT_BRUSH)
        if params.outline == "circle":
            diameter = int(params.width_mm * scale)
            dc.DrawEllipse(int(ox), int(oy), diameter, diameter)
        else:
            dc.DrawRectangle(int(ox), int(oy), int(params.width_mm * scale), int(params.height_mm * scale))

        points = list(sample_path_segments(result.segments, 0.35))
        if len(points) >= 2:
            trace_px = max(2, int(params.track_width_mm * scale))
            dc.SetPen(wx.Pen(wx.Colour(184, 94, 29), trace_px, wx.PENSTYLE_SOLID))
            for idx in range(1, len(points)):
                dc.DrawLine(tx(points[idx - 1]), tx(points[idx]))

            if self.terminal_pads:
                dc.SetPen(wx.Pen(wx.Colour(28, 84, 150), 1, wx.PENSTYLE_SOLID))
                dc.SetBrush(wx.Brush(wx.Colour(54, 113, 181)))
                for pad in self.terminal_pads:
                    _draw_terminal_pad(dc, tx, pad, scale)

            if self.via_points:
                via_px = max(trace_px + 4, int(self.via_diameter_mm * scale))
                dc.SetPen(wx.Pen(wx.Colour(28, 84, 150), 1, wx.PENSTYLE_SOLID))
                dc.SetBrush(wx.Brush(wx.Colour(74, 138, 207)))
                for point in self.via_points:
                    center = tx(point)
                    dc.DrawEllipse(
                        int(center.x - via_px / 2),
                        int(center.y - via_px / 2),
                        via_px,
                        via_px,
                    )

        if result.warnings or self.overflow_mm > 0.001:
            dc.SetTextForeground(wx.Colour(160, 70, 20))
        else:
            dc.SetTextForeground(wx.Colour(60, 60, 60))
        dc.DrawText("%d segments, %.1f mm" % (len(result.segments), result.path_length_mm), 12, 8)


def _draw_terminal_pad(dc, transform, pad: TerminalPad, scale: float):
    polygon = _terminal_pad_polygon(pad, steps=32 if pad.shape in {"oval", "round"} else 4)
    dc.DrawPolygon([transform(point) for point in polygon])


class HeaterDialog(wx.Dialog):
    def __init__(self, parent, pcbnew_module, board):
        super().__init__(
            parent,
            title="PCB Heater Generator",
            size=(900, 610),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.pcbnew = pcbnew_module
        self.board = board
        self.layer_choices = self._load_layers()
        self.net_choices = self._load_nets()

        self.controls: Dict[str, wx.Window] = {}
        self.labels: Dict[str, wx.StaticText] = {}
        self.latest_result: Optional[HeaterResult] = None
        self.latest_terminal_pads: List[TerminalPad] = []
        self.latest_terminal_overflow_mm = 0.0
        self.latest_via_points: List[Point] = []
        self.latest_via_overflow_mm = 0.0
        self._last_copper_unit = "oz"

        root = wx.BoxSizer(wx.VERTICAL)
        body = wx.BoxSizer(wx.HORIZONTAL)
        form = wx.FlexGridSizer(0, 2, 7, 8)
        form.AddGrowableCol(1, 1)

        self._add_float(form, "Voltage (V)", "voltage_v", 5.0)
        self._add_float(form, "Wattage (W)", "wattage_w", 10.0)
        self._add_float(form, "Trace width (mm)", "track_width_mm", 0.25)
        self._add_float(form, "Clearance (mm)", "clearance_mm", 0.25)
        self._add_check(form, "Adaptive fill", "adaptive_fill", False)
        self._add_float(form, "Copper thickness", "copper_thickness", 1.0)
        self._add_choice(form, "Copper unit", "copper_unit", ["oz", "um"], 0)
        self._add_choice(form, "Curve", "curve", ["Serpentine", "Coil", "Hilbert"], 0)
        self._add_choice(form, "Outline", "outline", ["Rectangle", "Square", "Circle"], 0)
        self._add_float(form, "Width / diameter (mm)", "width_mm", 40.0)
        self._add_float(form, "Height (mm)", "height_mm", 20.0)
        self._add_float(form, "Margin (mm)", "margin_mm", 1.0)
        self._add_int(form, "Hilbert order", "hilbert_order", 4, 1, 8)
        self._add_float(form, "Board X (mm)", "origin_x_mm", 20.0)
        self._add_float(form, "Board Y (mm)", "origin_y_mm", 20.0)
        self._add_choice(form, "Layer", "layer", [name for name, _ in self.layer_choices], 0)
        self._add_choice(form, "Net", "net", [name for name, _ in self.net_choices], 0)
        self._add_check(form, "Terminal pads", "terminal_pads", True)
        self._add_choice(form, "Pad shape", "terminal_pad_shape", ["Oval", "Round", "Rectangle"], 0)
        self._add_float(form, "Pad width (mm)", "terminal_pad_width_mm", 1.4)
        self._add_float(form, "Pad length (mm)", "terminal_pad_length_mm", 2.0)
        self._add_choice(form, "Terminal pad side", "terminal_pad_side", ["Inside", "Centered", "Outside"], 0)
        self._add_check(form, "Terminal vias", "terminal_vias", True)
        self._add_float(form, "Via diameter (mm)", "via_diameter_mm", 1.4)
        self._add_float(form, "Via drill (mm)", "via_drill_mm", 0.7)
        self._add_check(form, "Trim to target", "trim_to_target", True)

        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(form, 1, wx.EXPAND | wx.ALL, 12)
        self.mode_note = wx.StaticText(self, label="")
        left.Add(self.mode_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.preview = PreviewPanel(self)
        self.status_banner = wx.StaticText(self, label="")
        status_font = self.status_banner.GetFont()
        status_font.MakeBold()
        self.status_banner.SetFont(status_font)
        self.metrics = wx.StaticText(self, label="")
        self.metrics.SetMinSize((360, 100))

        right = wx.BoxSizer(wx.VERTICAL)
        right.Add(self.status_banner, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        right.Add(self.preview, 1, wx.EXPAND | wx.ALL, 12)
        right.Add(self.metrics, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        body.Add(left, 0, wx.EXPAND)
        body.Add(right, 1, wx.EXPAND)

        buttons = wx.StdDialogButtonSizer()
        self.generate_btn = wx.Button(self, wx.ID_OK, "Generate")
        close_btn = wx.Button(self, wx.ID_CANCEL, "Close")
        buttons.AddButton(self.generate_btn)
        buttons.AddButton(close_btn)
        buttons.Realize()

        root.Add(body, 1, wx.EXPAND)
        root.Add(buttons, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizer(root)

        self.Bind(wx.EVT_BUTTON, self.on_generate, self.generate_btn)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        for control in self.controls.values():
            if isinstance(control, wx.SpinCtrl):
                control.Bind(wx.EVT_TEXT, self.on_change)
                control.Bind(wx.EVT_SPINCTRL, self.on_change)
            elif isinstance(control, wx.TextCtrl):
                control.Bind(wx.EVT_TEXT, self.on_change)
            elif isinstance(control, wx.Choice):
                control.Bind(wx.EVT_CHOICE, self.on_change)
            elif isinstance(control, wx.CheckBox):
                control.Bind(wx.EVT_CHECKBOX, self.on_change)

        self._apply_control_state()
        self.update_preview()

    def _add_float(self, sizer, label, key, value):
        label_control = wx.StaticText(self, label=label)
        self.labels[key] = label_control
        sizer.Add(label_control, 0, wx.ALIGN_CENTER_VERTICAL)
        control = wx.TextCtrl(self, value=str(value))
        self.controls[key] = control
        sizer.Add(control, 1, wx.EXPAND)

    def _add_int(self, sizer, label, key, value, minimum, maximum):
        label_control = wx.StaticText(self, label=label)
        self.labels[key] = label_control
        sizer.Add(label_control, 0, wx.ALIGN_CENTER_VERTICAL)
        control = wx.SpinCtrl(self, value=str(value), min=minimum, max=maximum)
        self.controls[key] = control
        sizer.Add(control, 1, wx.EXPAND)

    def _add_choice(self, sizer, label, key, choices, selection):
        label_control = wx.StaticText(self, label=label)
        self.labels[key] = label_control
        sizer.Add(label_control, 0, wx.ALIGN_CENTER_VERTICAL)
        control = wx.Choice(self, choices=choices)
        control.SetSelection(selection)
        self.controls[key] = control
        sizer.Add(control, 1, wx.EXPAND)

    def _add_check(self, sizer, label, key, value):
        label_control = wx.StaticText(self, label=label)
        self.labels[key] = label_control
        sizer.Add(label_control, 0, wx.ALIGN_CENTER_VERTICAL)
        control = wx.CheckBox(self)
        control.SetValue(value)
        self.controls[key] = control
        sizer.Add(control, 1, wx.EXPAND)

    def on_change(self, event):
        self._apply_control_state()
        self.update_preview()
        event.Skip()

    def on_close(self, event):
        self.EndModal(wx.ID_CANCEL)

    def on_generate(self, event):
        result = self.update_preview()
        if result is None or len(result.points) < 2:
            wx.MessageBox(
                "The current settings do not generate a usable heater trace.",
                "PCB Heater Generator",
                wx.OK | wx.ICON_ERROR,
                parent=self,
            )
            return

        inserted_count = self._insert_result(result)
        wx.MessageBox(
            "Inserted %d heater route items on %s."
            % (inserted_count, self._selected_layer_name()),
            "PCB Heater Generator",
            wx.OK | wx.ICON_INFORMATION,
            parent=self,
        )
        self.EndModal(wx.ID_OK)

    def update_preview(self):
        try:
            params = self._read_params()
            result = generate_heater(params)
        except Exception as exc:
            self.metrics.SetLabel("Invalid settings: %s" % exc)
            self._set_status("Error: %s" % exc, "error")
            return None

        self.latest_result = result
        terminal_pads = self._terminal_pads(result.points)
        via_diameter = self._via_diameter()
        via_points = self._terminal_via_points(result.points)
        self.latest_terminal_pads = terminal_pads
        self.latest_terminal_overflow_mm = self._terminal_overflow(result, terminal_pads)
        self.latest_via_points = via_points
        self.latest_via_overflow_mm = self._via_overflow(result, via_points, via_diameter)
        max_overflow = max(result.trace_overflow_mm, self.latest_terminal_overflow_mm, self.latest_via_overflow_mm)
        self.preview.set_preview(
            result,
            terminal_pads,
            via_points,
            via_diameter,
            max_overflow,
        )
        self.metrics.SetLabel(self._metrics_text(result))
        self._update_status(result, max_overflow)
        return result

    def _read_params(self):
        outline = self._choice_value("outline").lower()
        height = self._float_value("height_mm")
        width = self._float_value("width_mm")
        if outline in {"square", "circle"}:
            height = width

        return HeaterParameters(
            voltage_v=self._float_value("voltage_v"),
            wattage_w=self._float_value("wattage_w"),
            track_width_mm=self._float_value("track_width_mm"),
            clearance_mm=self._float_value("clearance_mm"),
            copper_thickness_um=self._copper_thickness_um(),
            outline=outline,
            curve=self._choice_value("curve").lower(),
            width_mm=width,
            height_mm=height,
            margin_mm=self._float_value("margin_mm"),
            hilbert_order=int(self.controls["hilbert_order"].GetValue()),
            trim_to_target=self.controls["trim_to_target"].GetValue()
            and not self.controls["adaptive_fill"].GetValue(),
            adaptive_fill=self.controls["adaptive_fill"].GetValue(),
        )

    def _float_value(self, key):
        value = self.controls[key].GetValue()
        return float(value)

    def _choice_value(self, key):
        return self.controls[key].GetStringSelection()

    def _copper_thickness_um(self):
        value = self._float_value("copper_thickness")
        if self._choice_value("copper_unit").lower() == "oz":
            return copper_oz_to_um(value)
        return value

    def _metrics_text(self, result: HeaterResult):
        fit_line = "Outline fit: OK"
        max_overflow = max(
            self.latest_terminal_overflow_mm,
            self.latest_via_overflow_mm,
            result.trace_overflow_mm,
        )
        if max_overflow > 0.001:
            fit_line = "Outline fit: exceeds by %.2f mm" % max(
                self.latest_terminal_overflow_mm,
                self.latest_via_overflow_mm,
                result.trace_overflow_mm,
            )

        arc_count = sum(1 for segment in result.segments if segment.kind == "arc")
        lines = [
            "Target: %.3f ohm, %.1f mm trace" % (result.target_resistance_ohm, result.target_length_mm),
            "Generated: %.3f ohm, %.2f W, %.3f A" % (result.resistance_ohm, result.wattage_w, result.current_a),
            "Copper: %.3f oz / %.1f um" % (
                copper_um_to_oz(result.params.copper_thickness_um),
                result.params.copper_thickness_um,
            ),
            "Trace: %.3f mm width, %.3f mm clearance" % (
                result.params.track_width_mm,
                result.params.clearance_mm,
            ),
            "Route: %d joined segments, %d arcs" % (len(result.segments), arc_count),
            "Length: %.1f mm" % result.path_length_mm,
            fit_line,
        ]
        if self.controls["adaptive_fill"].GetValue():
            lines.append(
                "Adaptive: min %.3f/%.3f mm, selected %.3f/%.3f mm"
                % (
                    self._float_value("track_width_mm"),
                    self._float_value("clearance_mm"),
                    result.params.track_width_mm,
                    result.params.clearance_mm,
                )
            )
        if self.latest_terminal_overflow_mm > 0.001:
            lines.append("Terminal pads exceed the outline by up to %.2f mm." % self.latest_terminal_overflow_mm)
        if self.latest_via_overflow_mm > 0.001:
            lines.append("Terminal vias exceed the outline by up to %.2f mm." % self.latest_via_overflow_mm)
        lines.extend(result.warnings)
        return "\n".join(lines)

    def _apply_control_state(self):
        outline = self._choice_value("outline").lower()
        adaptive = self.controls["adaptive_fill"].GetValue()
        copper_unit = self._choice_value("copper_unit").lower()
        self._sync_copper_unit(copper_unit)
        height_enabled = outline == "rectangle"
        self.controls["height_mm"].Enable(height_enabled)
        self.labels["height_mm"].SetLabel("Height (mm)" if height_enabled else "Height (locked)")
        self.controls["height_mm"].SetToolTip(
            "Ignored for square and circle outlines; width/diameter controls both axes."
        )

        self.labels["copper_thickness"].SetLabel("Copper thickness (%s)" % copper_unit)
        self.labels["track_width_mm"].SetLabel("Min trace width (mm)" if adaptive else "Trace width (mm)")
        self.labels["clearance_mm"].SetLabel("Min clearance (mm)" if adaptive else "Clearance (mm)")
        self.controls["trim_to_target"].Enable(not adaptive)
        self.controls["trim_to_target"].SetToolTip(
            "Disabled in adaptive fill because adaptive mode uses the full outline."
        )
        terminal_pads = self.controls["terminal_pads"].GetValue()
        pad_shape = self._choice_value("terminal_pad_shape").lower()
        self.controls["terminal_pad_shape"].Enable(terminal_pads)
        self.controls["terminal_pad_width_mm"].Enable(terminal_pads)
        self.controls["terminal_pad_length_mm"].Enable(terminal_pads and pad_shape != "round")
        self.controls["terminal_pad_side"].Enable(terminal_pads)
        self.labels["terminal_pad_width_mm"].SetLabel(
            "Pad diameter (mm)" if pad_shape == "round" else "Pad width (mm)"
        )
        terminal_vias = self.controls["terminal_vias"].GetValue()
        self.controls["via_diameter_mm"].Enable(terminal_vias)
        self.controls["via_drill_mm"].Enable(terminal_vias)

        notes = []
        if not height_enabled:
            notes.append("Height follows width/diameter for square and circle outlines.")
        if adaptive:
            notes.append("Adaptive fill uses the full outline and treats width and clearance as minimums.")
        self.mode_note.SetLabel(" ".join(notes))
        self.mode_note.Wrap(300)
        self.Layout()

    def _sync_copper_unit(self, copper_unit):
        if copper_unit == self._last_copper_unit:
            return
        try:
            value = self._float_value("copper_thickness")
        except ValueError:
            self._last_copper_unit = copper_unit
            return

        if self._last_copper_unit == "oz" and copper_unit == "um":
            value = copper_oz_to_um(value)
        elif self._last_copper_unit == "um" and copper_unit == "oz":
            value = copper_um_to_oz(value)
        self.controls["copper_thickness"].ChangeValue("%.4g" % value)
        self._last_copper_unit = copper_unit

    def _set_status(self, message, level):
        colours = {
            "ok": wx.Colour(34, 110, 62),
            "warning": wx.Colour(164, 93, 18),
            "error": wx.Colour(178, 45, 39),
        }
        self.status_banner.SetLabel(message)
        self.status_banner.SetForegroundColour(colours.get(level, colours["ok"]))
        self.status_banner.Wrap(380)
        self.Layout()

    def _update_status(self, result: HeaterResult, max_overflow_mm: float):
        if len(result.points) < 2:
            self._set_status("Error: current settings do not generate a usable route.", "error")
            return
        if max_overflow_mm > 0.001:
            self._set_status("Error: copper exceeds the heater outline by %.2f mm." % max_overflow_mm, "error")
            return
        if result.warnings:
            self._set_status("Warning: %s" % result.warnings[0], "warning")
            return
        if self.controls["adaptive_fill"].GetValue():
            power_error = abs(result.wattage_w - result.params.wattage_w) / result.params.wattage_w * 100.0
            self._set_status(
                "Adaptive fill selected %.3f mm width / %.3f mm clearance; power error %.1f%%."
                % (result.params.track_width_mm, result.params.clearance_mm, power_error),
                "ok",
            )
            return
        self._set_status("Ready: route fits inside the selected outline.", "ok")

    def _load_layers(self) -> List[Tuple[str, int]]:
        pcbnew = self.pcbnew
        layers: List[Tuple[str, int]] = []
        try:
            copper_count = int(self.board.GetCopperLayerCount())
        except Exception:
            copper_count = 2

        if copper_count <= 2:
            candidates = [pcbnew.F_Cu, pcbnew.B_Cu]
        else:
            candidates = [pcbnew.F_Cu] + list(range(1, copper_count - 1)) + [pcbnew.B_Cu]

        for layer in candidates:
            try:
                if self.board.IsLayerEnabled(layer):
                    layers.append((self.board.GetLayerName(layer), layer))
            except Exception:
                layers.append((pcbnew.LayerName(layer), layer))

        return layers or [("F.Cu", pcbnew.F_Cu)]

    def _load_nets(self) -> List[Tuple[str, object]]:
        nets: List[Tuple[str, object]] = [("(no net)", None)]
        try:
            by_name = self.board.GetNetsByName()
            net_items = sorted(
                ((str(name), net_info) for name, net_info in by_name.items() if str(name)),
                key=lambda item: item[0],
            )
            nets.extend(net_items)
        except Exception:
            pass
        return nets

    def _selected_layer(self):
        selection = self.controls["layer"].GetSelection()
        return self.layer_choices[max(selection, 0)][1]

    def _selected_layer_name(self):
        selection = self.controls["layer"].GetSelection()
        return self.layer_choices[max(selection, 0)][0]

    def _selected_net(self):
        selection = self.controls["net"].GetSelection()
        return self.net_choices[max(selection, 0)][1]

    def _insert_result(self, result: HeaterResult) -> int:
        origin_x = self._float_value("origin_x_mm")
        origin_y = self._float_value("origin_y_mm")
        points = translated(result.points, origin_x, origin_y)
        segments = translated_segments(result.segments, origin_x, origin_y)

        layer = self._selected_layer()
        net = self._selected_net()
        width_iu = self.pcbnew.FromMM(result.params.track_width_mm)
        inserted = 0

        for segment in segments:
            if self._add_route_segment(segment, width_iu, layer, net):
                inserted += 1

        if self.controls["terminal_pads"].GetValue():
            for pad in self._terminal_pads(points):
                if self._add_terminal_pad(pad, layer, net):
                    inserted += 1

        if self.controls["terminal_vias"].GetValue():
            via_diameter = self._via_diameter()
            via_drill = self._via_drill(via_diameter)
            for point in self._terminal_via_points(points):
                if self._add_via(point, via_diameter, via_drill, net):
                    inserted += 1

        try:
            self.board.BuildConnectivity()
        except Exception:
            pass
        self.pcbnew.Refresh()
        return inserted

    def _add_route_segment(self, segment, width_iu, layer, net):
        if segment.kind == "arc" and segment.mid is not None:
            return self._add_arc(segment, width_iu, layer, net)
        return self._add_track(segment.start, segment.end, width_iu, layer, net)

    def _add_track(self, start, end, width_iu, layer, net):
        if math.hypot(end[0] - start[0], end[1] - start[1]) < 0.001:
            return False
        track = self.pcbnew.PCB_TRACK(self.board)
        track.SetStart(self._vector(start))
        track.SetEnd(self._vector(end))
        track.SetWidth(width_iu)
        track.SetLayer(layer)
        if net is not None:
            track.SetNet(net)
        self.board.Add(track)
        return True

    def _add_arc(self, segment, width_iu, layer, net):
        if segment.mid is None or math.hypot(
            segment.end[0] - segment.start[0],
            segment.end[1] - segment.start[1],
        ) < 0.001:
            return False
        arc = self.pcbnew.PCB_ARC(self.board)
        arc.SetStart(self._vector(segment.start))
        arc.SetMid(self._vector(segment.mid))
        arc.SetEnd(self._vector(segment.end))
        arc.SetWidth(width_iu)
        arc.SetLayer(layer)
        if net is not None:
            arc.SetNet(net)
        self.board.Add(arc)
        return True

    def _add_via(self, point, diameter_mm, drill_mm, net):
        via = self.pcbnew.PCB_VIA(self.board)
        via.SetPosition(self._vector(point))
        via.SetWidth(self.pcbnew.FromMM(diameter_mm))
        via.SetDrill(self.pcbnew.FromMM(drill_mm))
        try:
            via.SetLayerPair(self.pcbnew.F_Cu, self.pcbnew.B_Cu)
        except Exception:
            pass
        if net is not None:
            via.SetNet(net)
        self.board.Add(via)
        return True

    def _add_terminal_pad(self, terminal_pad: TerminalPad, layer, net):
        try:
            footprint = self.pcbnew.FOOTPRINT(self.board)
            footprint.SetReference("H*")
            footprint.SetValue("HeaterPad")
            footprint.SetPosition(self._vector((0.0, 0.0)))
            try:
                footprint.Reference().SetVisible(False)
                footprint.Value().SetVisible(False)
            except Exception:
                pass

            pad = self.pcbnew.PAD(footprint)
            pad.SetAttribute(self.pcbnew.PAD_ATTRIB_CONN)
            pad.SetShape(self._pad_shape_constant(terminal_pad.shape))
            pad.SetSize(
                self.pcbnew.VECTOR2I(
                    self.pcbnew.FromMM(terminal_pad.length_mm),
                    self.pcbnew.FromMM(terminal_pad.width_mm),
                )
            )
            pad.SetPosition(self._vector(terminal_pad.center))
            pad.SetOrientationDegrees(terminal_pad.angle_deg)
            layer_set = self.pcbnew.LSET()
            layer_set.addLayer(layer)
            pad.SetLayerSet(layer_set)
            if net is not None:
                pad.SetNet(net)
            footprint.Add(pad)
            self.board.Add(footprint)
            return True
        except Exception:
            start, end = _terminal_pad_segment(terminal_pad)
            return self._add_track(
                start,
                end,
                self.pcbnew.FromMM(terminal_pad.width_mm),
                layer,
                net,
            )

    def _pad_shape_constant(self, shape):
        if shape == "rectangle":
            return self.pcbnew.PAD_SHAPE_RECT
        if shape == "round":
            return self.pcbnew.PAD_SHAPE_CIRCLE
        return self.pcbnew.PAD_SHAPE_OVAL

    def _vector(self, point):
        return self.pcbnew.VECTOR2I(self.pcbnew.FromMM(point[0]), self.pcbnew.FromMM(point[1]))

    def _terminal_side(self):
        return self._choice_value("terminal_pad_side").lower()

    def _terminal_pads(self, points):
        if not self.controls["terminal_pads"].GetValue():
            return []
        if len(points) < 2:
            return []
        width = self._terminal_pad_width()
        length = self._terminal_pad_length(width)
        shape = self._choice_value("terminal_pad_shape").lower()
        if shape == "round":
            length = width
        return [
            _terminal_pad(points[0], points[1], width, length, shape, self._terminal_side()),
            _terminal_pad(points[-1], points[-2], width, length, shape, self._terminal_side()),
        ]

    def _terminal_pad_width(self):
        result = self.latest_result
        minimum = result.params.track_width_mm if result is not None else 0.01
        return max(self._float_value("terminal_pad_width_mm"), minimum)

    def _terminal_pad_length(self, width_mm):
        return max(self._float_value("terminal_pad_length_mm"), width_mm)

    def _terminal_overflow(self, result: HeaterResult, terminal_pads):
        overflow = 0.0
        for terminal_pad in terminal_pads:
            overflow = max(
                overflow,
                outline_overflow_mm(_terminal_pad_polygon(terminal_pad), result.params, 0.0),
            )
        return overflow

    def _terminal_via_points(self, points):
        if not self.controls["terminal_vias"].GetValue() or len(points) < 2:
            return []
        return [points[0], points[-1]]

    def _via_diameter(self):
        return max(self._float_value("via_diameter_mm"), 0.1)

    def _via_drill(self, via_diameter_mm):
        return min(max(self._float_value("via_drill_mm"), 0.05), max(via_diameter_mm - 0.05, 0.05))

    def _via_overflow(self, result: HeaterResult, via_points, via_diameter_mm):
        overflow = 0.0
        for point in via_points:
            overflow = max(overflow, outline_overflow_mm([point], result.params, via_diameter_mm))
        return overflow


def _terminal_pad(anchor, other, width_mm, length_mm, shape, side):
    vx = anchor[0] - other[0]
    vy = anchor[1] - other[1]
    norm = math.hypot(vx, vy) or 1.0
    ux = vx / norm
    uy = vy / norm
    offset = 0.0
    if side == "outside":
        offset = length_mm / 2.0
    elif side == "inside":
        offset = -length_mm / 2.0

    center = (anchor[0] + ux * offset, anchor[1] + uy * offset)
    return TerminalPad(center, math.degrees(math.atan2(uy, ux)), width_mm, length_mm, shape)


def _terminal_pad_segment(terminal_pad: TerminalPad):
    angle = math.radians(terminal_pad.angle_deg)
    ux = math.cos(angle)
    uy = math.sin(angle)
    half = terminal_pad.length_mm / 2.0
    return (
        (terminal_pad.center[0] - ux * half, terminal_pad.center[1] - uy * half),
        (terminal_pad.center[0] + ux * half, terminal_pad.center[1] + uy * half),
    )


def _terminal_pad_polygon(terminal_pad: TerminalPad, steps: int = 32):
    angle = math.radians(terminal_pad.angle_deg)
    ux = math.cos(angle)
    uy = math.sin(angle)
    vx = -uy
    vy = ux
    half_l = terminal_pad.length_mm / 2.0
    half_w = terminal_pad.width_mm / 2.0

    def transform(local_x, local_y):
        return (
            terminal_pad.center[0] + ux * local_x + vx * local_y,
            terminal_pad.center[1] + uy * local_x + vy * local_y,
        )

    if terminal_pad.shape == "rectangle":
        return [
            transform(-half_l, -half_w),
            transform(half_l, -half_w),
            transform(half_l, half_w),
            transform(-half_l, half_w),
        ]

    count = max(12, steps)
    if terminal_pad.shape == "round":
        half_l = half_w
    return [
        transform(math.cos(idx / count * 2.0 * math.pi) * half_l, math.sin(idx / count * 2.0 * math.pi) * half_w)
        for idx in range(count)
    ]
