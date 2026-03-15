"""
BOMKart ActionPlugin - Registers in KiCad PCB Editor → Tools → External Plugins.
"""

import pcbnew
import os
import wx


class BOMKartPlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "BOMKart - Order BOM"
        self.category = "BOM & Procurement"
        self.description = (
            "Extract BOM from PCB, check pricing & availability from local "
            "Indian distributors, and place orders — all without leaving KiCad."
        )
        self.show_toolbar_button = True
        icon_path = os.path.join(os.path.dirname(__file__), "resources", "bomkart_icon.png")
        if os.path.exists(icon_path):
            self.icon_file_name = icon_path
        dark_icon_path = os.path.join(os.path.dirname(__file__), "resources", "bomkart_icon_dark.png")
        if os.path.exists(dark_icon_path):
            self.dark_icon_file_name = dark_icon_path

    def Run(self):
        try:
            board = pcbnew.GetBoard()
            if board is None:
                wx.MessageBox(
                    "No board loaded.\nPlease open a PCB project first.",
                    "BOMKart", wx.OK | wx.ICON_ERROR,
                )
                return

            from .bom_extractor import BOMExtractor
            extractor = BOMExtractor(board)
            bom_items = extractor.extract()

            if not bom_items:
                wx.MessageBox(
                    "No components found on the board.\n\n"
                    "Make sure your schematic has components with values assigned.\n"
                    "Components marked DNP or excluded from BOM are skipped.",
                    "BOMKart", wx.OK | wx.ICON_WARNING,
                )
                return

            from .dialog.main_dialog import BOMKartMainDialog
            dlg = BOMKartMainDialog(None, bom_items, board)
            dlg.ShowModal()
            dlg.Destroy()

        except Exception as e:
            wx.MessageBox(
                f"BOMKart encountered an error:\n\n{str(e)}\n\n"
                "Please report this at github.com/hallycon/bomkart-kicad/issues",
                "BOMKart Error", wx.OK | wx.ICON_ERROR,
            )
