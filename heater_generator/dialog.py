from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import wx

from .generator import HeaterParameters, HeaterResult, generate_heater, outline_overflow_mm, translated


class PreviewPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent, size=(420, 320))
        self.result: Optional[HeaterResult] = None
        self.terminal_segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        self.terminal_width_mm = 0.0
        self.overflow_mm = 0.0
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.on_paint)

    def set_preview(
        self,
        result: HeaterResult,
        terminal_segments: Sequence[Tuple[Tuple[float, float], Tuple[float, float]]],
        terminal_width_mm: float,
        overflow_mm: float,
    ):
        self.result = result
        self.terminal_segments = list(terminal_segments)
        self.terminal_width_mm = terminal_width_mm
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

        points = result.points
        if len(points) >= 2:
            trace_px = max(2, int(params.track_width_mm * scale))
            dc.SetPen(wx.Pen(wx.Colour(184, 94, 29), trace_px, wx.PENSTYLE_SOLID))
            for idx in range(1, len(points)):
                dc.DrawLine(tx(points[idx - 1]), tx(points[idx]))

            if self.terminal_segments:
                terminal_px = max(trace_px + 4, int(self.terminal_width_mm * scale))
                dc.SetPen(wx.Pen(wx.Colour(54, 113, 181), terminal_px, wx.PENSTYLE_SOLID))
                for start, end in self.terminal_segments:
                    dc.DrawLine(tx(start), tx(end))

        if result.warnings or self.overflow_mm > 0.001:
            dc.SetTextForeground(wx.Colour(160, 70, 20))
        else:
            dc.SetTextForeground(wx.Colour(60, 60, 60))
        dc.DrawText("%d points, %.1f mm" % (len(points), result.path_length_mm), 12, 8)


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
        self.latest_terminal_segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        self.latest_terminal_overflow_mm = 0.0

        root = wx.BoxSizer(wx.VERTICAL)
        body = wx.BoxSizer(wx.HORIZONTAL)
        form = wx.FlexGridSizer(0, 2, 7, 8)
        form.AddGrowableCol(1, 1)

        self._add_float(form, "Voltage (V)", "voltage_v", 5.0)
        self._add_float(form, "Wattage (W)", "wattage_w", 10.0)
        self._add_float(form, "Trace width (mm)", "track_width_mm", 0.25)
        self._add_float(form, "Clearance (mm)", "clearance_mm", 0.25)
        self._add_check(form, "Adaptive fill", "adaptive_fill", False)
        self._add_float(form, "Copper thickness (um)", "copper_thickness_um", 35.0)
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
        self._add_choice(form, "Terminal pad side", "terminal_pad_side", ["Inside", "Centered", "Outside"], 0)
        self._add_check(form, "Trim to target", "trim_to_target", True)

        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(form, 1, wx.EXPAND | wx.ALL, 12)
        self.mode_note = wx.StaticText(self, label="")
        left.Add(self.mode_note, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        self.preview = PreviewPanel(self)
        self.metrics = wx.StaticText(self, label="")
        self.metrics.SetMinSize((360, 100))

        right = wx.BoxSizer(wx.VERTICAL)
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
            "Inserted %d heater track segments on %s."
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
            return None

        self.latest_result = result
        terminal_width = self._terminal_width(result)
        terminal_segments = self._terminal_segments(result.points)
        self.latest_terminal_segments = terminal_segments
        self.latest_terminal_overflow_mm = self._terminal_overflow(result, terminal_segments, terminal_width)
        self.preview.set_preview(
            result,
            terminal_segments,
            terminal_width,
            max(result.trace_overflow_mm, self.latest_terminal_overflow_mm),
        )
        self.metrics.SetLabel(self._metrics_text(result))
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
            copper_thickness_um=self._float_value("copper_thickness_um"),
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

    def _metrics_text(self, result: HeaterResult):
        fit_line = "Outline fit: OK"
        if self.latest_terminal_overflow_mm > 0.001 or result.trace_overflow_mm > 0.001:
            fit_line = "Outline fit: exceeds by %.2f mm" % max(
                self.latest_terminal_overflow_mm,
                result.trace_overflow_mm,
            )

        lines = [
            "Target: %.3f ohm, %.1f mm trace" % (result.target_resistance_ohm, result.target_length_mm),
            "Generated: %.3f ohm, %.2f W, %.3f A" % (result.resistance_ohm, result.wattage_w, result.current_a),
            "Trace: %.3f mm width, %.3f mm clearance" % (
                result.params.track_width_mm,
                result.params.clearance_mm,
            ),
            "Length: %.1f mm across %d points" % (result.path_length_mm, len(result.points)),
            fit_line,
        ]
        if self.latest_terminal_overflow_mm > 0.001:
            lines.append("Terminal pads exceed the outline by up to %.2f mm." % self.latest_terminal_overflow_mm)
        lines.extend(result.warnings)
        return "\n".join(lines)

    def _apply_control_state(self):
        outline = self._choice_value("outline").lower()
        adaptive = self.controls["adaptive_fill"].GetValue()
        height_enabled = outline == "rectangle"
        self.controls["height_mm"].Enable(height_enabled)
        self.labels["height_mm"].SetLabel("Height (mm)" if height_enabled else "Height (locked)")
        self.controls["height_mm"].SetToolTip(
            "Ignored for square and circle outlines; width/diameter controls both axes."
        )

        self.labels["track_width_mm"].SetLabel("Min trace width (mm)" if adaptive else "Trace width (mm)")
        self.labels["clearance_mm"].SetLabel("Min clearance (mm)" if adaptive else "Clearance (mm)")
        self.controls["trim_to_target"].Enable(not adaptive)
        self.controls["trim_to_target"].SetToolTip(
            "Disabled in adaptive fill because adaptive mode uses the full outline."
        )

        notes = []
        if not height_enabled:
            notes.append("Height follows width/diameter for square and circle outlines.")
        if adaptive:
            notes.append("Adaptive fill uses the full outline and treats width and clearance as minimums.")
        self.mode_note.SetLabel(" ".join(notes))
        self.mode_note.Wrap(300)
        self.Layout()

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

        layer = self._selected_layer()
        net = self._selected_net()
        width_iu = self.pcbnew.FromMM(result.params.track_width_mm)
        inserted = 0

        for idx in range(1, len(points)):
            if self._add_track(points[idx - 1], points[idx], width_iu, layer, net):
                inserted += 1

        if self.controls["terminal_pads"].GetValue():
            terminal_width = self.pcbnew.FromMM(self._terminal_width(result))
            for segment in self._terminal_segments(points):
                if self._add_track(segment[0], segment[1], terminal_width, layer, net):
                    inserted += 1

        try:
            self.board.BuildConnectivity()
        except Exception:
            pass
        self.pcbnew.Refresh()
        return inserted

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

    def _vector(self, point):
        return self.pcbnew.VECTOR2I(self.pcbnew.FromMM(point[0]), self.pcbnew.FromMM(point[1]))

    def _terminal_width(self, result: HeaterResult):
        return max(result.params.track_width_mm * 3.0, 1.4)

    def _terminal_length(self, result: HeaterResult):
        return max(result.params.track_width_mm * 4.0, 2.0)

    def _terminal_side(self):
        return self._choice_value("terminal_pad_side").lower()

    def _terminal_segments(self, points):
        if not self.controls["terminal_pads"].GetValue():
            return []
        result = self.latest_result
        length = self._terminal_length(result) if result is not None else 2.0
        return _terminal_segments(points, length, self._terminal_side())

    def _terminal_overflow(self, result: HeaterResult, terminal_segments, terminal_width_mm):
        overflow = 0.0
        for start, end in terminal_segments:
            overflow = max(overflow, outline_overflow_mm([start, end], result.params, terminal_width_mm))
        return overflow


def _terminal_segments(points: List[Tuple[float, float]], length_mm: float, side: str):
    if len(points) < 2:
        return []

    return [
        _terminal_segment(points[0], points[1], length_mm, side),
        _terminal_segment(points[-1], points[-2], length_mm, side),
    ]


def _terminal_segment(anchor, other, length_mm, side):
    vx = anchor[0] - other[0]
    vy = anchor[1] - other[1]
    norm = math.hypot(vx, vy) or 1.0
    ux = vx / norm
    uy = vy / norm

    if side == "outside":
        return (anchor, (anchor[0] + ux * length_mm, anchor[1] + uy * length_mm))
    if side == "centered":
        half = length_mm / 2.0
        return (
            (anchor[0] - ux * half, anchor[1] - uy * half),
            (anchor[0] + ux * half, anchor[1] + uy * half),
        )
    return (anchor, (anchor[0] - ux * length_mm, anchor[1] - uy * length_mm))
