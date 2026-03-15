"""
BOM Extractor — Reads KiCad PCB board and produces a structured, grouped BOM.

Supports KiCad 7.x and 8.x APIs.
Groups identical components by (value, footprint, MPN).
Extracts MPN, LCSC, Mouser, DigiKey part numbers from symbol fields.
Skips fiducials, mounting holes, test points, logos, and DNP-marked components.
"""

import pcbnew
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple


# Field names engineers commonly use for part numbers
MPN_FIELDS = [
    "MPN", "Mfr_PN", "Manufacturer Part Number", "Manufacturer_Part_Number",
    "PartNumber", "Part Number", "Part_Number", "Mfg_Part_No",
]
LCSC_FIELDS = [
    "LCSC", "LCSC_Part", "LCSC Part", "JLCPCB_Part", "JLCPCB Part", "JLC",
]
MOUSER_FIELDS = [
    "Mouser", "Mouser_PN", "Mouser Part", "Mouser_Part_Number",
]
DIGIKEY_FIELDS = [
    "DigiKey", "Digikey_PN", "DigiKey Part", "Digi-Key_PN", "DK_PN",
]
BOMKART_FIELDS = [
    "BOMKart", "BK", "BOMKart_PN", "bomkart_pn", "BomKart",
]
MANUFACTURER_FIELDS = [
    "Manufacturer", "Mfr", "MFG", "Mfg",
]
DESCRIPTION_FIELDS = [
    "Description", "Desc", "ki_description",
]

# Reference prefixes to skip (non-BOM items)
SKIP_PREFIXES = ("FID", "MH", "TP", "G", "LOGO", "H", "NT", "MP")


@dataclass
class BOMItem:
    """A single grouped BOM line item."""
    value: str
    footprint: str
    references: List[str] = field(default_factory=list)
    quantity: int = 0
    mpn: str = ""
    bk: str = ""
    lcsc: str = ""
    mouser: str = ""
    digikey: str = ""
    manufacturer: str = ""
    description: str = ""
    # Populated after API call
    unit_price: float = 0.0   # best price (min of bk_price, lcsc_price)
    bk_price: float = 0.0     # price from BOMKart internal distributor stock
    lcsc_price: float = 0.0   # price from LCSC external API
    total_price: float = 0.0
    availability: str = ""  # "available", "partial", "unavailable", ""
    distributor: str = ""
    alternatives: List[Dict] = field(default_factory=list)

    @property
    def group_key(self) -> str:
        return f"{self.value}||{self.footprint}||{self.mpn}"

    @property
    def ref_prefix(self) -> str:
        """Extract prefix letter(s) from first reference for sorting."""
        if self.references:
            m = re.match(r"^([A-Za-z]+)", self.references[0])
            return m.group(1) if m else "Z"
        return "Z"

    @property
    def ref_str(self) -> str:
        """Compact reference string: R1, R2, R3 → R1-R3; C1, C3, C5 → C1, C3, C5"""
        if not self.references:
            return ""
        return self._compress_refs(sorted(self.references, key=self._ref_sort_key))

    def _ref_sort_key(self, ref: str) -> Tuple[str, int]:
        m = re.match(r"^([A-Za-z]+)(\d+)$", ref)
        if m:
            return (m.group(1), int(m.group(2)))
        return (ref, 0)

    def _compress_refs(self, refs: List[str]) -> str:
        """Compress sequential references: R1, R2, R3, R5 → R1-R3, R5"""
        if len(refs) <= 3:
            return ", ".join(refs)

        groups = []
        i = 0
        while i < len(refs):
            start = refs[i]
            m_start = re.match(r"^([A-Za-z]+)(\d+)$", start)
            if not m_start:
                groups.append(start)
                i += 1
                continue

            prefix, num = m_start.group(1), int(m_start.group(2))
            end_num = num
            j = i + 1
            while j < len(refs):
                m_next = re.match(r"^([A-Za-z]+)(\d+)$", refs[j])
                if m_next and m_next.group(1) == prefix and int(m_next.group(2)) == end_num + 1:
                    end_num = int(m_next.group(2))
                    j += 1
                else:
                    break

            if end_num > num + 1:
                groups.append(f"{prefix}{num}-{prefix}{end_num}")
            elif end_num == num + 1:
                groups.append(f"{prefix}{num}, {prefix}{end_num}")
            else:
                groups.append(start)
            i = j

        return ", ".join(groups)

    def to_api_dict(self) -> dict:
        """Serialize for API request."""
        return {
            "value": self.value,
            "footprint": self.footprint,
            "quantity": self.quantity,
            "mpn": self.mpn,
            "bk": self.bk,
            "lcsc": self.lcsc,
            "mouser": self.mouser,
            "digikey": self.digikey,
            "manufacturer": self.manufacturer,
            "description": self.description,
            "references": self.references,
        }


