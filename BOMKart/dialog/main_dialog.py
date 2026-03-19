"""
BOMKart Main Dialog

Full-featured wxPython dialog with:
- BOM table with color-coded availability
- Component search panel (search engine inside KiCad)
- Settings panel (API URL, customer info)
- CSV/JSON export
- Order placement flow
"""

import wx
import wx.lib.mixins.listctrl as listmix
import csv
import io
import json
import os
import threading
from typing import List

from ..bom_extractor import BOMItem


# ── Colors ─────────────────────────────────────────────

CLR_GREEN_BG = wx.Colour(232, 245, 233)      # Available
CLR_YELLOW_BG = wx.Colour(255, 243, 224)      # Partial
CLR_RED_BG = wx.Colour(255, 235, 238)         # Unavailable
CLR_ALT_ROW = wx.Colour(248, 249, 250)        # Alternate row
CLR_HEADER_BG = wx.Colour(52, 73, 94)         # Dark header
CLR_ACCENT = wx.Colour(52, 152, 219)          # Blue accent
CLR_SUCCESS = wx.Colour(46, 204, 113)         # Green success
CLR_WARN = wx.Colour(243, 156, 18)            # Orange warning
CLR_ERROR = wx.Colour(211, 47, 47)            # Red error


class AutoWidthListCtrl(wx.ListCtrl, listmix.ListCtrlAutoWidthMixin):
    """ListCtrl with auto-sizing last column."""
    def __init__(self, parent, style=0):
        wx.ListCtrl.__init__(self, parent, style=style | wx.LC_REPORT | wx.LC_HRULES | wx.LC_VRULES)
        listmix.ListCtrlAutoWidthMixin.__init__(self)


