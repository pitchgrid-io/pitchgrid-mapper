"""Dump and lightly parse the MAD68 HID report descriptors for every interface.

The report descriptor is ground truth: it declares each report's fields, sizes,
and logical ranges. Analog keys would appear as multi-bit fields with a large
Logical Maximum (e.g. 255); a pure digital keyboard/NKRO shows 1-bit boolean
arrays or keycode-usage arrays (Logical Max 1, or keyboard usage page).
"""
import hid

VID, PID = 0x373B, 0x1058

ITEM_NAMES = {
    0x04: "UsagePage", 0x08: "Usage", 0x14: "LogicalMin", 0x24: "LogicalMax",
    0x74: "ReportSize", 0x94: "ReportCount", 0x84: "ReportID",
    0x80: "Input", 0x90: "Output", 0xA0: "Collection", 0xC0: "EndCollection",
    0xA4: "Push", 0xB4: "Pop", 0x18: "UsageMin", 0x28: "UsageMax",
}


def parse(desc):
    i, out = 0, []
    while i < len(desc):
        b = desc[i]
        if b == 0xC0:
            out.append(("EndCollection", None)); i += 1; continue
        size = b & 0x03
        size = 4 if size == 3 else size
        tag = b & 0xFC
        val = 0
        for k in range(size):
            val |= desc[i + 1 + k] << (8 * k)
        out.append((ITEM_NAMES.get(tag, f"0x{tag:02x}"), val))
        i += 1 + size
    return out


seen_paths = set()
for d in hid.enumerate(VID, PID):
    path = d["path"]
    key = (d.get("usage_page"), d.get("usage"), d.get("interface_number"))
    h = hid.device()
    try:
        h.open_path(path)
    except Exception as e:
        print(f"\n=== iface={d['interface_number']} up={d.get('usage_page'):#06x} u={d.get('usage'):#06x}: OPEN FAILED ({e})")
        continue
    try:
        desc = bytes(h.get_report_descriptor())
    except Exception as e:
        print(f"\n=== iface={d['interface_number']}: descriptor error {e}")
        h.close(); continue
    h.close()
    if desc in seen_paths:
        continue
    seen_paths.add(desc)
    print(f"\n=== iface={d['interface_number']} up={d.get('usage_page'):#06x} u={d.get('usage'):#06x}  ({len(desc)} bytes) ===")
    items = parse(desc)
    cur_page = None
    cur_rid = None
    for name, val in items:
        if name == "UsagePage":
            cur_page = val
        if name == "ReportID":
            cur_rid = val
        # Highlight the analog tell-tale: LogicalMax >1 on Input fields
        flag = ""
        if name == "LogicalMax" and val is not None and val > 1:
            flag = "   <-- multi-value (possible analog)"
        if name in ("ReportID", "Input", "Output", "LogicalMax", "ReportSize",
                    "ReportCount", "UsagePage", "Usage", "Collection", "EndCollection"):
            extra = f" rid={cur_rid}" if name in ("Input", "Output") else ""
            print(f"  {name:13s} {val if val is not None else ''}{extra}{flag}")
