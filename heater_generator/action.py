import os

import pcbnew


class HeaterGeneratorPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "PCB Heater Generator"
        self.category = "PCB geometry"
        self.description = "Generate copper PCB heater traces from voltage, wattage, and layout constraints."
        self.show_toolbar_button = True

        icon = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon):
            self.icon_file_name = icon

    def Run(self):
        import wx

        from .dialog import HeaterDialog

        board = pcbnew.GetBoard()
        if board is None:
            wx.MessageBox(
                "No board is open in the PCB editor.",
                "PCB Heater Generator",
                wx.OK | wx.ICON_ERROR,
            )
            return

        dialog = HeaterDialog(None, pcbnew, board)
        try:
            dialog.ShowModal()
        finally:
            dialog.Destroy()