class BOMKartMainDialog(wx.Dialog):
    """Main BOMKart plugin dialog."""

    # Fixed column indices (always present)
    COL_NUM = 0
    COL_REF = 1
    COL_VALUE = 2
    COL_FOOTPRINT = 3
    COL_QTY = 4
    COL_MPN = 5
    COL_BK = 6
    COL_BK_PRICE = 7    # ₹ from BOMKart internal distributor stock
    COL_LCSC = 8        # LCSC part number — always shown (catalog stores it)
    COL_LCSC_PRICE = 9  # ₹ from LCSC external API

    SEARCH_COLUMNS = [
        ("MPN", 180),
        ("Value", 100),
        ("Footprint", 120),
        ("Manufacturer", 120),
        ("₹ Price", 70),
        ("Stock", 60),
        ("Distributor", 120),
    ]

    def __init__(self, parent, bom_items: List[BOMItem], board=None):
        super().__init__(
            parent,
            title="BOMKart — Order Components from Local Distributors",
            size=(1200, 720),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX | wx.MINIMIZE_BOX,
        )
        self.bom_items = bom_items
        self.board = board
        self.api_response = None
        self._api = None
        self._multiplier = 1  # BOM quantity multiplier
        # Dynamic column index tracking (set by _compute_columns)
        # BK, LCSC, ₹BK, ₹LCSC are fixed constants — only Digikey/Mouser are dynamic
        self._col_digikey = None
        self._col_mouser = None
        self._col_stock = None
        self._col_distributor = None

        # Load settings
        from ..config.settings import Settings
        self.settings = Settings()

        self._build_ui()
        self._populate_bom_table()
        self.Centre()

    # ── UI Construction ────────────────────────────────

    def _build_ui(self):
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Header
        main_sizer.Add(self._build_header(panel), 0, wx.EXPAND | wx.ALL, 8)

        # Notebook with tabs: BOM | Search | Settings
        self.notebook = wx.Notebook(panel)

        self.bom_page = wx.Panel(self.notebook)
        self._build_bom_page(self.bom_page)
        self.notebook.AddPage(self.bom_page, "📋 BOM")

        self.search_page = wx.Panel(self.notebook)
        self._build_search_page(self.search_page)
        self.notebook.AddPage(self.search_page, "🔍 Component Search")

        self.settings_page = wx.Panel(self.notebook)
        self._build_settings_page(self.settings_page)
        self.notebook.AddPage(self.settings_page, "⚙ Settings")

        main_sizer.Add(self.notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        # Bottom bar
        main_sizer.Add(self._build_bottom_bar(panel), 0, wx.EXPAND | wx.ALL, 8)

        panel.SetSizer(main_sizer)

    def _build_header(self, parent) -> wx.Sizer:
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Logo text
        title = wx.StaticText(parent, label="BOMKart")
        font = title.GetFont()
        font.SetPointSize(18)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        title.SetFont(font)
        title.SetForegroundColour(CLR_ACCENT)
        sizer.Add(title, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)

        tagline = wx.StaticText(parent, label="Order components from local distributors")
        tagline.SetForegroundColour(wx.Colour(120, 120, 120))
        sizer.Add(tagline, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 20)

        sizer.AddStretchSpacer()

        # Board info
        board_info = f"{len(self.bom_items)} unique parts  •  {sum(i.quantity for i in self.bom_items)} total components"
        self.lbl_board_info = wx.StaticText(parent, label=board_info)
        font2 = self.lbl_board_info.GetFont()
        font2.SetPointSize(10)
        self.lbl_board_info.SetFont(font2)
        sizer.Add(self.lbl_board_info, 0, wx.ALIGN_CENTER_VERTICAL)

        return sizer

    def _build_bom_page(self, parent):
        sizer = wx.BoxSizer(wx.VERTICAL)

        # BOM list control (columns inserted dynamically in _populate_bom_table)
        self.bom_list = AutoWidthListCtrl(parent)
        self.bom_list.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self._on_bom_right_click)
        self.bom_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_bom_double_click)

        sizer.Add(self.bom_list, 1, wx.EXPAND | wx.ALL, 5)

        # Action buttons row
        btn_row = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_check = wx.Button(parent, label="⚡ Check Availability && Pricing")
        self.btn_check.SetMinSize((220, 36))
        self.btn_check.Bind(wx.EVT_BUTTON, self._on_check_availability)
        btn_row.Add(self.btn_check, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)

        # BOM multiplier
        btn_row.Add(wx.StaticText(parent, label="BOM Qty:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self.choice_multiplier = wx.Choice(parent, choices=["1x", "2x", "5x", "10x", "25x", "50x", "100x"])
        self.choice_multiplier.SetSelection(0)
        self.choice_multiplier.Bind(wx.EVT_CHOICE, self._on_multiplier_change)
        btn_row.Add(self.choice_multiplier, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 20)

        self.btn_export_csv = wx.Button(parent, label="📄 Download CSV")
        self.btn_export_csv.Bind(wx.EVT_BUTTON, self._on_export_csv)
        btn_row.Add(self.btn_export_csv, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)

        self.btn_export_json = wx.Button(parent, label="📋 Download JSON")
        self.btn_export_json.Bind(wx.EVT_BUTTON, self._on_export_json)
        btn_row.Add(self.btn_export_json, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)

        btn_row.AddStretchSpacer()

        # Cost summary
        self.lbl_cost = wx.StaticText(parent, label="Total: ₹ —")
        font = self.lbl_cost.GetFont()
        font.SetPointSize(14)
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self.lbl_cost.SetFont(font)
        btn_row.Add(self.lbl_cost, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 15)

        self.btn_order = wx.Button(parent, label="🛒 Place Order")
        self.btn_order.SetMinSize((150, 36))
        self.btn_order.SetBackgroundColour(CLR_SUCCESS)
        self.btn_order.Enable(False)
        self.btn_order.Bind(wx.EVT_BUTTON, self._on_place_order)
        btn_row.Add(self.btn_order, 0)

        sizer.Add(btn_row, 0, wx.EXPAND | wx.ALL, 5)
        parent.SetSizer(sizer)

    def _build_search_page(self, parent):
        """Component search engine — search parts without leaving KiCad."""
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Search bar
        search_row = wx.BoxSizer(wx.HORIZONTAL)

        search_row.Add(
            wx.StaticText(parent, label="Search components:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8,
        )
        self.txt_search = wx.TextCtrl(parent, size=(350, -1), style=wx.TE_PROCESS_ENTER)
        self.txt_search.SetHint("MPN, value, or description — e.g. STM32F103, 100nF 0402, USB-C")
        self.txt_search.Bind(wx.EVT_TEXT_ENTER, self._on_search)
        search_row.Add(self.txt_search, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)

        self.btn_search = wx.Button(parent, label="🔍 Search")
        self.btn_search.Bind(wx.EVT_BUTTON, self._on_search)
        search_row.Add(self.btn_search, 0)

        sizer.Add(search_row, 0, wx.EXPAND | wx.ALL, 8)

        # Category filter
        cat_row = wx.BoxSizer(wx.HORIZONTAL)
        cat_row.Add(wx.StaticText(parent, label="Category:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.choice_category = wx.Choice(parent, choices=[
            "All", "Resistors", "Capacitors", "Inductors", "ICs/MCUs",
            "Connectors", "Diodes", "Transistors", "LEDs", "Crystals",
            "Power/Regulators", "Sensors", "RF/Wireless",
        ])
        self.choice_category.SetSelection(0)
        cat_row.Add(self.choice_category, 0, wx.RIGHT, 20)

        self.lbl_search_status = wx.StaticText(parent, label="")
        self.lbl_search_status.SetForegroundColour(wx.Colour(120, 120, 120))
        cat_row.Add(self.lbl_search_status, 0, wx.ALIGN_CENTER_VERTICAL)

        sizer.Add(cat_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        # Results list
        self.search_list = AutoWidthListCtrl(parent)
        for idx, (name, width) in enumerate(self.SEARCH_COLUMNS):
            self.search_list.InsertColumn(idx, name, width=width)

        sizer.Add(self.search_list, 1, wx.EXPAND | wx.ALL, 8)

        # Hint text
        hint = wx.StaticText(
            parent,
            label="💡 Tip: Search for any MPN, value, or description. "
                  "Double-click a result to see full details and alternatives."
        )
        hint.SetForegroundColour(wx.Colour(150, 150, 150))
        sizer.Add(hint, 0, wx.LEFT | wx.BOTTOM, 8)

        parent.SetSizer(sizer)

    def _build_settings_page(self, parent):
        sizer = wx.FlexGridSizer(cols=2, vgap=10, hgap=15)
        sizer.AddGrowableCol(1, 1)

        # API URL
        sizer.Add(wx.StaticText(parent, label="API Server URL:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_api_url = wx.TextCtrl(parent, value=self.settings.api_url, size=(400, -1))
        sizer.Add(self.txt_api_url, 1, wx.EXPAND)

        # API Key
        sizer.Add(wx.StaticText(parent, label="API Key:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_api_key = wx.TextCtrl(parent, value=self.settings.api_key, style=wx.TE_PASSWORD)
        sizer.Add(self.txt_api_key, 1, wx.EXPAND)

        # Customer info
        sizer.Add(wx.StaticText(parent, label=""), 0)
        sizer.Add(wx.StaticText(parent, label="— Delivery Information —"), 0)

        sizer.Add(wx.StaticText(parent, label="Name:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_cust_name = wx.TextCtrl(parent, value=self.settings.get("customer_name", ""))
        sizer.Add(self.txt_cust_name, 1, wx.EXPAND)

        sizer.Add(wx.StaticText(parent, label="Phone (WhatsApp):"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_cust_phone = wx.TextCtrl(parent, value=self.settings.get("customer_phone", ""))
        sizer.Add(self.txt_cust_phone, 1, wx.EXPAND)

        sizer.Add(wx.StaticText(parent, label="Email:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_cust_email = wx.TextCtrl(parent, value=self.settings.get("customer_email", ""))
        sizer.Add(self.txt_cust_email, 1, wx.EXPAND)

        sizer.Add(wx.StaticText(parent, label="Delivery Address:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_address = wx.TextCtrl(parent, value=self.settings.get("delivery_address", ""), style=wx.TE_MULTILINE, size=(-1, 50))
        sizer.Add(self.txt_address, 1, wx.EXPAND)

        sizer.Add(wx.StaticText(parent, label="Delivery Pincode:"), 0, wx.ALIGN_CENTER_VERTICAL)
        self.txt_pincode = wx.TextCtrl(parent, value=self.settings.get("delivery_pincode", ""))
        sizer.Add(self.txt_pincode, 1, wx.EXPAND)

        # Save button
        sizer.Add(wx.StaticText(parent, label=""), 0)
        self.btn_save_settings = wx.Button(parent, label="💾 Save Settings")
        self.btn_save_settings.Bind(wx.EVT_BUTTON, self._on_save_settings)
        sizer.Add(self.btn_save_settings, 0)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(sizer, 0, wx.EXPAND | wx.ALL, 15)

        # Connection test
        test_row = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_test_conn = wx.Button(parent, label="🔌 Test Connection")
        self.btn_test_conn.Bind(wx.EVT_BUTTON, self._on_test_connection)
        test_row.Add(self.btn_test_conn, 0, wx.RIGHT, 10)
        self.lbl_conn_status = wx.StaticText(parent, label="")
        test_row.Add(self.lbl_conn_status, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(test_row, 0, wx.LEFT, 15)

        parent.SetSizer(outer)

    def _build_bottom_bar(self, parent) -> wx.Sizer:
        sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.status_text = wx.StaticText(parent, label="Ready — click 'Check Availability' to fetch pricing from distributors.")
        self.status_text.SetForegroundColour(wx.Colour(100, 100, 100))
        sizer.Add(self.status_text, 1, wx.ALIGN_CENTER_VERTICAL)

        version = wx.StaticText(parent, label="v0.2.0  •  bomkart.in")
        version.SetForegroundColour(wx.Colour(180, 180, 180))
        sizer.Add(version, 0, wx.ALIGN_CENTER_VERTICAL)
        return sizer

    # ── BOM Table ──────────────────────────────────────

    def _compute_columns(self) -> list:
        """
        Fixed columns: #, Ref, Value, Footprint, Qty, MPN, BK, ₹BK, LCSC, ₹LCSC
        Dynamic columns: Digikey, Mouser (only if schematic has those PNs)
        End columns: Stock, Distributor (internal BK stock status)
        """
        has_digikey = any(item.digikey for item in self.bom_items)
        has_mouser = any(item.mouser for item in self.bom_items)

        columns = [
            ("#",          35),
            ("Ref",       130),
            ("Value",      90),
            ("Footprint",  120),
            ("Qty",        45),
            ("MPN",       140),
            ("BK",         80),
            ("₹ BK",       72),   # COL_BK_PRICE = 7
            ("LCSC",       85),   # COL_LCSC = 8
            ("₹ LCSC",     72),   # COL_LCSC_PRICE = 9
        ]

        self._col_digikey = None
        self._col_mouser = None

        if has_digikey:
            self._col_digikey = len(columns)
            columns.append(("Digikey", 105))
        if has_mouser:
            self._col_mouser = len(columns)
            columns.append(("Mouser", 95))

        self._col_stock = len(columns)
        columns.append(("Stock", 85))
        self._col_distributor = len(columns)
        columns.append(("Distributor", 110))

        return columns

    def _populate_bom_table(self):
        """Fill BOM list control with extracted data."""
        self.bom_list.ClearAll()  # Remove existing columns and items

        columns = self._compute_columns()
        for idx, (name, width) in enumerate(columns):
            self.bom_list.InsertColumn(idx, name, width=width)

        for idx, item in enumerate(self.bom_items):
            row = self.bom_list.InsertItem(idx, str(idx + 1))
            self.bom_list.SetItem(row, self.COL_REF, item.ref_str)
            self.bom_list.SetItem(row, self.COL_VALUE, item.value)
            self.bom_list.SetItem(row, self.COL_FOOTPRINT, item.footprint)
            self.bom_list.SetItem(row, self.COL_QTY, str(item.quantity))
            self.bom_list.SetItem(row, self.COL_MPN, item.mpn or "—")
            self.bom_list.SetItem(row, self.COL_BK, item.bk or "—")
            self.bom_list.SetItem(row, self.COL_BK_PRICE, "—")
            self.bom_list.SetItem(row, self.COL_LCSC, item.lcsc or "—")
            self.bom_list.SetItem(row, self.COL_LCSC_PRICE, "—")
            if self._col_digikey is not None:
                self.bom_list.SetItem(row, self._col_digikey, item.digikey or "—")
            if self._col_mouser is not None:
                self.bom_list.SetItem(row, self._col_mouser, item.mouser or "—")
            self.bom_list.SetItem(row, self._col_stock, "—")
            self.bom_list.SetItem(row, self._col_distributor, "—")
            if idx % 2:
                self.bom_list.SetItemBackgroundColour(row, CLR_ALT_ROW)

    def _set_cell_color(self, row: int, col: int, bg: wx.Colour, text: str):
        """Set per-cell background color in LC_REPORT mode (works on Windows/MSW)."""
        li = wx.ListItem()
        li.SetId(row)
        li.SetColumn(col)
        li.SetText(text)
        li.SetBackgroundColour(bg)
        self.bom_list.SetItem(li)

    def _update_bom_with_pricing(self, response: dict):
        """Update BOM table rows with API pricing response."""
        self.api_response = response
        items_data = response.get("items", [])
        total_cost = 0.0
        CLR_PRICE_BEST = wx.Colour(200, 245, 200)   # light green — cheapest price

        for idx, api_item in enumerate(items_data):
            if idx >= len(self.bom_items):
                break

            bom_item = self.bom_items[idx]
            offers = api_item.get("offers", [])
            status = api_item.get("status", "unavailable")

            # Populate MPN from API if not in schematic
            resolved_mpn = api_item.get("resolved_mpn", "")
            if resolved_mpn and not bom_item.mpn:
                bom_item.mpn = resolved_mpn
                self.bom_list.SetItem(idx, self.COL_MPN, resolved_mpn)

            # Populate BK from API
            resolved_bk = api_item.get("resolved_bk", "")
            if resolved_bk and not bom_item.bk:
                bom_item.bk = resolved_bk
            if bom_item.bk:
                self.bom_list.SetItem(idx, self.COL_BK, bom_item.bk)

            # Populate LCSC from API
            resolved_lcsc = api_item.get("resolved_lcsc", "")
            if resolved_lcsc and not bom_item.lcsc:
                bom_item.lcsc = resolved_lcsc
            if bom_item.lcsc:
                self.bom_list.SetItem(idx, self.COL_LCSC, bom_item.lcsc)

            # Separate internal (BK) offers from LCSC external offers
            bk_offers = [o for o in offers if o.get("distributor_id") != "external-lcsc"]
            lcsc_offers = [o for o in offers if o.get("distributor_id") == "external-lcsc"]

            bk_price = min(o.get("unit_price", 0) for o in bk_offers) if bk_offers else 0.0
            lcsc_price = lcsc_offers[0].get("unit_price", 0) if lcsc_offers else 0.0

            # Set ₹ BK price cell
            bk_text = f"₹{bk_price:.2f}" if bk_price else "—"
            self.bom_list.SetItem(idx, self.COL_BK_PRICE, bk_text)

            # Set ₹ LCSC price cell
            lcsc_text = f"₹{lcsc_price:.2f}" if lcsc_price else "—"
            self.bom_list.SetItem(idx, self.COL_LCSC_PRICE, lcsc_text)

            # Highlight cheapest price cell in light green
            if bk_price and lcsc_price:
                if bk_price <= lcsc_price:
                    self._set_cell_color(idx, self.COL_BK_PRICE, CLR_PRICE_BEST, bk_text)
                else:
                    self._set_cell_color(idx, self.COL_LCSC_PRICE, CLR_PRICE_BEST, lcsc_text)
            elif bk_price:
                self._set_cell_color(idx, self.COL_BK_PRICE, CLR_PRICE_BEST, bk_text)
            elif lcsc_price:
                self._set_cell_color(idx, self.COL_LCSC_PRICE, CLR_PRICE_BEST, lcsc_text)

            # Store individual prices on item (for CSV export)
            bom_item.bk_price = bk_price
            bom_item.lcsc_price = lcsc_price

            # Best price for totals
            best_price = 0.0
            if bk_price and lcsc_price:
                best_price = min(bk_price, lcsc_price)
            elif bk_price:
                best_price = bk_price
            elif lcsc_price:
                best_price = lcsc_price

            if best_price:
                total_cost += best_price * bom_item.quantity
                bom_item.unit_price = best_price
                bom_item.total_price = best_price * bom_item.quantity

            # Internal distributor name (for Stock/Distributor columns)
            if bk_offers:
                best_bk = min(bk_offers, key=lambda o: o.get("unit_price", 999999))
                bom_item.distributor = best_bk.get("distributor_name", "")
                self.bom_list.SetItem(idx, self._col_distributor, bom_item.distributor)

            bom_item.alternatives = api_item.get("alternatives", [])

            # Availability + row color
            if status == "available":
                bom_item.availability = "available"
                self.bom_list.SetItem(idx, self._col_stock, "✅ In Stock")
                self.bom_list.SetItemBackgroundColour(idx, CLR_GREEN_BG)
            elif status == "partial":
                bom_item.availability = "partial"
                self.bom_list.SetItem(idx, self._col_stock, "⚠️ Partial")
                self.bom_list.SetItemBackgroundColour(idx, CLR_YELLOW_BG)
            else:
                bom_item.availability = "unavailable"
                self.bom_list.SetItem(idx, self._col_stock, "❌ Not Found")
                self.bom_list.SetItemBackgroundColour(idx, CLR_RED_BG)

            # Re-apply price cell colors after row color (cell color must come after row color)
            if bk_price and lcsc_price:
                if bk_price <= lcsc_price:
                    self._set_cell_color(idx, self.COL_BK_PRICE, CLR_PRICE_BEST, bk_text)
                else:
                    self._set_cell_color(idx, self.COL_LCSC_PRICE, CLR_PRICE_BEST, lcsc_text)
            elif bk_price:
                self._set_cell_color(idx, self.COL_BK_PRICE, CLR_PRICE_BEST, bk_text)
            elif lcsc_price:
                self._set_cell_color(idx, self.COL_LCSC_PRICE, CLR_PRICE_BEST, lcsc_text)

        self.lbl_cost.SetLabel(f"Total: ₹{total_cost:,.2f}")
        self.btn_order.Enable(True)

        summary = response.get("summary", {})
        avail = summary.get("items_available", 0)
        unavail = summary.get("items_unavailable", 0)
        delivery = summary.get("estimated_delivery", "N/A")
        self._set_status(
            f"✅ {avail} available, {unavail} unavailable  •  Est. delivery: {delivery}",
            CLR_SUCCESS,
        )

    # ── Event Handlers ─────────────────────────────────

    def _get_api(self):
        """Lazy-init API client with current settings."""
        if self._api is None:
            from ..api_client import BOMKartAPI
            url = self.txt_api_url.GetValue().strip() if hasattr(self, 'txt_api_url') else self.settings.api_url
            key = self.txt_api_key.GetValue().strip() if hasattr(self, 'txt_api_key') else self.settings.api_key
            self._api = BOMKartAPI(base_url=url, api_key=key)
        return self._api

    def _on_multiplier_change(self, event):
        label = self.choice_multiplier.GetStringSelection()  # e.g. "10x"
        self._multiplier = int(label.replace("x", ""))
        # Update Qty column instantly
        total_cost = 0.0
        for idx, item in enumerate(self.bom_items):
            scaled_qty = item.quantity * self._multiplier
            self.bom_list.SetItem(idx, self.COL_QTY, str(scaled_qty))
            # Recalculate price if already fetched
            if item.unit_price:
                line_total = item.unit_price * scaled_qty
                total_cost += line_total
                item.total_price = line_total
        if any(i.unit_price for i in self.bom_items):
            self.lbl_cost.SetLabel(f"Total: ₹{total_cost:,.2f}")

        self._set_status(
            f"BOM multiplier set to {self._multiplier}x — "
            f"{sum(i.quantity * self._multiplier for i in self.bom_items)} total components",
            CLR_ACCENT,
        )

    def _on_check_availability(self, event):
        self.btn_check.Enable(False)
        self._set_status("🔄 Querying distributors... please wait.", CLR_ACCENT)
        self._api = None  # Force re-init with latest settings
        t = threading.Thread(target=self._fetch_availability_thread, daemon=True)
        t.start()

    def _fetch_availability_thread(self):
        try:
            api = self._get_api()
            # Send multiplied quantities so stock check is accurate
            items = []
            for item in self.bom_items:
                d = item.to_api_dict()
                d["quantity"] = item.quantity * self._multiplier
                items.append(d)
            payload = {"items": items}
            response = api.check_bom(payload)
            wx.CallAfter(self._update_bom_with_pricing, response)
        except Exception as e:
            wx.CallAfter(self._on_api_error, str(e))
        finally:
            wx.CallAfter(lambda: self.btn_check.Enable(True))

    def _on_api_error(self, msg: str):
        self._set_status(f"❌ Error: {msg}", CLR_ERROR)

    def _on_search(self, event):
        query = self.txt_search.GetValue().strip()
        if not query:
            return
        self.btn_search.Enable(False)
        self.lbl_search_status.SetLabel("Searching...")
        self._api = None
        t = threading.Thread(target=self._search_thread, args=(query,), daemon=True)
        t.start()

    def _search_thread(self, query: str):
        try:
            api = self._get_api()
            cat = self.choice_category.GetStringSelection()
            category = "" if cat == "All" else cat.lower()
            result = api.search_component(query, category=category)
            wx.CallAfter(self._update_search_results, result)
        except Exception as e:
            wx.CallAfter(self._search_error, str(e))
        finally:
            wx.CallAfter(lambda: self.btn_search.Enable(True))

    def _update_search_results(self, result: dict):
        self.search_list.DeleteAllItems()
        components = result.get("components", [])
        self.lbl_search_status.SetLabel(f"{len(components)} results found")

        for idx, comp in enumerate(components):
            row = self.search_list.InsertItem(idx, comp.get("mpn", "—"))
            self.search_list.SetItem(row, 1, comp.get("value", "—"))
            self.search_list.SetItem(row, 2, comp.get("footprint", "—"))
            self.search_list.SetItem(row, 3, comp.get("manufacturer", "—"))
            price = comp.get("unit_price", 0)
            self.search_list.SetItem(row, 4, f"₹{price:.2f}" if price else "—")
            self.search_list.SetItem(row, 5, str(comp.get("available_qty", "—")))
            self.search_list.SetItem(row, 6, comp.get("distributor_name", "—"))
            if idx % 2:
                self.search_list.SetItemBackgroundColour(row, CLR_ALT_ROW)

    def _search_error(self, msg: str):
        self.lbl_search_status.SetLabel(f"Error: {msg}")
        self.lbl_search_status.SetForegroundColour(CLR_ERROR)

    def _on_bom_right_click(self, event):
        """Right-click context menu on BOM item."""
        idx = event.GetIndex()
        if idx < 0 or idx >= len(self.bom_items):
            return

        item = self.bom_items[idx]
        menu = wx.Menu()

        if item.mpn:
            mi_search = menu.Append(wx.ID_ANY, f"🔍 Search for '{item.mpn}'")
            self.Bind(wx.EVT_MENU, lambda e: self._search_for_part(item.mpn), mi_search)

        if item.alternatives:
            mi_alt = menu.Append(wx.ID_ANY, f"🔄 Show {len(item.alternatives)} alternatives")
            self.Bind(wx.EVT_MENU, lambda e: self._show_alternatives(item), mi_alt)

        if item.mpn or item.lcsc or item.mouser or item.digikey:
            menu.AppendSeparator()
            if item.mpn:
                mi_copy = menu.Append(wx.ID_ANY, f"📋 Copy MPN: {item.mpn}")
                self.Bind(wx.EVT_MENU, lambda e: self._copy_to_clipboard(item.mpn), mi_copy)
            if item.lcsc:
                mi_lcsc = menu.Append(wx.ID_ANY, f"📋 Copy LCSC: {item.lcsc}")
                self.Bind(wx.EVT_MENU, lambda e: self._copy_to_clipboard(item.lcsc), mi_lcsc)

        self.PopupMenu(menu)
        menu.Destroy()

    def _on_bom_double_click(self, event):
        """Double-click a BOM row to search for it."""
        idx = event.GetIndex()
        if 0 <= idx < len(self.bom_items):
            item = self.bom_items[idx]
            query = item.mpn or item.value
            self.txt_search.SetValue(query)
            self.notebook.SetSelection(1)  # Switch to search tab
            self._on_search(None)

    def _search_for_part(self, query: str):
        self.txt_search.SetValue(query)
        self.notebook.SetSelection(1)
        self._on_search(None)

    def _show_alternatives(self, item: BOMItem):
        """Show alternatives dialog for a BOM item."""
        alts = item.alternatives
        if not alts:
            wx.MessageBox("No alternatives found.", "BOMKart", wx.OK)
            return

        msg = f"Alternatives for {item.mpn or item.value}:\n\n"
        for i, alt in enumerate(alts[:10], 1):
            msg += f"{i}. {alt.get('mpn', '?')} — ₹{alt.get('unit_price', '?')} ({alt.get('distributor_name', '?')})\n"

        wx.MessageBox(msg, "BOMKart — Alternatives", wx.OK | wx.ICON_INFORMATION)

    def _copy_to_clipboard(self, text: str):
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(text))
            wx.TheClipboard.Close()
            self._set_status(f"Copied: {text}", CLR_ACCENT)

    # ── Export ─────────────────────────────────────────

    def _on_export_csv(self, event):
        dlg = wx.FileDialog(
            self, "Save BOM as CSV",
            wildcard="CSV files (*.csv)|*.csv",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            defaultFile="bomkart_bom.csv",
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            try:
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    has_digikey = any(i.digikey for i in self.bom_items)
                    has_mouser = any(i.mouser for i in self.bom_items)
                    multiplier = self._multiplier

                    header = ["References", "Value", "Footprint", "Qty per BOM",
                              f"Qty ({multiplier}x BOM)", "MPN",
                              "BK", "₹ BK", "LCSC", "₹ LCSC"]
                    if has_digikey:
                        header.append("Digikey")
                    if has_mouser:
                        header.append("Mouser")
                    header += ["Manufacturer", f"Best Price (INR)",
                               f"Line Total ({multiplier}x BOM)",
                               "Stock", "Distributor"]
                    writer.writerow(header)

                    for item in self.bom_items:
                        scaled_qty = item.quantity * multiplier
                        line_total = item.unit_price * scaled_qty if item.unit_price else ""
                        bk_price_str = f"{item.bk_price:.2f}" if getattr(item, "bk_price", 0) else ""
                        lcsc_price_str = f"{item.lcsc_price:.2f}" if getattr(item, "lcsc_price", 0) else ""
                        row = [
                            item.ref_str, item.value, item.footprint,
                            item.quantity, scaled_qty,
                            item.mpn, item.bk, bk_price_str, item.lcsc, lcsc_price_str,
                        ]
                        if has_digikey:
                            row.append(item.digikey)
                        if has_mouser:
                            row.append(item.mouser)
                        row += [
                            item.manufacturer,
                            f"{item.unit_price:.2f}" if item.unit_price else "",
                            f"{line_total:.2f}" if line_total else "",
                            item.availability or "",
                            item.distributor,
                        ]
                        writer.writerow(row)
                self._set_status(f"✅ BOM exported to {path}", CLR_SUCCESS)
            except Exception as e:
                wx.MessageBox(f"Export failed: {e}", "Error", wx.OK | wx.ICON_ERROR)
        dlg.Destroy()

    def _on_export_json(self, event):
        dlg = wx.FileDialog(
            self, "Save BOM as JSON",
            wildcard="JSON files (*.json)|*.json",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            defaultFile="bomkart_bom.json",
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            try:
                data = {
                    "bomkart_version": "0.2.0",
                    "total_unique_parts": len(self.bom_items),
                    "total_components": sum(i.quantity for i in self.bom_items),
                    "items": [i.to_api_dict() for i in self.bom_items],
                }
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                self._set_status(f"✅ BOM exported to {path}", CLR_SUCCESS)
            except Exception as e:
                wx.MessageBox(f"Export failed: {e}", "Error", wx.OK | wx.ICON_ERROR)
        dlg.Destroy()

    # ── Settings ───────────────────────────────────────

    def _on_save_settings(self, event):
        self.settings.api_url = self.txt_api_url.GetValue().strip()
        self.settings.api_key = self.txt_api_key.GetValue().strip()
        self.settings["customer_name"] = self.txt_cust_name.GetValue().strip()
        self.settings["customer_phone"] = self.txt_cust_phone.GetValue().strip()
        self.settings["customer_email"] = self.txt_cust_email.GetValue().strip()
        self.settings["delivery_address"] = self.txt_address.GetValue().strip()
        self.settings["delivery_pincode"] = self.txt_pincode.GetValue().strip()
        self.settings.save()
        self._api = None  # Force re-init
        self._set_status("✅ Settings saved.", CLR_SUCCESS)

    def _on_test_connection(self, event):
        self.lbl_conn_status.SetLabel("Testing...")
        self.lbl_conn_status.SetForegroundColour(CLR_ACCENT)
        self._api = None

        def _test():
            try:
                api = self._get_api()
                ok = api.health()
                if ok:
                    wx.CallAfter(self.lbl_conn_status.SetLabel, "✅ Connected!")
                    wx.CallAfter(self.lbl_conn_status.SetForegroundColour, CLR_SUCCESS)
                else:
                    wx.CallAfter(self.lbl_conn_status.SetLabel, "❌ Server returned unhealthy status")
                    wx.CallAfter(self.lbl_conn_status.SetForegroundColour, CLR_ERROR)
            except Exception as e:
                wx.CallAfter(self.lbl_conn_status.SetLabel, f"❌ {str(e)[:80]}")
                wx.CallAfter(self.lbl_conn_status.SetForegroundColour, CLR_ERROR)

        threading.Thread(target=_test, daemon=True).start()

    # ── Order ──────────────────────────────────────────

    def _on_place_order(self, event):
        if not self.api_response:
            wx.MessageBox("Please check availability first.", "BOMKart", wx.OK | wx.ICON_WARNING)
            return

        # Validate customer info
        name = self.settings.get("customer_name", "")
        phone = self.settings.get("customer_phone", "")
        if not name or not phone:
            wx.MessageBox(
                "Please fill in your delivery details in the Settings tab first.\n"
                "Name and Phone number are required.",
                "BOMKart", wx.OK | wx.ICON_WARNING,
            )
            self.notebook.SetSelection(2)
            return

        # Confirm
        available = sum(1 for i in self.bom_items if i.availability == "available")
        unavailable = sum(1 for i in self.bom_items if i.availability == "unavailable")
        cost_text = self.lbl_cost.GetLabel()

        msg = (
            f"Place order for {available} available items?\n\n"
            f"{cost_text}\n"
        )
        if unavailable:
            msg += f"\n⚠️ {unavailable} items are unavailable and won't be ordered.\n"
        msg += (
            f"\nDelivery to: {self.settings.get('delivery_pincode', 'N/A')}\n"
            f"Confirmation will be sent to: {phone}\n"
        )

        dlg = wx.MessageDialog(self, msg, "Confirm Order", wx.YES_NO | wx.ICON_QUESTION)
        if dlg.ShowModal() == wx.ID_YES:
            self._set_status("🔄 Placing order...", CLR_ACCENT)
            t = threading.Thread(target=self._place_order_thread, daemon=True)
            t.start()
        dlg.Destroy()

    def _place_order_thread(self):
        try:
            api = self._get_api()
            order_data = {
                "request_id": self.api_response.get("request_id", ""),
                "customer_name": self.settings.get("customer_name", ""),
                "customer_phone": self.settings.get("customer_phone", ""),
                "customer_email": self.settings.get("customer_email", ""),
                "delivery_address": self.settings.get("delivery_address", ""),
                "delivery_pincode": self.settings.get("delivery_pincode", ""),
                "items": [
                    i.to_api_dict() for i in self.bom_items
                    if i.availability in ("available", "partial")
                ],
            }
            result = api.place_order(order_data)
            order_id = result.get("order_id", "N/A")
            self.settings["last_order_id"] = order_id
            self.settings.save()

            wx.CallAfter(
                wx.MessageBox,
                f"Order submitted! 🎉\n\n"
                f"Order ID: {order_id}\n"
                f"Status: Broadcast to distributors\n\n"
                f"You'll receive confirmation on WhatsApp at {self.settings.get('customer_phone', '')}.\n"
                f"Track at: bomkart.in/orders/{order_id}",
                "BOMKart — Order Placed",
                wx.OK | wx.ICON_INFORMATION,
            )
            wx.CallAfter(self._set_status, f"✅ Order {order_id} placed!", CLR_SUCCESS)
        except Exception as e:
            wx.CallAfter(self._on_api_error, str(e))

    # ── Helpers ────────────────────────────────────────

    def _set_status(self, text: str, color: wx.Colour = wx.Colour(100, 100, 100)):
        self.status_text.SetLabel(text)
        self.status_text.SetForegroundColour(color)
