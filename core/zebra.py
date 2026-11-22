import base64
import io
import json
import socket

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, ImageDraw, ImageFont

CODE128_PATTERNS = [
    "212222", "222122", "22221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232", "2331112",
]

ZEBRA_LABEL_PRESETS = {
    "50 x 25 mm (2\" x 1\" Product Sticker)": {"width_mm": 50, "height_mm": 25, "pw": 406, "ll": 203, "bc_h": 75, "by": 2},
    "57 x 32 mm (2.25\" x 1.25\" Standard)": {"width_mm": 57, "height_mm": 32, "pw": 464, "ll": 256, "bc_h": 95, "by": 2},
    "100 x 50 mm (4\" x 2\" Shelf / Bin / Carton)": {"width_mm": 100, "height_mm": 50, "pw": 812, "ll": 406, "bc_h": 160, "by": 3},
}


@st.cache_data(max_entries=256, show_spinner=False)
def render_code128_image(data: str, module_width: int = 3, bar_height: int = 120, quiet_zone: int = 24) -> Image.Image:
    clean_text = "".join(ch if 32 <= ord(ch) <= 126 else "-" for ch in str(data))
    codes = [104]
    checksum = 104
    for idx, ch in enumerate(clean_text, start=1):
        val = ord(ch) - 32
        codes.append(val)
        checksum += val * idx
    codes.append(checksum % 103)
    codes.append(106)

    modules = []
    for c in codes:
        pattern = CODE128_PATTERNS[c]
        for i, width_char in enumerate(pattern):
            modules.extend([1 if i % 2 == 0 else 0] * int(width_char))

    img_w = len(modules) * module_width + quiet_zone * 2
    img_h = bar_height + 42
    img = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(img)

    x = quiet_zone
    for bit in modules:
        if bit == 1:
            draw.rectangle([x, 8, x + module_width - 1, 8 + bar_height], fill="black")
        x += module_width

    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), clean_text, font=font)
    text_x = max(4, (img_w - (bbox[2] - bbox[0])) // 2)
    draw.text((text_x, bar_height + 16), clean_text, fill="black", font=font)
    return img


def sanitize_zpl_text(val: str) -> str:
    return str(val or "").replace("^", " ").replace("~", " ").strip()


def generate_zebra_zpl(
    barcode_value: str,
    title: str,
    subtitle: str = "",
    preset_name: str = "50 x 25 mm (2\" x 1\" Product Sticker)",
    copies: int = 1,
    darkness: int = 15,
) -> str:
    p = ZEBRA_LABEL_PRESETS[preset_name]
    pw, ll, bc_h, by = p["pw"], p["ll"], p["bc_h"], p["by"]

    clean_code = sanitize_zpl_text(barcode_value)
    clean_title = sanitize_zpl_text(title)[:44]
    clean_sub = sanitize_zpl_text(subtitle)[:44]

    title_font_h = 22 if pw <= 464 else 32
    title_font_w = 22 if pw <= 464 else 32
    bc_y = 42 if pw <= 464 else 65
    sub_y = bc_y + bc_h + 34

    sub_block = (
        f"^FO20,{sub_y}^A0N,{title_font_h - 4},{title_font_w - 4}^FB{pw - 40},1,0,C^FD{clean_sub}^FS"
        if clean_sub
        else ""
    )

    return f"""^XA
^CI28
^PW{pw}
^LL{ll}
^MD{darkness}
^FO20,12^A0N,{title_font_h},{title_font_w}^FB{pw - 40},1,0,C^FD{clean_title}^FS
^FO30,{bc_y}^BY{by},2,{bc_h}^BCN,{bc_h},Y,N,N^FD{clean_code}^FS
{sub_block}
^PQ{max(1, int(copies))},0,1,Y
^XZ"""


def build_label_canvas_and_png(barcode_val: str, label_title: str, label_sub: str, preset_name: str) -> tuple[Image.Image, bytes, str]:
    preset_dims = ZEBRA_LABEL_PRESETS[preset_name]
    bc_img = render_code128_image(barcode_val, module_width=3, bar_height=110)
    canvas = Image.new("RGB", (preset_dims["pw"], preset_dims["ll"]), "white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((20, 10), label_title[:44], fill="black", font=font)
    scaled_bc = bc_img.resize((max(180, preset_dims["pw"] - 60), max(70, preset_dims["bc_h"] + 25)))
    canvas.paste(scaled_bc, (30, 32))
    if label_sub:
        draw.text((20, preset_dims["ll"] - 22), label_sub[:44], fill="black", font=font)

    png_buf = io.BytesIO()
    canvas.save(png_buf, format="PNG", dpi=(203, 203))
    png_bytes = png_buf.getvalue()
    return canvas, png_bytes, base64.b64encode(png_bytes).decode("ascii")


def send_zpl_to_network_zebra(zpl_data: str, printer_ip: str, printer_port: int = 9100) -> tuple[bool, str]:
    if not printer_ip.strip():
        return False, "Enter the Zebra ZD230 IP address."
    try:
        with socket.create_connection((printer_ip.strip(), int(printer_port)), timeout=5) as sock:
            sock.sendall(zpl_data.encode("utf-8"))
        return True, f"Sent ZPL to network Zebra printer at {printer_ip}:{printer_port}."
    except Exception as exc:
        return False, f"Network socket error ({printer_ip}:{printer_port}): {exc}"


def render_client_browser_printer_widget(
    zpl_data: str,
    label_png_b64: str,
    barcode_val: str,
    width_mm: int,
    height_mm: int,
    copies: int = 1,
    auto_trigger: bool = False,
):
    zpl_json = json.dumps(zpl_data)
    auto_js = "setTimeout(() => sendToClientZebra(), 350);" if auto_trigger else ""

    html_code = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                border: 1px solid #d0d7de; border-radius: 10px; padding: 14px; background: #f8fafc;">
      <div style="font-weight: 600; font-size: 14px; margin-bottom: 8px; color: #0f172a;">
        🖥️ Client Workstation Printer (Detected from Your Browser PC)
      </div>
      <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 10px; flex-wrap: wrap;">
        <select id="clientPrinterSelect"
                style="flex: 1; min-width: 220px; padding: 8px 10px; border-radius: 6px;
                       border: 1px solid #94a3b8; font-size: 13px; background: #fff;">
          <option value="">Scanning your PC for Zebra USB printers...</option>
        </select>
        <button type="button" onclick="discoverClientPrinters()"
                style="padding: 8px 12px; border-radius: 6px; border: 1px solid #94a3b8;
                       background: #fff; cursor: pointer; font-size: 13px;">
          🔄 Refresh Local Printers
        </button>
      </div>
      <div style="display: flex; gap: 10px; flex-wrap: wrap;">
        <button type="button" onclick="sendToClientZebra()"
                style="flex: 1; min-width: 210px; padding: 10px 14px; border-radius: 6px; border: none;
                       background: #ff4b4b; color: #fff; font-weight: 600; cursor: pointer; font-size: 13px;">
          🖨️ Direct Raw ZPL to My PC's Zebra ({copies}x)
        </button>
        <button type="button" onclick="openBrowserOsPrintDialog()"
                style="flex: 1; min-width: 210px; padding: 10px 14px; border-radius: 6px;
                       border: 1px solid #0284c7; background: #e0f2fe; color: #0369a1;
                       font-weight: 600; cursor: pointer; font-size: 13px;">
          🪟 Browser OS Printer Dialog (Any Local USB Printer)
        </button>
      </div>
      <div id="clientPrintStatus" style="margin-top: 9px; font-size: 12px; color: #334155;"></div>
    </div>
    <script>
      const ZPL_PAYLOAD = {zpl_json};
      const PNG_B64 = "{label_png_b64}";
      const COPIES = {int(copies)};
      const W_MM = {int(width_mm)};
      const H_MM = {int(height_mm)};
      let detectedDevices = [];
      let activeEndpoint = "http://127.0.0.1:9100";

      async function discoverClientPrinters() {{
        const sel = document.getElementById("clientPrinterSelect");
        const statusEl = document.getElementById("clientPrintStatus");
        sel.innerHTML = '<option value="">Scanning your local PC...</option>';
        detectedDevices = [];
        const endpoints = ["http://127.0.0.1:9100", "https://localhost:9101"];
        for (const ep of endpoints) {{
          try {{
            const resp = await fetch(ep + "/available", {{ method: "GET" }});
            if (resp.ok) {{
              const data = await resp.json();
              activeEndpoint = ep;
              const printers = data.printer || data || [];
              if (Array.isArray(printers) && printers.length > 0) {{
                detectedDevices = printers;
                break;
              }}
            }}
          }} catch (e) {{}}
        }}
        sel.innerHTML = "";
        if (detectedDevices.length > 0) {{
          detectedDevices.forEach((dev, idx) => {{
            const opt = document.createElement("option");
            opt.value = String(idx);
            opt.textContent = (dev.name || dev.uid || ("Zebra Printer #" + (idx + 1))) + (dev.connection ? (" [" + dev.connection + "]") : "");
            sel.appendChild(opt);
          }});
          statusEl.innerHTML = "✅ Found <b>" + detectedDevices.length + "</b> local Zebra printer(s).";
          statusEl.style.color = "#15803d";
        }} else {{
          const opt = document.createElement("option");
          opt.value = "";
          opt.textContent = "No Zebra Browser Print agent detected — use Browser OS Printer Dialog";
          sel.appendChild(opt);
          statusEl.innerHTML = "ℹ️ Install <b>Zebra Browser Print</b> for 1-click raw ZPL, or click <b>Browser OS Printer Dialog</b>.";
          statusEl.style.color = "#475569";
        }}
      }}

      async function sendToClientZebra() {{
        const sel = document.getElementById("clientPrinterSelect");
        const statusEl = document.getElementById("clientPrintStatus");
        if (!detectedDevices.length || sel.value === "") {{
          openBrowserOsPrintDialog();
          return;
        }}
        const device = detectedDevices[parseInt(sel.value, 10)];
        try {{
          const res = await fetch(activeEndpoint + "/write", {{
            method: "POST",
            headers: {{ "Content-Type": "text/plain;charset=UTF-8" }},
            body: JSON.stringify({{ device: device, data: ZPL_PAYLOAD }})
          }});
          if (res.ok) {{
            statusEl.innerHTML = "✅ Sent raw ZPL (" + COPIES + "x) to <b>" + (device.name || device.uid) + "</b>!";
            statusEl.style.color = "#15803d";
          }} else {{
            throw new Error("HTTP " + res.status);
          }}
        }} catch (err) {{
          statusEl.innerHTML = "❌ Local Zebra write error: " + err.message;
          statusEl.style.color = "#b91c1c";
        }}
      }}

      function openBrowserOsPrintDialog() {{
        const w = window.open("", "_blank", "width=520,height=420");
        if (!w) {{ alert("Please allow pop-ups for this site."); return; }}
        let imgsHtml = "";
        for (let i = 0; i < COPIES; i++) {{
          imgsHtml += `<div class="lbl"><img src="data:image/png;base64,${{PNG_B64}}" /></div>`;
        }}
        w.document.write(`<!DOCTYPE html><html><head><title>Print - {barcode_val}</title>
          <style>@page {{ size: ${{W_MM}}mm ${{H_MM}}mm; margin: 0; }} html, body {{ margin: 0; padding: 0; }}
          .lbl {{ width: ${{W_MM}}mm; height: ${{H_MM}}mm; page-break-after: always; display: flex; align-items: center; justify-content: center; }}
          .lbl:last-child {{ page-break-after: auto; }} img {{ width: 100%; height: 100%; object-fit: contain; }}</style></head>
          <body>${{imgsHtml}}<script>window.onload=function(){{window.focus();window.print();setTimeout(function(){{window.close();}},500);}};<\/script></body></html>`);
        w.document.close();
      }}
      discoverClientPrinters();
      {auto_js}
    </script>
    """
    components.html(html_code, height=165)
