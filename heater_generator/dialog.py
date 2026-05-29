from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import wx

from .generator import HeaterParameters, HeaterResult, generate_heater, translated


class PreviewPanel(wx.Panel):
    def __init__(self, parent):
        super().__init__(parent, size=(420, 320))
        self.result: Optional[HeaterResult] = None
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.Bind(wx.EVT_PAINT, self.on_paint)

    def set_result(self, result: HeaterResult):
        self.result = result
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

        dc.SetPen(wx.Pen(wx.Colour(120, 120, 120), 1, wx.PENSTYLE_DOT))
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

            terminal_px = max(trace_px + 4, int(max(params.track_width_mm * 3.0, 1.4) * scale))
            dc.SetPen(wx.Pen(wx.Colour(54, 113, 181), terminal_px, wx.PENSTYLE_SOLID))
            _draw_terminal_stub(dc, tx, points, 0)
            _draw_terminal_stub(dc, tx, points, -1)

        if result.warnings:
            dc.SetTextForeground(wx.Colour(160, 70, 20))
        else:
            dc.SetTextForeground(wx.Colour(60, 60, 60))
        dc.DrawText("%d points, %.1f mm" % (len(points), result.path_length_mm), 12, 8)


def _draw_terminal_stub(dc, transform, points, index):
    if len(points) < 2:
        return
    if index == 0:
        anchor = points[0]
        other = points[1]
    else:
        anchor = points[-1]
        other = points[-2]
    vx = anchor[0] - other[0]
    vy = anchor[1] - other[1]
    length = math.hypot(vx, vy) or 1.0
    stub = (anchor[0] + vx / length * 1.6, anchor[1] + vy / length * 1.6)
    dc.DrawLine(transform(anchor), transform(stub))


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
        self.latest_result: Optional[HeaterResult] = None

        root = wx.BoxSizer(wx.VERTICAL)
        body = wx.BoxSizer(wx.HORIZONTAL)
        form = wx.FlexGridSizer(0, 2, 7, 8)
        form.AddGrowableCol(1, 1)

        self._add_float(form, "Voltage (V)", "voltage_v", 5.0)
        self._add_float(form, "Wattage (W)", "wattage_w", 10.0)
        self._add_float(form, "Trace width (mm)", "track_width_mm", 0.25)
        self._add_float(form, "Clearance (mm)", "clearance_mm", 0.25)
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
        self._add_check(form, "Trim to target", "trim_to_target", True)

        left = wx.BoxSizer(wx.VERTICAL)
        left.Add(form, 1, wx.EXPAND | wx.ALL, 12)

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

        self.update_preview()

    def _add_float(self, sizer, label, key, value):
        sizer.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
        control = wx.TextCtrl(self, value=str(value))
        self.controls[key] = control
        sizer.Add(control, 1, wx.EXPAND)

    def _add_int(self, sizer, label, key, value, minimum, maximum):
        sizer.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
        control = wx.SpinCtrl(self, value=str(value), min=minimum, max=maximum)
        self.controls[key] = control
        sizer.Add(control, 1, wx.EXPAND)

    def _add_choice(self, sizer, label, key, choices, selection):
        sizer.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
        control = wx.Choice(self, choices=choices)
        control.SetSelection(selection)
        self.controls[key] = control
        sizer.Add(control, 1, wx.EXPAND)

    def _add_check(self, sizer, label, key, value):
        sizer.Add(wx.StaticText(self, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
        control = wx.CheckBox(self)
        control.SetValue(value)
        self.controls[key] = control
        sizer.Add(control, 1, wx.EXPAND)

    def on_change(self, event):
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
        self.preview.set_result(result)
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
            trim_to_target=self.controls["trim_to_target"].GetValue(),
        )

    def _float_value(self, key):
        value = self.controls[key].GetValue()
        return float(value)

    def _choice_value(self, key):
        return self.controls[key].GetStringSelection()

    def _metrics_text(self, result: HeaterResult):
        lines = [
            "Target: %.3f ohm, %.1f mm trace" % (result.target_resistance_ohm, result.target_length_mm),
            "Generated: %.3f ohm, %.2f W, %.3f A" % (result.resistance_ohm, result.wattage_w, result.current_a),
            "Length: %.1f mm across %d points" % (result.path_length_mm, len(result.points)),
        ]
        lines.extend(result.warnings)
        return "\n".join(lines)

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
            terminal_width = self.pcbnew.FromMM(max(result.params.track_width_mm * 3.0, 1.4))
            terminal_length = max(result.params.track_width_mm * 4.0, 2.0)
            for segment in _terminal_segments(points, terminal_length):
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


def _terminal_segments(points: List[Tuple[float, float]], length_mm: float):
    if len(points) < 2:
        return []

    return [
        _terminal_segment(points[0], points[1], length_mm),
        _terminal_segment(points[-1], points[-2], length_mm),
    ]


def _terminal_segment(anchor, other, length_mm):
    vx = anchor[0] - other[0]
    vy = anchor[1] - other[1]
    norm = math.hypot(vx, vy) or 1.0
    half = length_mm / 2.0
    return (
        (anchor[0] - vx / norm * half, anchor[1] - vy / norm * half),
        (anchor[0] + vx / norm * half, anchor[1] + vy / norm * half),
    )