class BOMExtractor:
    """Extracts grouped BOM from a KiCad pcbnew BOARD object."""

    def __init__(self, board: pcbnew.BOARD):
        self.board = board

    def _get_field(self, fp: pcbnew.FOOTPRINT, field_names: List[str]) -> str:
        """
        Try multiple field names, return first non-empty value found.
        Compatible with KiCad 7.x and 8.x APIs.
        """
        for name in field_names:
            try:
                # KiCad 8.x: GetFieldText raises KeyError if field doesn't exist
                val = fp.GetFieldText(name).strip()
                if val:
                    return val
            except (KeyError, RuntimeError, AttributeError):
                pass

            try:
                # KiCad 8.x: GetFieldByName returns PCB_FIELD or None
                f = fp.GetFieldByName(name)
                if f:
                    val = f.GetText().strip()
                    if val:
                        return val
            except (RuntimeError, AttributeError):
                pass

        # Fallback: iterate all fields
        try:
            for f in fp.GetFields():
                fname = f.GetName().strip()
                for target in field_names:
                    if fname.lower() == target.lower():
                        val = f.GetText().strip()
                        if val:
                            return val
        except (RuntimeError, AttributeError):
            pass

        return ""

    def _should_skip(self, fp: pcbnew.FOOTPRINT) -> bool:
        """Check if footprint should be excluded from BOM."""
        ref = fp.GetReference()

        # Skip by reference prefix
        for prefix in SKIP_PREFIXES:
            if ref.startswith(prefix):
                return True

        # Skip if excluded from BOM (KiCad 8)
        try:
            if fp.IsExcludedFromBOM():
                return True
        except AttributeError:
            pass

        # Skip if DNP field set
        try:
            if fp.IsDNP():
                return True
        except AttributeError:
            pass

        # Check manual DNP field
        dnp = self._get_field(fp, ["DNP", "dnp", "Do Not Place"])
        if dnp.lower() in ("yes", "1", "true", "dnp", "y"):
            return True

        # Skip if value is empty or just a reference
        val = fp.GetValue().strip()
        if not val or val == ref:
            return True

        return False

    def _get_footprint_short_name(self, fp: pcbnew.FOOTPRINT) -> str:
        """Get a clean footprint name from the FPID."""
        try:
            # KiCad 8
            fpid = fp.GetFPID()
            lib_item = str(fpid.GetUniStringLibItemName())
            if lib_item:
                return lib_item
        except (RuntimeError, AttributeError):
            pass

        try:
            # Fallback
            return str(fp.GetFPID().GetFootprintName())
        except (RuntimeError, AttributeError):
            pass

        return "Unknown"

    def extract(self) -> List[BOMItem]:
        """
        Extract and group BOM from board.

        Returns list of BOMItem sorted by reference prefix then numeric value.
        """
        groups: OrderedDict[str, BOMItem] = OrderedDict()

        for fp in self.board.GetFootprints():
            if self._should_skip(fp):
                continue

            ref = fp.GetReference()
            value = fp.GetValue().strip()
            footprint = self._get_footprint_short_name(fp)

            mpn = self._get_field(fp, MPN_FIELDS)
            bk = self._get_field(fp, BOMKART_FIELDS)
            lcsc = self._get_field(fp, LCSC_FIELDS)
            mouser = self._get_field(fp, MOUSER_FIELDS)
            digikey = self._get_field(fp, DIGIKEY_FIELDS)
            manufacturer = self._get_field(fp, MANUFACTURER_FIELDS)
            description = self._get_field(fp, DESCRIPTION_FIELDS)

            # Group key: value + footprint + mpn (or first available identifier)
            key = f"{value}||{footprint}||{mpn or bk or lcsc or digikey or mouser}"

            if key not in groups:
                groups[key] = BOMItem(
                    value=value,
                    footprint=footprint,
                    mpn=mpn,
                    bk=bk,
                    lcsc=lcsc,
                    mouser=mouser,
                    digikey=digikey,
                    manufacturer=manufacturer,
                    description=description,
                )

            groups[key].references.append(ref)
            groups[key].quantity += 1

            # Merge part numbers if one instance has it and another doesn't
            item = groups[key]
            if not item.mpn and mpn:
                item.mpn = mpn
            if not item.bk and bk:
                item.bk = bk
            if not item.lcsc and lcsc:
                item.lcsc = lcsc
            if not item.mouser and mouser:
                item.mouser = mouser
            if not item.digikey and digikey:
                item.digikey = digikey
            if not item.manufacturer and manufacturer:
                item.manufacturer = manufacturer
            if not item.description and description:
                item.description = description

        # Sort by reference prefix (C, D, J, L, Q, R, U...) then value
        items = sorted(
            groups.values(),
            key=lambda x: (x.ref_prefix, x.value),
        )

        return items

    def get_board_info(self) -> dict:
        """Get metadata about the current board."""
        info = {
            "filename": "",
            "title": "",
            "revision": "",
            "date": "",
        }
        try:
            info["filename"] = self.board.GetFileName()
        except Exception:
            pass
        try:
            ts = self.board.GetTitleBlock()
            info["title"] = ts.GetTitle()
            info["revision"] = ts.GetRevision()
            info["date"] = ts.GetDate()
        except Exception:
            pass
        return info

    def to_api_payload(self, items: List[BOMItem]) -> dict:
        """Build API request payload from BOM items."""
        board_info = self.get_board_info()
        return {
            "board_name": board_info.get("title") or board_info.get("filename", ""),
            "board_revision": board_info.get("revision", ""),
            "total_unique_parts": len(items),
            "total_components": sum(i.quantity for i in items),
            "items": [i.to_api_dict() for i in items],
        }
