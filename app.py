import base64
import io
import re
from fpdf import FPDF
from PIL import Image, ImageDraw
import pymupdf as fitz
import streamlit as st
import streamlit.components.v1 as components
import datetime
import os
import json
import hashlib
try:
    import winreg
except ImportError:
    winreg = None
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
import time
import threading
import requests
from supabase import create_client

# =========================================================
# TETAPAN VERSI SISTEM (Tukar di sini sahaja!)
# =========================================================
APP_VERSION = "1.3.1"  # Set versi Fasa 2 anda di sini

def semak_saiz_kertas(page, toleransi_pt=3.0):
    """Semak saiz kertas mesti A4 (210 x 297mm), potret atau landskap."""
    PT_TO_MM = 25.4 / 72.0
    pendek, panjang = sorted((page.rect.width, page.rect.height))

    # Saiz A4 dalam pt (potret: 595.28 x 841.89)
    if abs(pendek - 595.28) <= toleransi_pt and abs(panjang - 841.89) <= toleransi_pt:
        return []

    # Cuba kenal pasti nama saiz biasa
    saiz_dikenali = {
        "Letter": (612.0, 792.0),
        "Legal": (612.0, 1008.0),
        "A3": (841.89, 1190.55),
        "A5": (419.53, 595.28),
    }
    nama = "saiz tidak diketahui"
    for n, (p, q) in saiz_dikenali.items():
        if abs(pendek - p) <= toleransi_pt and abs(panjang - q) <= toleransi_pt:
            nama = n
            break

    return [{
        "msg": (
            f"Saiz Kertas Salah: Dikesan {nama} ({pendek * PT_TO_MM:.0f} x"
            f" {panjang * PT_TO_MM:.0f}mm). Sepatutnya A4 (210 x 297mm)."
            " Sila ubah di Word: Layout > Size > A4."
        ),
        "bbox": None,
    }]

def semak_tajuk_bab_capital(page):
    """Pengesanan khusus perkataan 'Bab 4' atau 'bab 4' yang sepatutnya ALL CAPS ('BAB 4')."""
    ralat = []
    text_dict = page.get_text("dict")
    for blok in text_dict.get("blocks", []):
        if blok.get("type", 0) != 0:
            continue
        for baris in blok.get("lines", []):
            teks = "".join(s.get("text", "") for s in baris.get("spans", [])).strip()
            # Hanya baris yang BERMULA dengan "bab <no>" dan pendek (ciri tajuk)
            m = re.match(r"^(bab)\s*(\d+|[IVX]+)\b", teks, re.IGNORECASE)
            if not m or len(teks) > 60:
                continue
            if m.group(1) == "BAB":
                continue  # sudah betul
            salah_teks = m.group(0)
            nombor = m.group(2)
            bbox = baris["bbox"]

            ralat.append({
                "msg": (
                    f"Format Tajuk Bab Salah: Dikesan '{salah_teks}'. Sepatutnya"
                    f" ditulis dalam HURUF BESAR SEPENUHNYA ('BAB {nombor}')."
                ),
                "bbox": bbox,
            })

    return ralat


def semak_saiz_kertas(
    page,
    target_width_mm=210.0,
    target_height_mm=297.0,
    tolerance_mm=3.0,
):
    """Semak sama ada muka surat menggunakan saiz A4 (210mm x 297mm)."""
    PT_TO_MM = 25.4 / 72.0

    page_width_mm = page.rect.width * PT_TO_MM
    page_height_mm = page.rect.height * PT_TO_MM

    width_diff = abs(page_width_mm - target_width_mm)
    height_diff = abs(page_height_mm - target_height_mm)

    # Jika lebar atau tinggi lari melebihi toleransi (3mm)
    if width_diff > tolerance_mm or height_diff > tolerance_mm:
        # Tentukan nama saiz kertas jika ia adalah US Letter
        if (
            abs(page_width_mm - 215.9) < 5.0
            and abs(page_height_mm - 279.4) < 5.0
        ):
            saiz_dikesan = "US Letter (216mm x 279mm)"
        else:
            saiz_dikesan = (
                f"Kustom ({page_width_mm:.0f}mm x {page_height_mm:.0f}mm)"
            )

        return [{
            "msg": (
                f"Saiz Kertas Salah: Dikesan {saiz_dikesan}. Sepatutnya A4"
                " (210mm x 297mm). Sila tukar Saiz Kertas di Layout Word."
            ),
            "bbox": (0, 0, page.rect.width, page.rect.height),
        }]

    return []


def kampoi_bbox(senarai_bbox, ambang_kampoi=3):
    """Menggabungkan (kampoi) senarai bbox jika jumlah isu >= ambang_kampoi.

    Jika isu < ambang_kampoi (contoh: 1 atau 2 isu sahaja), kekalkan kotak
    berasingan.
    """
    # Buang bbox kosong/tiada nilai
    senarai_valid = [b for b in senarai_bbox if b and len(b) == 4]

    if not senarai_valid:
        return []

    # 🛑 SYARAT UTAMA: Jika isu 1 atau 2 sahaja, JANGAN KAMPOI (kekalkan kotak berasingan)
    if len(senarai_valid) < ambang_kampoi:
        return senarai_valid

    # 🟢 SYARAT KAMPOI: Jika 3 isu atau lebih, gabung jadi 1 kotak besar
    x0 = min(b[0] for b in senarai_valid)
    y0 = min(b[1] for b in senarai_valid)
    x1 = max(b[2] for b in senarai_valid)
    y1 = max(b[3] for b in senarai_valid)

    return [(x0, y0, x1, y1)]


def kesan_jenis_objek_atas_caption(page, caption_bbox):
    """Mengesan sama ada objek di ATAS tajuk ialah Jadual, Imej, atau Grafik Vektor (Carta/Graf)."""
    c_x0, c_y0, c_x1, c_y1 = caption_bbox

    # Bina zon carian (200pt di atas tajuk)
    zon_atas = fitz.Rect(
        max(0, c_x0 - 50),
        max(0, c_y0 - 220),
        min(page.rect.width, c_x1 + 50),
        c_y0,
    )

    # 1. Semak kewujudan Jadual (Table)
    try:
        tables = page.find_tables()
        for tab in tables:
            tab_rect = fitz.Rect(tab.bbox)
            if tab_rect.intersects(zon_atas):
                return "JADUAL"
    except Exception:
        pass

    # 2. Semak kewujudan Imej (Bitmap Photo/PNG)
    for img in page.get_image_info(hashes=False):
        img_rect = fitz.Rect(img["bbox"])
        if img_rect.intersects(zon_atas):
            return "IMEJ"

    # 3. Semak kewujudan Grafik Vektor (Carta / Graf / Shape Drawing)
    for dwg in page.get_drawings():
        dwg_rect = fitz.Rect(dwg["rect"])
        # Tapis garisan kecil biasa, cari elemen grafik yang agak besar (width > 60 & height > 30)
        if (
            dwg_rect.intersects(zon_atas)
            and dwg_rect.width > 60
            and dwg_rect.height > 30
        ):
            return "CARTA_VEKTOR"

    return "TIADA_OBJEK"


def semak_keseluruhan_dokumen(doc, aktifkan_spacing=True):
    semua_ralat = []
    masuk_bab1 = False
    sudah_tamat_bab5 = False

    for page_idx, page in enumerate(doc):
        page_num = page_idx + 1

        page_text = page.get_text("text")
        page_text_upper = page_text.upper()

        lines_upper = [
            line.strip()
            for line in page_text_upper.split("\n")
            if line.strip()
        ]
        top_5_text = " ".join(lines_upper[:5]) if lines_upper else ""

        # -----------------------------------------------------------------
        # 0. SEMAK SAIZ KERTAS (Kekal untuk semua muka surat)
        # -----------------------------------------------------------------
        for err in semak_saiz_kertas(page):
            err["page"] = page_num
            err["page_num"] = page_num
            err["muka_surat"] = page_num
            semua_ralat.append(err)

        # -----------------------------------------------------------------
        # 1. SEMAK FORMAT HURUF TAJUK BAB ("Bab 4" vs "BAB 4")
        # -----------------------------------------------------------------
        for err in semak_tajuk_bab_capital(page):
            err["page"] = page_num
            err["page_num"] = page_num
            err["muka_surat"] = page_num
            semua_ralat.append(err)

        # -----------------------------------------------------------------
        # 2. SEMAK MARGIN & JUSTIFY (Paksa tolerance_mm=0.5)
        # -----------------------------------------------------------------
        for err in semak_justify_perenggan(page, tolerance_mm=0.5):
            err["page"] = page_num
            err["page_num"] = page_num
            err["muka_surat"] = page_num
            semua_ralat.append(err)

        # -----------------------------------------------------------------
        # 3. SEMAK SPACING HANYA DI ZON BAB 1 HINGGA BAB 5
        # -----------------------------------------------------------------
        if aktifkan_spacing:

            # A. Semak jika muka surat ini ialah Halaman Awalan Utama
            is_halaman_awalan_tajuk = any(
                top_5_text.startswith(kw) or f"\n{kw}" in top_5_text
                for kw in [
                    "ISI KANDUNGAN",
                    "SENARAI JADUAL",
                    "SENARAI RAJAH",
                    "PENGAKUAN PENULIS",
                    "PERAKUAN PENYELIA",
                    "PENGHARGAAN",
                    "ABSTRAK",
                    "ABSTRACT",
                    "BORANG PENGESAHAN",
                ]
            )

            # B. Pengesanan Permulaan BAB 1
            if not masuk_bab1:
                is_bab1_header = any(
                    kw in top_5_text
                    for kw in [
                        "BAB 1",
                        "BAB I",
                        "1.0 PENDAHULUAN",
                        "BAB 1: PENDAHULUAN",
                        "BAB 1\nPENDAHULUAN",
                    ]
                )

                if is_bab1_header and not is_halaman_awalan_tajuk:
                    masuk_bab1 = True
                else:
                    continue

            # C. Semak jika sudah sampai Penamat (RUJUKAN / LAMPIRAN)
            if masuk_bab1 and not sudah_tamat_bab5:
                is_tajuk_penamat = any(
                    top_5_text.startswith(kw)
                    for kw in [
                        "RUJUKAN",
                        "SENARAI RUJUKAN",
                        "BIBLIOGRAFI",
                        "LAMPIRAN",
                    ]
                )
                if is_tajuk_penamat:
                    sudah_tamat_bab5 = True

            # D. Jalankan semakan spacing jika berada dalam Zon Bab 1 hingga Bab 5
            if masuk_bab1 and not sudah_tamat_bab5:
                for err in semak_jarak_baris_perenggan(page):
                    err["page"] = page_num
                    err["page_num"] = page_num
                    err["muka_surat"] = page_num
                    semua_ralat.append(err)

    # 🛡️ PERISAI KALIS RALAT: Pastikan kunci 'msg' sentiasa wujud
    for err in semua_ralat:
        if "msg" not in err:
            err["msg"] = err.get("mesej", err.get("jenis", "Ralat Dokumen"))

    return semua_ralat


def semak_jarak_baris_perenggan(
    page,
    min_ratio=1.35,
    max_ratio=1.75,
    margin_top_pt=45.0,
    margin_bottom_pt=45.0,
):
    ralat_spacing = []

    page_text = page.get_text("text")
    page_text_upper = page_text.upper()

    # 🟢 1. MUKA SURAT KHAS UTAMA (Abaikan Semakan Spacing)
    EXCLUDED_PAGES = [
        "PENGAKUAN PENULIS",
        "PERAKUAN PENYELIA PROJEK",
        "PERAKUAN PEGAWAI PENGESAHAN LUARAN",
        "SENARAI JADUAL",
        "SENARAI RAJAH",
        "SENARAI LAMPIRAN",
        "SENARAI GAMBAR",
        "SENARAI CARTA ALIR",
    ]
    if any(kw in page_text_upper for kw in EXCLUDED_PAGES):
        return []

    # 🟢 2. PENGESANAN UNIVERSAL ISI KANDUNGAN (TOC SCORE)
    lines_raw = [line.strip() for line in page_text.split("\n") if line.strip()]
    toc_score = 0

    for line in lines_raw:
        line_upper = line.upper()

        # A. Kesan baris sub-bab yang ada nombor/julat muka surat di hujung
        if re.search(r"^\d+(\.\d+)+\s+.*\d+$", line):
            toc_score += 1

        # B. Kesan baris tajuk BAB / RUJUKAN / LAMPIRAN
        elif (
            re.search(r"^BAB\s+\d+", line_upper)
            or line_upper.startswith("RUJUKAN")
            or line_upper.startswith("LAMPIRAN")
        ):
            toc_score += 1

        # C. Kesan pengepala Isi Kandungan
        elif (
            "PERKARA" in line_upper
            or "MUKA SURAT" in line_upper
            or "ISI KANDUNGAN" in line_upper
        ):
            toc_score += 2

    # Jika TOC Score >= 3, sah ia Muka Surat Isi Kandungan -> Abaikan semakan spacing!
    if toc_score >= 3:
        return []

    # 🟢 3. PENGESANAN JADUAL (Abaikan Teks Dalam Jadual)
    table_bboxes = []
    try:
        tables = page.find_tables()
        for tbl in tables:
            table_bboxes.append(tbl.bbox)
    except Exception:
        pass

    def is_in_table(bbox):
        x0, y0, x1, y1 = bbox
        for tb in table_bboxes:
            if (
                x0 >= (tb[0] - 10)
                and x1 <= (tb[2] + 10)
                and y0 >= (tb[1] - 10)
                and y1 <= (tb[3] + 10)
            ):
                return True
        return False

    # 🟢 4. SEMAKAN TEKS PERENGGAN BIASA
    text_page = page.get_text("dict")
    page_height = page.rect.height

    for block in text_page.get("blocks", []):
        if block.get("type", 0) != 0:
            continue

        lines = block.get("lines", [])
        if len(lines) < 2:
            continue

        current_error_group = None

        for i in range(len(lines) - 1):
            line1 = lines[i]
            line2 = lines[i + 1]

            bbox1 = line1["bbox"]
            bbox2 = line2["bbox"]

            # Abaikan jika teks dalam jadual
            if is_in_table(bbox1) or is_in_table(bbox2):
                continue

            y0_1, y1_1 = bbox1[1], bbox1[3]
            y0_2, y1_2 = bbox2[1], bbox2[3]

            # Abaikan header/footer ekstrim
            if y0_1 < margin_top_pt or y1_2 > (page_height - margin_bottom_pt):
                continue

            spans1 = line1.get("spans", [])
            spans2 = line2.get("spans", [])

            if not spans1 or not spans2:
                continue

            teks_line1 = "".join([s.get("text", "") for s in spans1]).strip()
            teks_line2 = "".join([s.get("text", "") for s in spans2]).strip()

            if not teks_line1 or not teks_line2:
                continue

            # Abaikan kata kunci jadual biasa
            TABLE_KEYWORDS = [
                "BIL",
                "BAHAN",
                "HARGA",
                "KUANTITI",
                "JUMLAH",
                "SEUNIT",
                "RM",
            ]
            if any(
                kw in teks_line1.upper() or kw in teks_line2.upper()
                for kw in TABLE_KEYWORDS
            ) and (len(teks_line1) < 35 or len(teks_line2) < 35):
                continue

            # Abaikan jika Tajuk Utama Bab
            if (
                any(
                    teks_line1.upper().startswith(kw)
                    for kw in ["BAB ", "1.", "2.", "3.", "4.", "5.", "6."]
                )
                and len(teks_line1) < 40
            ):
                continue

            font_size1 = max(s.get("size", 11.0) for s in spans1)
            font_size2 = max(s.get("size", 11.0) for s in spans2)
            avg_font_size = (font_size1 + font_size2) / 2.0

            if not (9.0 <= avg_font_size <= 13.0):
                continue

            delta_y = y0_2 - y0_1
            ratio = delta_y / avg_font_size

            if ratio < 0.6 or ratio > 2.2:
                if current_error_group:
                    ralat_spacing.append(current_error_group)
                    current_error_group = None
                continue

            teks_sampel = teks_line1[:30]

            is_error = False
            jenis_ralat = ""
            msg_ralat = ""

            if ratio < min_ratio:
                is_error = True
                jenis_ralat = "Line Spacing Rapat (Single / 1.15 Spacing)"
                msg_ralat = (
                    f"Jarak baris terlalu rapat (Nisbah: {ratio:.2f}x, Sepatutnya"
                    f" 1.5x) pada teks: '{teks_sampel}...'"
                )
            elif ratio > max_ratio:
                is_error = True
                jenis_ralat = "Line Spacing Renggang (Double Spacing)"
                msg_ralat = (
                    f"Jarak baris terlalu renggang (Nisbah: {ratio:.2f}x, Sepatutnya"
                    f" 1.5x) pada teks: '{teks_sampel}...'"
                )

            if is_error:
                if current_error_group is None:
                    current_error_group = {
                        "jenis": jenis_ralat,
                        "msg": msg_ralat,
                        "bbox": (
                            min(bbox1[0], bbox2[0]),
                            bbox1[1],
                            max(bbox1[2], bbox2[2]),
                            bbox2[3],
                        ),
                    }
                else:
                    b = current_error_group["bbox"]
                    current_error_group["bbox"] = (
                        min(b[0], bbox1[0], bbox2[0]),
                        min(b[1], bbox1[1]),
                        max(b[2], bbox1[2], bbox2[2]),
                        max(b[3], bbox2[3]),
                    )
            else:
                if current_error_group:
                    ralat_spacing.append(current_error_group)
                    current_error_group = None

        if current_error_group:
            ralat_spacing.append(current_error_group)

    return ralat_spacing

# False=Tajuk di sebelah kiri TIDAK AKAN dianggap ralat
# True=Tajuk di sebelah kiri AKAN dikesan sebagai ralat
def semak_format_rujukan_apa(doc, semak_tajuk_center=False):
    """Fungsi semakan format APA.

    Parameter:
    - doc: Dokumen PyMuPDF (fitz)
    - semak_tajuk_center: Putar ke True jika mahu semak Tajuk di Tengah (Center).
                          Putar ke False jika mahu terima Tajuk di Sebelah Kiri.
    """
    errors = []

    SKIP_PAGE_KEYWORDS = [
        "ISI KANDUNGAN",
        "SENARAI KANDUNGAN",
        "BAB 1",
        "BAB 2",
        "BAB 3",
        "BAB 4",
        "BAB 5",
        "KESIMPULAN",
        "CADANGAN",
    ]

    MM_TO_PT = 72.0 / 25.4
    TARGET_MARGIN_LEFT_PT = 40.0 * MM_TO_PT  # Margin Kiri 40mm

    for page_idx, page in enumerate(doc):
        page_num = page_idx + 1
        page_width = page.rect.width
        text_upper = page.get_text().upper()

        if any(kw in text_upper for kw in SKIP_PAGE_KEYWORDS):
            continue

        if "RUJUKAN" in text_upper or "BIBLIOGRAFI" in text_upper:
            
            text_page = page.get_text("dict")

            raw_lines = []
            is_after_heading = False

            for block in text_page.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue

                for line in block.get("lines", []):
                    x0, y0, x1, y1 = line["bbox"]
                    line_text = "".join(
                        [s.get("text", "") for s in line.get("spans", [])]
                    ).strip()

                    if not line_text:
                        continue

                    # Semak Tajuk Utama "RUJUKAN"
                    if line_text.upper() in [
                        "RUJUKAN",
                        "SENARAI RUJUKAN",
                        "BIBLIOGRAFI",
                    ]:
                        is_after_heading = True

                        # 📌 SUIS TOGGLE: HANYA SEMAK JIKA 'semak_tajuk_center' ADALAH TRUE
                        if semak_tajuk_center:
                            line_center = (x0 + x1) / 2.0
                            if abs(line_center - (page_width / 2.0)) > 60.0:
                                errors.append({
                                    "page": page_num,
                                    "page_num": page_num,
                                    "muka_surat": page_num,
                                    "msg": (
                                        f"Format Tajuk Salah: Tajuk '{line_text}' diletakkan"
                                        " di sebelah kiri. Mengikut GP PTA 2026, tajuk RUJUKAN"
                                        " MESTI di Tengah (Center) & BOLD."
                                    ),
                                    "bbox": (x0, y0, x1, y1),
                                })
                        continue

                    if is_after_heading:
                        raw_lines.append(
                            {"text": line_text, "bbox": (x0, y0, x1, y1), "x0": x0}
                        )

            # -----------------------------------------------------
            # ALGORITMA PENGELOMPOKAN PINTAR ENTRI RUJUKAN
            # -----------------------------------------------------
            entries = []
            current_entry = None

            def is_url_link(txt):
                return (
                    txt.startswith("http://")
                    or txt.startswith("https://")
                    or txt.startswith("www.")
                )

            def is_url_continuation(txt):
                return (
                    ("/" in txt or "?" in txt or "=" in txt or "." in txt)
                    and (" " not in txt)
                ) or txt[0].islower() or ("/" in txt and len(txt.split()) <= 2)

            # 1) Gabung nombor senarai yang terpisah baris ("1." + teks) jadi satu baris
            _merged = []
            _pending = None
            for _l in raw_lines:
                if re.fullmatch(r"(\d{1,3}[\.\)]|\[\d{1,3}\])", _l["text"].strip()):
                    _pending = _l
                    continue
                if _pending is not None:
                    _b1, _b2 = _pending["bbox"], _l["bbox"]
                    _l = {
                        "text": _pending["text"].strip() + " " + _l["text"],
                        "bbox": (
                            min(_b1[0], _b2[0]), min(_b1[1], _b2[1]),
                            max(_b1[2], _b2[2]), max(_b1[3], _b2[3]),
                        ),
                        "x0": _pending["x0"],
                    }
                    _pending = None
                _merged.append(_l)
            if _pending is not None:
                _merged.append(_pending)
            raw_lines = _merged

            # 2) Adakah dokumen guna hanging indent? (baris sambungan menjorok ke dalam)
            _min_x0 = min((l["x0"] for l in raw_lines), default=0)
            has_hanging_indent = any(l["x0"] > _min_x0 + 15 for l in raw_lines)

            _TAJUK_BAHAGIAN = {
                "internet", "buku", "jurnal", "laman web",
                "laman sesawang", "web", "artikel",
            }

            def looks_like_entry_start(txt):
                t = txt.strip()
                if t.lower() in _TAJUK_BAHAGIAN:
                    return True
                # Format "Nama, I." di permulaan baris
                if re.match(r"^[^\W\d_][\w'’\-\. ]{1,40},\s*[A-Z]\.", t):
                    return True
                # "Teks pengarang (2021)" / "(t.t.)" dalam 80 aksara pertama
                if re.match(
                    r"^[^\(]{1,80}\(((19|20)\d{2}[a-z]?|n\.d\.|t\.t\.)\)",
                    t, re.IGNORECASE,
                ):
                    return True
                return False

            for line in raw_lines:
                text = line["text"]
                bbox = line["bbox"]
                x0 = line["x0"]

                curr_is_url = is_url_link(text)
                is_new_entry = False

                if current_entry is None:
                    is_new_entry = True
                else:
                    prev_last_text = current_entry["lines"][-1]
                    prev_is_url = is_url_link(prev_last_text) or is_url_continuation(
                        prev_last_text
                    )

                    # Nombor senarai sebenar sahaja ("1. " / "1) " / "[1] ")
                    if re.match(r"^(\d{1,3}[\.\)]|\[\d{1,3}\])\s", text):
                        is_new_entry = True
                    elif curr_is_url and prev_is_url:
                        is_new_entry = True
                    elif not curr_is_url and prev_is_url:
                        if not is_url_continuation(text):
                            is_new_entry = True
                    elif not curr_is_url and not prev_is_url:
                        if has_hanging_indent:
                            if x0 <= (current_entry["x0"] + 10):
                                is_new_entry = True
                        elif looks_like_entry_start(text):
                            is_new_entry = True

                if is_new_entry:
                    if current_entry:
                        entries.append(current_entry)
                    current_entry = {"lines": [text], "bbox": bbox, "x0": x0}
                else:
                    if current_entry:
                        current_entry["lines"].append(text)
                        b = current_entry["bbox"]
                        current_entry["bbox"] = (
                            min(b[0], bbox[0]),
                            min(b[1], bbox[1]),
                            max(b[2], bbox[2]),
                            max(b[3], bbox[3]),
                        )

            if current_entry:
                entries.append(current_entry)

            # -----------------------------------------------------
            # ANALISIS ISU UNTUK SETIAP ENTRI
            # -----------------------------------------------------
            for entry in entries:
                entry_lines = entry["lines"]
                entry_text = " ".join(entry_lines)
                entry_bbox = entry["bbox"]
                entry_x0 = entry["x0"]

                entry_issues = []

                # 1. URL Mentah Terpencil
                if len(entry_lines) <= 2 and all(
                    is_url_link(l) or is_url_continuation(l) for l in entry_lines
                ):
                    url_sample = (
                        entry_text[:35] + "..." if len(entry_text) > 35 else entry_text
                    )
                    errors.append({
                        "page": page_num,
                        "page_num": page_num,
                        "muka_surat": page_num,
                        "msg": (
                            f"Format APA Salah: Entri hanya mengandungi URL mentah"
                            f" ('{url_sample}'). URL wajib mempunyai Nama"
                            " Pengarang/Saluran, Tahun, dan Tajuk."
                        ),
                        "bbox": entry_bbox,
                    })
                    continue

                # 2. Teks Terlalu Pendek / Nota Kasar
                words = entry_text.split()
                if len(words) < 4 and not re.search(r"\d{4}", entry_text):
                    errors.append({
                        "page": page_num,
                        "page_num": page_num,
                        "muka_surat": page_num,
                        "msg": (
                            f"Format Rujukan Tidak Lengkap: '{entry_text}' ditulis seperti"
                            " nota kasar. Tiada elemen asas APA (Pengarang, Tahun,"
                            " Penerbit/Institusi)."
                        ),
                        "bbox": entry_bbox,
                    })
                    continue

                # 3. Penggunaan Nombor
                num_match = re.match(r"^(\d+)[\.\)]", entry_text)
                if num_match:
                    entry_issues.append(
                        f"menggunakan nombor ('{num_match.group(1)}.')"
                    )

                # 4. Margin Kiri
                if entry_x0 < (TARGET_MARGIN_LEFT_PT - 10.0):
                    diff_mm = round((TARGET_MARGIN_LEFT_PT - entry_x0) / MM_TO_PT, 1)
                    entry_issues.append(f"terkeluar {diff_mm}mm dari margin kiri 40mm")

                # 5. Format Tahun
                has_year = re.search(
                    r"\((19|20)\d{2}[a-z]?\)", entry_text
                ) or re.search(r"\((n\.d\.|t\.t\.)\)", entry_text, re.IGNORECASE)
                if not has_year:
                    entry_issues.append("tiada format tahun terbitan '(Tahun)' / '(t.t.)'")

                if entry_issues:
                    tajuk_ringkas = (
                        entry_text[:35] + "..."
                        if len(entry_text) > 35
                        else entry_text
                    )
                    issues_str = ", ".join(entry_issues)
                    errors.append({
                        "page": page_num,
                        "page_num": page_num,
                        "muka_surat": page_num,
                        "msg": (
                            f"Isu Format APA pada '{tajuk_ringkas}': Dikesan {issues_str}."
                            " Format APA wajib disusun mengikut Abjad (A-Z) tanpa nombor."
                        ),
                        "bbox": entry_bbox,
                    })

    return errors


def int_to_roman(number):
    """Menukar nombor bulat kepada nombor Roman kecil."""
    if number <= 0:
        return ""
    num_map = [(10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
    roman = ""
    while number > 0:
        for val, sign in num_map:
            if number >= val:
                roman += sign
                number -= val
                break
    return roman


def is_toc_page(page, skip_keywords=None):
    """Mengesan Muka Surat Isi Kandungan / Senarai (termasuk M/S sambungan).

    Menyokong format ber-titik '...' ATAU ruang kosong (Tab), serta nombor
    Arab/Roman.
    """
    if skip_keywords is None:
        skip_keywords = [
            "ISI KANDUNGAN",
            "SENARAI KANDUNGAN",
            "TABLE OF CONTENTS",
            "KANDUNGAN",
        ]

    full_text = page.get_text()
    page_text_upper = full_text.upper()

    # 📌 1. SEMAK KATA KUNCI TAJUK
    if any(
        kw.strip().upper() in page_text_upper
        for kw in skip_keywords
        if kw.strip()
    ):
        return True

    # 🟢 PEMBAIKAN 1: Kesan jika muka surat mengandungi tajuk kolum Isi Kandungan
    if "PERKARA" in page_text_upper and "MUKA SURAT" in page_text_upper:
        return True

    # Helper untuk menyemak nombor muka surat (Arab ATAU Roman)
    def is_valid_page_str(s):
        s_clean = s.strip().lower()
        if not s_clean:
            return False
        # Nombor Arab (1, 2, 10, dll)
        if s_clean.isdigit() and len(s_clean) <= 3:
            return True
        # Nombor Roman (i, ii, iv, v, vi, vii, viii, ix, x, xii, xiv, dll)
        roman_chars = set("ivxlcdm")
        if set(s_clean).issubset(roman_chars) and len(s_clean) <= 6:
            return True
        return False

    # 📌 2. SEMAK STRUKTUR BARIS (Untuk Muka Surat Sambungan)
    text_page = page.get_text("dict")
    toc_pattern_count = 0
    total_valid_lines = 0

    for block in text_page.get("blocks", []):
        if block.get("type", 0) != 0:
            continue

        for line in block.get("lines", []):
            spans = line.get("spans", [])
            line_text = "".join(s.get("text", "") for s in spans).strip()

            if not line_text:
                continue

            total_valid_lines += 1

            # A. Pelajar guna Dot Leaders ('...' atau '…')
            has_dots = ".." in line_text or "…" in line_text

            # B. Baris diakhiri nombor muka surat (Arab / Roman)
            # 🟢 PEMBAIKAN 2: Ambil perkataan TERAKHIR dari line_text secara terus
            words = line_text.split()
            last_word = words[-1] if words else ""
            ends_with_page_num = is_valid_page_str(last_word)

            # C. Ada jurang/tab kosong ke kanan (> 30pt / ~1cm)
            has_large_gap = False
            if len(spans) >= 2 and ends_with_page_num:
                prev_x1 = spans[-2].get("bbox")[2]
                num_x0 = spans[-1].get("bbox")[0]
                if (num_x0 - prev_x1) > 30.0:
                    has_large_gap = True

            # D. Awalan nombor subtajuk / Bab / Perkara
            has_section_num = (
                any(c.isdigit() for c in line_text[:5])
                or line_text.startswith("BAB ")
                or line_text.startswith("SENARAI ")
                or line_text.startswith("PERKARA")
            )

            if has_dots or (
                ends_with_page_num and (has_large_gap or has_section_num)
            ):
                toc_pattern_count += 1

    # Jika sekurang-kurangnya 50% baris memenuhi struktur Isi Kandungan
    if total_valid_lines >= 3 and (toc_pattern_count / total_valid_lines) >= 0.50:
        return True

    return False


def get_document_zones(doc):
    """Imbasan awal (Pre-pass) untuk menentukan zon: Tajuk Dalam & BAB 1."""
    FRONT_MATTER_KEYWORDS = [
        "PENGAKUAN PENULIS",
        "PERAKUAN PENYELIA",
        "PENGHARGAAN",
        "ABSTRAK",
        "ABSTRACT",
        "ISI KANDUNGAN",
        "SENARAI KANDUNGAN",
        "SENARAI JADUAL",
        "SENARAI RAJAH",
        "SENARAI ATUR CARA",
        "SENARAI KOD",
        "SENARAI LAMPIRAN",
        "SENARAI SINGKATAN",
        "SENARAI SIMBOL",
    ]

    tajuk_dalam_idx = None
    first_fm_idx = None
    bab1_page_idx = None

    for idx, page in enumerate(doc):
        text_upper = page.get_text().upper()

        # 1. Cari Halaman Tajuk Dalam
        if (
            "LAPORAN PROJEK YANG DIKEMUKAKAN" in text_upper
            and tajuk_dalam_idx is None
        ):
            tajuk_dalam_idx = idx

        # 2. Cari muka surat Front Matter pertama
        if (
            any(kw in text_upper for kw in FRONT_MATTER_KEYWORDS)
            and first_fm_idx is None
        ):
            first_fm_idx = idx

        # 3. Cari Muka Surat Sebenar BAB 1 (Abaikan jika muka surat tersebut adalah Isi Kandungan)
        if not is_toc_page(page):
            lines = [
                line.strip().upper()
                for line in page.get_text().splitlines()
                if line.strip()
            ]
            has_bab1_heading = any(
                line
                in [
                    "BAB 1",
                    "BAB I",
                    "BAB 1: PENGENALAN",
                    "BAB 1 PENGENALAN",
                    "BAB I PENGENALAN",
                ]
                or (line.startswith("BAB 1") and "PENGENALAN" in line)
                for line in lines
            )
            if has_bab1_heading and bab1_page_idx is None:
                bab1_page_idx = idx

    # Fallback jika penanda tidak dijumpai
    if first_fm_idx is not None and tajuk_dalam_idx is None:
        tajuk_dalam_idx = max(0, first_fm_idx - 1)
    elif tajuk_dalam_idx is None:
        tajuk_dalam_idx = 0

    if bab1_page_idx is None:
        bab1_page_idx = len(doc)

    return tajuk_dalam_idx, bab1_page_idx


def semak_penomboran_gppta(doc):
    errors = []

    # Guna fungsi pemetaan zon di luar
    tajuk_dalam_idx, bab1_page_idx = get_document_zones(doc)

        # Cari muka surat pertama LAMPIRAN (selepas Rujukan)
    def _top3(pg):
        return [l.strip().upper() for l in pg.get_text().splitlines() if l.strip()][:3]

    _mula = bab1_page_idx
    for _i in range(bab1_page_idx, len(doc)):
        if any(l.startswith(("RUJUKAN", "SENARAI RUJUKAN", "BIBLIOGRAFI")) for l in _top3(doc[_i])):
            _mula = _i
            break

    lampiran_idx = None
    for _i in range(_mula, len(doc)):
        if any(l.startswith(("LAMPIRAN", "APPENDIX")) and len(l) < 60 for l in _top3(doc[_i])):
            lampiran_idx = _i
            break

    # SEMAKAN PER-MUKA SURAT
    for page_idx, page in enumerate(doc):
        page_num_display = page_idx + 1
        page_width = page.rect.width
        page_height = page.rect.height

        footer_items = []
        text_page = page.get_text("dict")

        for block in text_page.get("blocks", []):
            for line in block.get("lines", []):
                x0, y0, x1, y1 = line["bbox"]
                line_text = "".join(
                    [s.get("text", "") for s in line.get("spans", [])]
                ).strip()

                # Semak zon footer (20% kawasan bawah muka surat)
                if y0 > (page_height * 0.80) and line_text:
                    numbers = re.findall(r"\b\d+\b", line_text)
                    romans = re.findall(r"\b[ivxlcdm]+\b", line_text.lower())

                    # 📌 HANYA SIMPAN JIKA ADA NOMBOR/ROMAN
                    # Ini menghalang teks perenggan biasa daripada terangkum sebagai footer!
                    if numbers or romans:
                        is_right = x1 > (page_width * 0.65)
                        is_center = (page_width * 0.25) <= x0 and x1 <= (
                            page_width * 0.75
                        )

                        footer_items.append({
                            "text": line_text,
                            "cleaned": line_text.strip(" -().[]"),
                            "numbers": numbers,
                            "romans": romans,
                            "bbox": (x0, y0, x1, y1),
                            "is_right": is_right,
                            "is_center": is_center,
                        })

        # A. KULIT LUAR / SAMPUL (Sebelum Tajuk Dalam)
        if page_idx < tajuk_dalam_idx:
            for item in footer_items:
                if item["numbers"] or item["romans"]:
                    errors.append({
                        "page": page_num_display,
                        "page_num": page_num_display,
                        "muka_surat": page_num_display,
                        "msg": (
                            "Muka surat sampul/hadapan tidak boleh memaparkan nombor"
                            f" muka surat (dikesan '{item['text']}')."
                        ),
                        "bbox": item["bbox"],
                    })

        # B. HALAMAN TAJUK DALAM (Muka surat 'i' Tersembunyi)
        elif page_idx == tajuk_dalam_idx:
            for item in footer_items:
                if "1" in item["numbers"] or "i" in item["romans"]:
                    errors.append({
                        "page": page_num_display,
                        "page_num": page_num_display,
                        "muka_surat": page_num_display,
                        "msg": (
                            "Halaman Tajuk Dalam tidak boleh memaparkan nombor muka"
                            " surat (nombor 'i' mestilah tersembunyi)."
                        ),
                        "bbox": item["bbox"],
                    })
                    break

        # C. BAHAGIAN AWALAN (Roman: ii, iii, iv, ...)
        elif tajuk_dalam_idx < page_idx < bab1_page_idx:
            expected_roman_val = (page_idx - tajuk_dalam_idx) + 1
            expected_roman = int_to_roman(expected_roman_val)

            found_exact_right = any(
                expected_roman in item["romans"] and item["is_right"]
                for item in footer_items
            )

            if not found_exact_right:
                center_item = next(
                    (
                        item
                        for item in footer_items
                        if expected_roman in item["romans"]
                        and item["is_center"]
                    ),
                    None,
                )
                any_detected_item = (
                    footer_items[0] if footer_items else None
                )

                if center_item:
                    errors.append({
                        "page": page_num_display,
                        "page_num": page_num_display,
                        "muka_surat": page_num_display,
                        "msg": (
                            f"Kedudukan Nombor Salah: Nombor '{expected_roman}'"
                            " berada di bawah tengah. GP PTA 2026 mewajibkan di"
                            " penjuru bawah kanan."
                        ),
                        "bbox": center_item["bbox"],  # Highlight nombor di tengah
                    })
                elif any_detected_item:
                    errors.append({
                        "page": page_num_display,
                        "page_num": page_num_display,
                        "muka_surat": page_num_display,
                        "msg": (
                            "Nombor muka surat bahagian awalan sepatutnya"
                            f" '{expected_roman}' (Roman kecil) di penjuru bawah"
                            " kanan."
                        ),
                        "bbox": any_detected_item[
                            "bbox"
                        ],  # Highlight nombor salah yang wujud
                    })
                else:
                    # TIADA NOMBOR LANGSUNG -> Tiada kotak merah
                    errors.append({
                        "page": page_num_display,
                        "page_num": page_num_display,
                        "muka_surat": page_num_display,
                        "msg": (
                            "Nombor muka surat bahagian awalan sepatutnya"
                            f" '{expected_roman}' (Roman kecil) di penjuru bawah"
                            " kanan."
                        ),
                        "bbox": None,
                    })

        # D0. LAMPIRAN: tidak boleh ada nombor muka surat
        elif lampiran_idx is not None and page_idx >= lampiran_idx:
            for item in footer_items:
                if (
                    re.fullmatch(r"[\s\-\(\)\.\[\]]*\d{1,3}[\s\-\(\)\.\[\]]*", item["text"])
                    and (item["is_right"] or item["is_center"])
                ):
                    errors.append({
                        "page": page_num_display,
                        "page_num": page_num_display,
                        "muka_surat": page_num_display,
                        "msg": (
                            "Muka surat Lampiran tidak boleh memaparkan nombor muka"
                            f" surat (dikesan '{item['text']}')."
                        ),
                        "bbox": item["bbox"],
                    })
                    break

        # D. BAHAGIAN UTAMA & RUJUKAN/LAMPIRAN (Arab: 1, 2, 3, ...)
        elif page_idx >= bab1_page_idx:
            expected_arabic_val = (page_idx - bab1_page_idx) + 1
            expected_arabic_str = str(expected_arabic_val)

            found_exact_right = any(
                expected_arabic_str in item["numbers"] and item["is_right"]
                for item in footer_items
            )

            if not found_exact_right:
                center_item = next(
                    (
                        item
                        for item in footer_items
                        if expected_arabic_str in item["numbers"]
                        and item["is_center"]
                    ),
                    None,
                )
                any_detected_item = (
                    footer_items[0] if footer_items else None
                )

                if center_item:
                    errors.append({
                        "page": page_num_display,
                        "page_num": page_num_display,
                        "muka_surat": page_num_display,
                        "msg": (
                            f"Kedudukan Nombor Salah: Nombor '{expected_arabic_str}'"
                            " berada di bawah tengah. GP PTA 2026 mewajibkan di"
                            " penjuru bawah kanan."
                        ),
                        "bbox": center_item["bbox"],  # Highlight nombor di tengah
                    })
                elif any_detected_item:
                    errors.append({
                        "page": page_num_display,
                        "page_num": page_num_display,
                        "muka_surat": page_num_display,
                        "msg": (
                            "Nombor muka surat bahagian utama sepatutnya"
                            f" '{expected_arabic_str}' di penjuru bawah kanan."
                        ),
                        "bbox": any_detected_item[
                            "bbox"
                        ],  # Highlight nombor salah yang wujud
                    })
                else:
                    # TIADA NOMBOR LANGSUNG -> Tiada kotak merah
                    errors.append({
                        "page": page_num_display,
                        "page_num": page_num_display,
                        "muka_surat": page_num_display,
                        "msg": (
                            "Nombor muka surat bahagian utama sepatutnya"
                            f" '{expected_arabic_str}' di penjuru bawah kanan."
                        ),
                        "bbox": None,
                    })

    return errors

# =========================================================
# FUNGSI POPUP LOG KEMASKINI (CHANGELOG DIALOG)
# =========================================================
@st.dialog(f"📜 Log Kemaskini (v{APP_VERSION})")
def paparkan_log_kemaskini():
    st.markdown(f"### v{APP_VERSION}")
    st.markdown(
        """
    * **Pengesanan Pintar Tajuk Jadual & Rajah:** Penambahbaikan logik pengecaman objek visual (jadual teks, imej, dan grafik vektor/carta). Sistem kini berupaya membezakan jadual sebenar daripada carta/grafik untuk menyemak kedudukan tajuk serta mengesan kesalahan label secara tepat (contoh: penggunaan label *Jadual* pada *Carta/Graf*).
    * **Pemurnian Kotak Amaran Margin:** Kotak merah amaran margin kini digariskan tepat pada jalur lebihan sahaja (dilukis sehingga garisan margin 40mm/25mm) bagi elak menutupi carta alir atau teks di tengah dokumen.
    * **Kawalan & Reset Memori Bypass:** Penambahbaikan integrasi master bypass dan butang reset semula status isu diabaikan.
    """
    )

    st.divider()

    st.markdown("### v1.3.0")
    st.markdown(
        """
    * **Master Bypass Dokumen:** Penambahan butang *'🌐 Abaikan Semua Isu Dokumen'* untuk menyembunyikan/mengabaikan semua isu di seluruh muka surat secara serentak dengan 1-klik.
    * **Penggabungan Ralat Fon (Aggregated Font Errors):** Amaran jenis dan saiz fon kini digabungkan ke dalam 1 isu ringkas dan 1 kotak gabungan (*combined bbox*).
    * **Semakan Margin Imej & Gambar:** Pengesanan automatik untuk objek grafik, gambarajah, rajah, atau carta alir yang terkeluar dari garisan margin.
    * **Penstabilan UI & Pratonton:** Penambahbaikan struktur pembolehubah pratonton visual untuk memastikan render imej muka surat kekal pantas dan stabil.
    """
    )

    st.divider()

    st.markdown("### v1.2.0")
    st.markdown(
        """
    * **Semakan Justify Perenggan:** Pengesan otomatik bagi perenggan yang tidak diselaraskan (*justified*).
    * **Toleransi Margin Dinamik:** Pelarasan ambang toleransi margin bagi mengelakkan ralat palsu pada teks dan jadual.
    * **Peningkatan UI:** Pengemaskinian reka bentuk sidebar dan kad profil pengguna yang lebih kemas.
    """
    )


def semak_justify_perenggan(
    page,
    target_kanan_mm=25.0,
    tolerance_mm=3.0,
    justify_tolerance_pt=8.0,
    tolerance_pt=None,
):
    """Semakan Margin Kanan & Justified untuk semua muka surat kandungan/teks utama."""

    # -----------------------------------------------------------------
    # 0. ABAIKAN MUKA SURAT TERTENTU (Trik Kebal Spasi & Newline)
    # -----------------------------------------------------------------
    raw_text_top = page.get_text("text")[:1000].upper()
    clean_text_top = re.sub(r"\s+", "", raw_text_top)

    # Kata kunci tanpa spasi
    ignore_keywords_clean = [
        "PERAKUANPENULIS",
        "PENGESAHAN",
        "PENGHARGAAN",
        "SENARAISINGKATAN",
        "SENARAIJADUAL",
        "SENARAIRAJAH",
        "ISIKANDUNGAN",
        "SENARAILAMPIRAN",
        "LAPORANPROJEKYANGDIKEMUKAKAN",
    ]

    # Jika ada salah satu kata kunci terdeteksi, terus abaikan halaman ini
    if any(kw in clean_text_top for kw in ignore_keywords_clean):
        return []

    if tolerance_pt is not None:
        justify_tolerance_pt = tolerance_pt

    PT_TO_MM = 25.4 / 72.0
    MM_TO_PT = 2.83465

    page_width = page.rect.width
    page_height = page.rect.height
    target_margin_pt = page_width - (target_kanan_mm * MM_TO_PT)

    # -----------------------------------------------------------------
    # 1. KESAN KAWASAN JADUAL
    # -----------------------------------------------------------------
    table_bboxes = []
    try:
        tables = page.find_tables()
        for tbl in tables:
            table_bboxes.append(tbl.bbox)
    except Exception:
        pass

    def is_in_table(bbox):
        x0, y0, x1, y1 = bbox
        for tb in table_bboxes:
            if (
                x0 >= (tb[0] - 5)
                and x1 <= (tb[2] + 5)
                and y0 >= (tb[1] - 5)
                and y1 <= (tb[3] + 5)
            ):
                return True
        return False

    # -----------------------------------------------------------------
    # 1b. KESAN BENTUK VEKTOR (cth. carta alir yang dilukis terus dalam Word)
    # -----------------------------------------------------------------
    shape_rects = []
    try:
        for d in page.get_drawings():
            r = d.get("rect")
            if r is None:
                continue
            # Abaikan garisan nipis & bingkai terlalu besar (cth. sempadan muka surat)
            if (
                r.width >= 30
                and r.height >= 18
                and r.width <= page_width * 0.75
                and r.height <= page_height * 0.5
            ):
                shape_rects.append((r.x0, r.y0, r.x1, r.y1))
    except Exception:
        pass

    def is_in_shape(bbox):
        x0, y0, x1, y1 = bbox
        for sx0, sy0, sx1, sy1 in shape_rects:
            if (
                x0 >= (sx0 - 3)
                and x1 <= (sx1 + 3)
                and y0 >= (sy0 - 3)
                and y1 <= (sy1 + 3)
            ):
                return True
        return False

    text_page = page.get_text("dict")
    all_valid_lines = []

    # -----------------------------------------------------------------
    # 2. KUMPUL SEMUA BARIS TEKS UTAMA
    # -----------------------------------------------------------------
    for block in text_page.get("blocks", []):
        if block.get("type", 0) != 0:
            continue

        for line in block.get("lines", []):
            bbox = line["bbox"]

            # Abaikan Header / Footer (Top/Bottom Margin)
            if bbox[1] < 45.0 or bbox[3] > (page_height - 45.0):
                continue

            if is_in_table(bbox):
                continue

            if is_in_shape(bbox):
                continue

            spans = line.get("spans", [])
            if not spans:
                continue

            teks = "".join([s.get("text", "") for s in spans]).strip()

            # Tapis Tajuk Sub-bab / Bab (Kalis Capital & Lowercase: BAB 4, Bab 4, bab 4, 4.1, dll)
            is_heading = re.match(
                r"^(BAB\s+\d+|BAB\s+[IVXLCDM]+|\d+(\.\d+)*|(JADUAL|RAJAH|GAMBAR|CARTA|FIGURE|TABLE)\s*\d)",
                teks,
                re.IGNORECASE,
            )

            # Tapis Tajuk Utama di Tengah (contoh: "DAPATAN DAN ANALISIS")
            is_centered_heading = (
                bbox[0] > 90.0
                and bbox[2] < (page_width - 90.0)
                and teks.isupper()
            )

            if len(teks) >= 10 and not is_heading and not is_centered_heading:
                all_valid_lines.append({"bbox": bbox, "text": teks})

    if len(all_valid_lines) < 2:
        return []

    lines_by_y = sorted(all_valid_lines, key=lambda l: l["bbox"][1])

    # -----------------------------------------------------------------
    # 3. SEMAK MARGIN KANAN (TERKELUAR VS TERLALU JAUH KE DALAM)
    # -----------------------------------------------------------------
    ralat = []

    # Toleransi isu terkeluar ke kanan: dimaafkan sehingga 1.5mm
    overflow_tolerance_pt = 1.5 * MM_TO_PT

    # Teks yang sedikit melepasi margin (>0.5mm) tidak dilaporkan, tetapi
    # dikira supaya isu tambahan 'melepasi tebing perenggan lain' tak keluar
    ada_terkeluar_kecil = any(
        l["bbox"][2] > (target_margin_pt + 0.5 * MM_TO_PT) for l in lines_by_y
    )

    # A) SEMAK SEKIRANYA ADA BARIS TEKS TERKELUAR (KOTAK UNGU)
    overflow_lines = [
        l for l in lines_by_y
        if l["bbox"][2] > (target_margin_pt + overflow_tolerance_pt)
    ]

    if overflow_lines:
        min_y0 = min(l["bbox"][1] for l in overflow_lines)
        max_y1 = max(l["bbox"][3] for l in overflow_lines)
        max_overflow_x1 = max(l["bbox"][2] for l in overflow_lines)

        terkeluar_mm = (max_overflow_x1 - target_margin_pt) * PT_TO_MM
        gap_bbox = (target_margin_pt, min_y0, max_overflow_x1, max_y1)

        ralat.append({
            "msg": f"Luar Margin Kanan: Teks terkeluar sebanyak {terkeluar_mm:.1f}mm melepasi margin kanan.",
            "bbox": gap_bbox,
        })

    # B) JIKA TEKS TERLALU JAUH KE DALAM / MARGIN KANAN TERLALU LUAS
    max_x1 = max(l["bbox"][2] for l in lines_by_y)
    dikesan_margin_mm = (page_width - max_x1) * PT_TO_MM

    if dikesan_margin_mm > (target_kanan_mm + tolerance_mm):
        min_y0 = min(l["bbox"][1] for l in lines_by_y)
        max_y1 = max(l["bbox"][3] for l in lines_by_y)

        gap_bbox = (max_x1, min_y0, target_margin_pt, max_y1)

        ralat.append({
            "msg": (
                f"Margin Kanan Salah: Dikesan {dikesan_margin_mm:.1f}mm"
                f" (Sepatutnya {target_kanan_mm:.0f}mm). Sila ubah Margin Kanan di Layout Word."
            ),
            "bbox": gap_bbox,
        })
        return ralat

    # -----------------------------------------------------------------
    # 4. SEMAK JUSTIFIED
    # -----------------------------------------------------------------
    middle_lines = []
    for i in range(len(lines_by_y) - 1):
        curr = lines_by_y[i]
        next_l = lines_by_y[i + 1]

        v_gap = next_l["bbox"][1] - curr["bbox"][3]
        same_left = abs(next_l["bbox"][0] - curr["bbox"][0]) < 45.0

        ends_with_punctuation = curr["text"].strip().endswith(
            (".", ":", ";", "?")
        )

        if v_gap < 14.0 and same_left and not ends_with_punctuation:
            middle_lines.append(curr)

    if not middle_lines:
        return ralat

    unjustified_lines = []
    page_max_x1 = max(l["bbox"][2] for l in middle_lines)

    for l in middle_lines:
        x1 = l["bbox"][2]
        dist_to_margin = target_margin_pt - x1
        dist_to_max = page_max_x1 - x1

        if (
            dist_to_margin > justify_tolerance_pt
            or dist_to_max > justify_tolerance_pt
        ) and dist_to_margin < 120.0:
            unjustified_lines.append(l)

    if unjustified_lines:
        min_unjust_x1 = min(l["bbox"][2] for l in unjustified_lines)
        min_y = min(l["bbox"][1] for l in unjustified_lines)
        max_y = max(l["bbox"][3] for l in unjustified_lines)

        max_selisih_mm = max(
            (target_margin_pt - l["bbox"][2]) * PT_TO_MM
            for l in unjustified_lines
        )

        gap_bbox = (min_unjust_x1, min_y, target_margin_pt, max_y)

        ralat.append({
            "msg": (
                "Penjajaran teks tidak 'Justified': Tebing kanan tidak"
                f" selaras ({max_selisih_mm:.1f}mm dari margin)."
            ),
            "bbox": gap_bbox,
        })

        # ---------------------------------------------------------
        # TAMBAHAN: kumpulan perenggan yang tebing kanannya
        # terkeluar melepasi tebing perenggan lain di muka surat sama
        # ---------------------------------------------------------
        unj_ids = {id(l) for l in unjustified_lines}
        ok_lines = [l for l in middle_lines if id(l) not in unj_ids]

        if not ada_terkeluar_kecil and len(unjustified_lines) >= 2 and len(ok_lines) >= 2:
            u_x1 = [l["bbox"][2] for l in unjustified_lines]
            o_x1 = [l["bbox"][2] for l in ok_lines]

            # Setiap kumpulan mesti rapat (selaras dalam kumpulan sendiri)
            u_rapat = (max(u_x1) - min(u_x1)) <= justify_tolerance_pt
            o_rapat = (max(o_x1) - min(o_x1)) <= justify_tolerance_pt
            u_purata = sum(u_x1) / len(u_x1)
            o_purata = sum(o_x1) / len(o_x1)
            beza_pt = o_purata - u_purata

            if u_rapat and o_rapat and beza_pt > justify_tolerance_pt:
                min_y2 = min(l["bbox"][1] for l in ok_lines)
                max_y2 = max(l["bbox"][3] for l in ok_lines)
                gap_bbox2 = (
                    u_purata,
                    min_y2,
                    max(max(o_x1), target_margin_pt),
                    max_y2,
                )
                ralat.append({
                    "msg": (
                        "Penjajaran teks tidak 'Justified': Tebing kanan"
                        f" perenggan ini terkeluar {beza_pt * PT_TO_MM:.1f}mm"
                        " melepasi tebing kanan perenggan lain."
                        " Sila seragamkan Right Indent / penjajaran."
                    ),
                    "bbox": gap_bbox2,
                })

    return ralat


PT_TO_MM = 2.83465


def is_page_number(text):
    txt = text.strip().lower()
    return txt.isdigit() or bool(
        re.match(r"^(?=[i|v|x|l|c|d|m]+$)[i|v|x|l|c|d|m]+$", txt)
    )


def format_margin_msg(
    jenis_elemen,
    teks_sampel,
    dist_from_edge_mm,
    target_mm=25,
    jenis_margin="Kiri",
):
    """dist_from_edge_mm:

    - Margin Kiri: Jarak x0 dari tepi kiri (mm)
    - Margin Kanan: Jarak x1 dari tepi kanan kertas (mm)
    """
    # Berapa mm teks tersebut melepasi/terkeluar dari garisan margin minimum (cth: 25mm)
    terkeluar_mm = round(target_mm - dist_from_edge_mm, 1)
    pos_mm = round(dist_from_edge_mm, 1)

    teks_clean = f"'{teks_sampel[:15]}...'" if teks_sampel else ""

    # Jika terkeluar_mm <= 0, bermakna teks berada dalam kawasan selamat (tiada ralat)
    return (
        f"Luar Margin {jenis_margin}: {jenis_elemen} {teks_clean} "
        f"(Jarak tepi: {pos_mm}mm | Terkeluar {terkeluar_mm}mm dari {target_mm}mm)"
    )


PT_TO_MM = 25.4 / 72.0  # Faktor penukaran point ke milimeter
TOLERANCE_MM = 1.5  # Toleransi margin (laraskan ke 2.0 jika sensitif)


def check_margin_kiri_violations(page, target_margin_mm=40, tolerance_mm=1.5):
    raw_text_errors = []
    raw_drawing_errors = []
    raw_image_errors = []

    # 📌 1. ISYTIHAR DI SINI (ATAS SEKALI) SUPAYA SENTIASA ADA NILAI!
    limit_pt = (target_margin_mm - tolerance_mm) / PT_TO_MM
    margin_line_pt = target_margin_mm / PT_TO_MM  # <--- PINDAH KE SINI

    text_page = page.get_text("dict")

    # -----------------------------------------------------------------
    # 1. KUMPUL SEMUA TEKS YANG TERKELUAR MARGIN KIRI
    # -----------------------------------------------------------------
    for block in text_page.get("blocks", []):
        for line in block.get("lines", []):
            x0, y0, x1, y1 = line["bbox"]
            line_text = "".join(
                [s.get("text", "") for s in line.get("spans", [])]
            ).strip()

            if x0 < limit_pt and line_text:
                if not is_page_number(line_text):
                    x0_mm = x0 * PT_TO_MM
                    diff_mm = target_margin_mm - x0_mm
                    raw_text_errors.append({
                        "bbox": (x0, y0, x1, y1),
                        "diff_mm": diff_mm,
                    })

    # -----------------------------------------------------------------
    # 2. KUMPUL SEMUA GARISAN / TITIK VEKTOR YANG TERKELUAR
    # -----------------------------------------------------------------
    for draw in page.get_drawings():
        rect = draw["rect"]
        if 5 < rect.x0 < limit_pt and (rect.y1 - rect.y0) <= (
            page.rect.height * 0.8
        ):
            bbox_tuple = (rect.x0, rect.y0, rect.x1, rect.y1)
            if not any(e["bbox"] == bbox_tuple for e in raw_drawing_errors):
                x0_mm = rect.x0 * PT_TO_MM
                diff_mm = target_margin_mm - x0_mm
                raw_drawing_errors.append({
                    "bbox": bbox_tuple,
                    "diff_mm": diff_mm,
                })

    # -----------------------------------------------------------------
    # 3. KUMPUL SEMUA IMEJ / GAMBAR YANG TERKELUAR MARGIN KIRI
    # -----------------------------------------------------------------
    images_info = page.get_image_info()
    for img in images_info:
        img_x0 = img["bbox"][0]
        if img_x0 < limit_pt:
            x0_mm = img_x0 * PT_TO_MM
            diff_mm = target_margin_mm - x0_mm
            raw_image_errors.append({
                "bbox": img["bbox"],
                "diff_mm": diff_mm,
            })

    errors = []

    # -----------------------------------------------------------------
    # 4. RUMUSKAN TEKS KEPADA 1 ISU & 1 KOTAK BESAR
    # -----------------------------------------------------------------
    if raw_text_errors:
        all_bboxes = [e["bbox"] for e in raw_text_errors]
        diffs = [e["diff_mm"] for e in raw_text_errors]
        min_diff = min(diffs)
        max_diff = max(diffs)
        count = len(raw_text_errors)

        combined_bbox = (
            min(b[0] for b in all_bboxes),  # x0: Paling luar sebelah kiri
            min(b[1] for b in all_bboxes),  # y0: Paling atas
            margin_line_pt,  # x1: SEHINGGA GARISAN MARGIN KIRI SAHAJA!
            max(b[3] for b in all_bboxes),  # y1: Paling bawah
        )

        if count == 1 or abs(max_diff - min_diff) < 0.1:
            msg = (
                f"Luar Margin Kiri (Teks): Terkeluar {min_diff:.1f}mm dari"
                f" sasaran {target_margin_mm}mm."
            )
        else:
            msg = (
                f"Luar Margin Kiri (Teks: {count} baris): Terkeluar"
                f" {min_diff:.1f}mm - {max_diff:.1f}mm dari sasaran"
                f" {target_margin_mm}mm."
            )

        errors.append({"msg": msg, "bbox": combined_bbox})

    # -----------------------------------------------------------------
    # 5. RUMUSKAN GARISAN KEPADA 1 ISU SAHAJA
    # -----------------------------------------------------------------
    if raw_drawing_errors:
        all_bboxes = [e["bbox"] for e in raw_drawing_errors]
        diffs = [e["diff_mm"] for e in raw_drawing_errors]
        min_diff = min(diffs)
        max_diff = max(diffs)
        count = len(raw_drawing_errors)

        combined_bbox = (
            min(b[0] for b in all_bboxes),
            min(b[1] for b in all_bboxes),
            margin_line_pt,  # ✅ Selamat digunakan walaupun tiada raw_text_errors
            max(b[3] for b in all_bboxes),
        )

        if count == 1 or abs(max_diff - min_diff) < 0.1:
            msg = (
                f"Luar Margin Kiri (Garisan/Carta): Terkeluar {min_diff:.1f}mm"
                f" dari sasaran {target_margin_mm}mm."
            )
        else:
            msg = (
                f"Luar Margin Kiri (Garisan/Carta: {count} objek): Terkeluar"
                f" {min_diff:.1f}mm - {max_diff:.1f}mm dari sasaran"
                f" {target_margin_mm}mm."
            )

        errors.append({"msg": msg, "bbox": combined_bbox})

    # -----------------------------------------------------------------
    # 6. RUMUSKAN IMEJ KEPADA 1 ISU SAHAJA
    # -----------------------------------------------------------------
    if raw_image_errors:
        all_bboxes = [e["bbox"] for e in raw_image_errors]
        diffs = [e["diff_mm"] for e in raw_image_errors]
        min_diff = min(diffs)
        max_diff = max(diffs)
        count = len(raw_image_errors)

        combined_bbox = (
            min(b[0] for b in all_bboxes),
            min(b[1] for b in all_bboxes),
            margin_line_pt,  # ✅ Selamat digunakan walaupun tiada raw_text_errors
            max(b[3] for b in all_bboxes),
        )

        if count == 1 or abs(max_diff - min_diff) < 0.1:
            msg = (
                f"Luar Margin Kiri (Imej/Gambar): Terkeluar {min_diff:.1f}mm"
                f" dari sasaran {target_margin_mm}mm."
            )
        else:
            msg = (
                f"Luar Margin Kiri (Imej/Gambar: {count} objek): Terkeluar"
                f" {min_diff:.1f}mm - {max_diff:.1f}mm dari sasaran"
                f" {target_margin_mm}mm."
            )

        errors.append({"msg": msg, "bbox": combined_bbox})

    return errors

def check_margin_kanan_violations(
    page,
    target_margin_mm=25,
    tolerance_mm=2.5,
    object_tolerance_mm=0.5,
):
    """Khusus untuk mengesan TEKS TERKELUAR (< 22.5mm), OBJEK/GRAFIK, dan JADUAL melimpah margin kanan.

    Diperbaiki: Garisan/vektor yang tergolong dalam kawasan JADUAL tidak lagi
    dikesan dua kali sebagai 'Graf/Objek Visual'.
    """

    # -----------------------------------------------------------------
    # 0. ABAIKAN MUKA SURAT TERTENTU (Abaikan spasi & newline)
    # -----------------------------------------------------------------
    raw_text_top = page.get_text("text")[:1000].upper()
    clean_text_top = re.sub(r"\s+", "", raw_text_top)

    ignore_keywords_clean = [
        "PERAKUANPENULIS",
        "PENGESAHAN",
        "PENGHARGAAN",
        "SENARAISINGKATAN",
        "SENARAIJADUAL",
        "SENARAIRAJAH",
        "ISIKANDUNGAN",
        "SENARAILAMPIRAN",
    ]

    if any(kw in clean_text_top for kw in ignore_keywords_clean):
        return []

    PT_TO_MM = 25.4 / 72.0
    page_width_pt = page.rect.width
    page_height_pt = page.rect.height
    page_width_mm = page_width_pt * PT_TO_MM

    min_text_mm = target_margin_mm - tolerance_mm  # 22.5mm
    limit_obj_mm = target_margin_mm - object_tolerance_mm
    limit_x1_obj_pt = page_width_pt - (limit_obj_mm / PT_TO_MM)
    target_margin_pt = page_width_pt - (target_margin_mm / PT_TO_MM)

    errors = []

    # -----------------------------------------------------------------
    # 1. KESAN JADUAL TERLEBIH DAHULU (Simpan BBox Jadual)
    # -----------------------------------------------------------------
    table_bboxes = []
    try:
        for tbl in page.find_tables():
            bbox = tbl.bbox
            table_bboxes.append(bbox)
            tbl_x0, tbl_y0, tbl_x1, tbl_y1 = bbox

            if tbl_x1 > limit_x1_obj_pt:
                x1_mm = tbl_x1 * PT_TO_MM
                dist_right_mm = page_width_mm - x1_mm
                diff_mm = target_margin_mm - dist_right_mm

                gap_bbox = (target_margin_pt, tbl_y0, tbl_x1, tbl_y1)

                msg = (
                    f"Luar Margin Kanan (Jadual): Terkeluar {diff_mm:.1f}mm dari "
                    f"sasaran {target_margin_mm}mm."
                )
                errors.append({"msg": msg, "bbox": gap_bbox})
    except Exception:
        pass

    def is_inside_table(bbox):
        """Semak adakah elemen/garisan berada di dalam atau sebahagian daripada jadual."""
        x0, y0, x1, y1 = bbox
        for tb in table_bboxes:
            # Jika koordinat bertindih dengan kawasan jadual (+- 5pt margin bertolak ansur)
            if not (
                x1 < (tb[0] - 5)
                or x0 > (tb[2] + 5)
                or y1 < (tb[1] - 5)
                or y0 > (tb[3] + 5)
            ):
                return True
        return False

    # -----------------------------------------------------------------
    # 2. SEMAK TEKS MELIMPAH KELUAR (< 22.5mm)
    # -----------------------------------------------------------------
    raw_text_errors = []
    words = page.get_text("words")
    if words:
        lines_dict = {}
        for w in words:
            y_approx = round(w[1] / 3.0) * 3
            key = (w[5], y_approx)
            if key not in lines_dict:
                lines_dict[key] = []
            lines_dict[key].append(w)

        for key, line_words in lines_dict.items():
            line_x0 = min(w[0] for w in line_words)
            line_y0 = min(w[1] for w in line_words)
            line_x1 = max(w[2] for w in line_words)
            line_y1 = max(w[3] for w in line_words)
            full_text = " ".join([w[4] for w in line_words]).strip()

            if not full_text or is_page_number(full_text):
                continue

            x1_mm = line_x1 * PT_TO_MM
            dist_right_mm = page_width_mm - x1_mm

            if dist_right_mm < min_text_mm:
                diff_mm = target_margin_mm - dist_right_mm
                raw_text_errors.append({
                    "bbox": (line_x0, line_y0, line_x1, line_y1),
                    "diff_mm": diff_mm,
                })

    if raw_text_errors:
        all_bboxes = [e["bbox"] for e in raw_text_errors]
        diffs = [e["diff_mm"] for e in raw_text_errors]
        min_diff, max_diff = min(diffs), max(diffs)
        count = len(raw_text_errors)

        box_y0 = min(b[1] for b in all_bboxes)
        box_x1 = max(b[2] for b in all_bboxes)
        box_y1 = max(b[3] for b in all_bboxes)

        combined_bbox = (target_margin_pt, box_y0, box_x1, box_y1)

        if count == 1 or abs(max_diff - min_diff) < 0.1:
            msg = (
                f"Luar Margin Kanan (Teks): Terkeluar {min_diff:.1f}mm dari sasaran"
                f" {target_margin_mm}mm."
            )
        else:
            msg = (
                f"Luar Margin Kanan (Teks: {count} baris): Terkeluar"
                f" {min_diff:.1f}mm - {max_diff:.1f}mm dari sasaran"
                f" {target_margin_mm}mm."
            )

        errors.append({"msg": msg, "bbox": combined_bbox})

    # -----------------------------------------------------------------
    # 3. SEMAK OBJEK / GRAF (DIABAIKAN JIKA DALAM JADUAL)
    # -----------------------------------------------------------------
    raw_object_errors = []
    page_dict = page.get_text("dict")

    # (a) Image blocks
    for b in page_dict.get("blocks", []):
        if b.get("type") == 1:
            bbox = b["bbox"]
            if is_inside_table(bbox):
                continue  # Abaikan jika imej dalam jadual

            x1 = bbox[2]
            if (bbox[2] - bbox[0]) > (page_width_pt * 0.95) or (
                bbox[3] - bbox[1]
            ) > (page_height_pt * 0.95):
                continue
            if x1 > limit_x1_obj_pt:
                x1_mm = x1 * PT_TO_MM
                dist_right_mm = page_width_mm - x1_mm
                diff_mm = target_margin_mm - dist_right_mm
                raw_object_errors.append({"bbox": bbox, "diff_mm": diff_mm})

    # (b) XObjects
    try:
        for xobj in page.get_xobjects():
            if len(xobj) >= 4 and xobj[3]:
                bbox = xobj[3]
                if is_inside_table(bbox):
                    continue  # Abaikan jika dalam jadual

                x1 = bbox[2]
                if (bbox[2] - bbox[0]) > (page_width_pt * 0.95):
                    continue
                if x1 > limit_x1_obj_pt:
                    x1_mm = x1 * PT_TO_MM
                    dist_right_mm = page_width_mm - x1_mm
                    diff_mm = target_margin_mm - dist_right_mm
                    raw_object_errors.append({"bbox": bbox, "diff_mm": diff_mm})
    except Exception:
        pass

    # (c) Drawings (Garisan/Vektor)
    for d in page.get_drawings():
        rect = d["rect"]
        bbox = (rect.x0, rect.y0, rect.x1, rect.y1)

        if is_inside_table(bbox):
            continue  # 📌 PENAPIS UTAMA: Abaikan garisan bingkai jadual!

        if (rect.x1 - rect.x0) > (page_width_pt * 0.95) or (
            rect.y1 - rect.y0
        ) > (page_height_pt * 0.95):
            continue
        if rect.x1 > limit_x1_obj_pt:
            x1_mm = rect.x1 * PT_TO_MM
            dist_right_mm = page_width_mm - x1_mm
            diff_mm = target_margin_mm - dist_right_mm
            raw_object_errors.append({"bbox": bbox, "diff_mm": diff_mm})

    if raw_object_errors:
        all_bboxes = [e["bbox"] for e in raw_object_errors]
        diffs = [e["diff_mm"] for e in raw_object_errors]
        max_diff = max(diffs)

        box_y0 = min(b[1] for b in all_bboxes)
        box_x1 = max(b[2] for b in all_bboxes)
        box_y1 = max(b[3] for b in all_bboxes)

        combined_obj_bbox = (target_margin_pt, box_y0, box_x1, box_y1)

        msg = (
            "Luar Margin Kanan (Graf/Objek Visual): Terkeluar"
            f" {max_diff:.1f}mm dari sasaran {target_margin_mm}mm."
        )
        errors.append({"msg": msg, "bbox": combined_obj_bbox})

    return errors


def semak_kedudukan_tajuk_jadual(page):
    """Semak jika tajuk Jadual diletakkan di BAWAH jadual (Mesti di ATAS)

    atau disalah guna untuk Graf/Carta/Imej (Sepatutnya Rajah).
    """
    errors = []
    page_dict = page.get_text("dict")
    blocks = page_dict.get("blocks", [])

    # 1. Kesan Jadual Teks Sebenar guna fungsi khas PyMuPDF
    table_bboxes = []
    try:
        tables = page.find_tables()
        for tab in tables:
            table_bboxes.append(tab.bbox)
    except Exception:
        pass

    # 2. Kumpul Objek Visual (Imej, Garisan Jadual & Grafik Vektor)
    visual_bboxes = []

    # A. Imej (Bitmap)
    for b in blocks:
        if b.get("type") == 1:
            bbox = b["bbox"]
            if (bbox[2] - bbox[0]) < (page.rect.width * 0.95) and (
                bbox[3] - bbox[1]
            ) < (page.rect.height * 0.95):
                visual_bboxes.append(("IMEJ", bbox))

    # B. Grafik Vektor & Garisan Jadual (Guna 'OR' supaya garisan melintang/menegak jadual diambil kira)
    for d in page.get_drawings():
        rect = d["rect"]
        w, h = rect.x1 - rect.x0, rect.y1 - rect.y0

        if w < (page.rect.width * 0.95) and h < (page.rect.height * 0.95):
            # TUKAR 'AND' KEPADA 'OR': Garisan melintang jadual selalunya w > 20 tapi h = 1
            if w > 20 or h > 20:
                is_line = (w > 40 and h < 5) or (h > 40 and w < 5)
                obj_type = "GARISAN_JADUAL" if is_line else "CARTA_VEKTOR"
                visual_bboxes.append(
                    (obj_type, (rect.x0, rect.y0, rect.x1, rect.y1))
                )

    # 3. Semak Setiap Blok Teks Tajuk 'Jadual'
    for b in blocks:
        if b.get("type") != 0 or "lines" not in b:
            continue

        full_block_text = " ".join(
            span["text"] for line in b["lines"] for span in line["spans"]
        ).strip()

        if re.match(r"^Jadual\s+\d+(\.\d+)*", full_block_text, re.IGNORECASE):
            caption_bbox = b["bbox"]
            caption_y0, caption_y1 = caption_bbox[1], caption_bbox[3]
            caption_x0, caption_x1 = caption_bbox[0], caption_bbox[2]

            min_dist_above = float("inf")
            min_dist_below = float("inf")
            obj_type_above = None

            # A. Semak adakah terdapat JADUAL daripada find_tables() di atas tajuk
            is_real_table_above = False
            for t_bbox in table_bboxes:
                if (
                    t_bbox[3] <= caption_y0 + 15
                    and (caption_y0 - t_bbox[3]) < 250
                ):
                    is_real_table_above = True
                    break

            # B. Semak jarak garisan jadual / imej / carta di atas dan di bawah tajuk
            for obj_type, v_bbox in visual_bboxes:
                v_x0, v_y0, v_x1, v_y1 = v_bbox
                if v_x1 < caption_x0 - 100 or v_x0 > caption_x1 + 100:
                    continue

                if v_y1 <= caption_y0 + 15:
                    dist = caption_y0 - v_y1
                    if 0 <= dist < min_dist_above:
                        min_dist_above = dist
                        obj_type_above = obj_type

                if v_y0 >= caption_y1 - 15:
                    dist = v_y0 - caption_y1
                    if 0 <= dist < min_dist_below:
                        min_dist_below = dist

            # JIKA ADA JADUAL / GARISAN / OBJEK DI ATAS TAJUK (Jarak < 250pt) DAN TIADA OBJEK DI BAWAH
            if (
                is_real_table_above or min_dist_above < 250
            ) and min_dist_below > 50:
                tajuk_short = (
                    full_block_text[:35] + "..."
                    if len(full_block_text) > 35
                    else full_block_text
                )

                # KES 1: Jika objek kat atas tu Graf/Carta/Imej (bukan jadual)
                if not is_real_table_above and obj_type_above in [
                    "IMEJ",
                    "CARTA_VEKTOR",
                ]:
                    errors.append({
                        "msg": (
                            f"Salah Label Objek: Tajuk '{tajuk_short}' bermula"
                            " dengan 'Jadual', tetapi objek di atasnya dikesan"
                            " sebagai Graf/Carta/Imej. Sepatutnya dinamakan"
                            " 'Rajah X.X' (bukan Jadual)."
                        ),
                        "bbox": caption_bbox,
                    })
                # KES 2: Jika objek kat atas tu Jadual Teks Sebenar / Garisan Jadual
                else:
                    errors.append({
                        "msg": (
                            f"Kedudukan Tajuk Salah: Tajuk '{tajuk_short}'"
                            " bermula dengan 'Jadual' tetapi diletakkan di BAWAH"
                            " jadual. Format menetapkan tajuk Jadual MESTI di"
                            " ATAS."
                        ),
                        "bbox": caption_bbox,
                    })

    return errors


def semak_kedudukan_tajuk_rajah(page, max_vertical_gap_pt=150.0):
    """Semakan kedudukan tajuk Rajah / Figure.

    Tajuk Rajah MESTI berada di BAWAH imej/grafik.

    Logik diperbaiki:
    - Jika terdapat imej di ATAS tajuk, kedudukan tajuk dikira BETUL.
    - Ralat HANYA dicetuskan jika tajuk berada di atas imej DAN tiada imej di atas
      tajuk tersebut.
    """

    # -----------------------------------------------------------------
    # 0. ABAIKAN MUKA SURAT TERTENTU (Trik Kebal Spasi & Newline)
    # -----------------------------------------------------------------
    raw_text_top = page.get_text("text")[:1000].upper()
    clean_text_top = re.sub(r"\s+", "", raw_text_top)

    ignore_keywords_clean = [
        "PERAKUANPENULIS",
        "PENGESAHAN",
        "PENGHARGAAN",
        "SENARAISINGKATAN",
        "SENARAIJADUAL",
        "SENARAIRAJAH",
        "ISIKANDUNGAN",
        "SENARAILAMPIRAN",
    ]

    if any(kw in clean_text_top for kw in ignore_keywords_clean):
        return []

    # -----------------------------------------------------------------
    # 1. KUMPUL KAWASAN IMEJ / OBJEK / DRAWINGS
    # -----------------------------------------------------------------
    image_bboxes = []

    # (a) Block Imej (Type 1)
    page_dict = page.get_text("dict")
    for b in page_dict.get("blocks", []):
        if b.get("type") == 1:
            image_bboxes.append(b["bbox"])

    # (b) XObjects (Imej PDF)
    try:
        for xobj in page.get_xobjects():
            if len(xobj) >= 4 and xobj[3]:
                image_bboxes.append(xobj[3])
    except Exception:
        pass

    # (c) Drawings (Vektor / Graf)
    for d in page.get_drawings():
        rect = d["rect"]
        # Abaikan garisan bingkai penuh muka surat
        if (rect.x1 - rect.x0) < page.rect.width * 0.9 and (
            rect.y1 - rect.y0
        ) < page.rect.height * 0.9:
            image_bboxes.append((rect.x0, rect.y0, rect.x1, rect.y1))

    if not image_bboxes:
        return []

    # -----------------------------------------------------------------
    # 2. SEMAK KEDUDUKAN TAJUK RAJAH / FIGURE
    # -----------------------------------------------------------------
    ralat = []
    blocks = page_dict.get("blocks", [])

    for b in blocks:
        if b.get("type") != 0:
            continue

        for line in b.get("lines", []):
            spans = line.get("spans", [])
            line_text = "".join([s.get("text", "") for s in spans]).strip()

            # Semak jika baris bermula dengan "Rajah" atau "Figure"
            if re.match(r"^(Rajah|Figure)\s+\d+", line_text, re.IGNORECASE):
                cap_bbox = line["bbox"]
                cap_x0, cap_y0, cap_x1, cap_y1 = cap_bbox

                # Langkah A: Semak adakah terdapat imej di ATAS tajuk ini
                has_image_above = False
                for img_box in image_bboxes:
                    img_x0, img_y0, img_x1, img_y1 = img_box

                    # Adakah imej berada di atas tajuk (berada dalam jarak munisipal yang munasabah)?
                    is_above = (img_y1 <= cap_y0 + 15.0) and (
                        cap_y0 - img_y1 <= max_vertical_gap_pt
                    )

                    if is_above:
                        has_image_above = True
                        break

                # 📌 JIKA ADA IMEJ DI ATAS TAJUK: Kedudukan Rajah ini SAH (Abaikan semakan lanjut)
                if has_image_above:
                    continue

                # Langkah B: Jika TIADA imej di atas, semak adakah terdapat imej di BAWAH tajuk ini
                has_image_below = False
                for img_box in image_bboxes:
                    img_x0, img_y0, img_x1, img_y1 = img_box
                    is_below = (img_y0 >= cap_y1 - 15.0) and (
                        img_y0 - cap_y1 <= max_vertical_gap_pt
                    )
                    if is_below:
                        has_image_below = True
                        break

                # Ralat HANYA dicetuskan jika tajuk berada di atas imej TANPA sebarang imej di atas tajuk tersebut
                if has_image_below and not has_image_above:
                    ralat.append({
                        "msg": (
                            f"Kedudukan Tajuk Salah: Tajuk '{line_text}' bermula"
                            " dengan 'Rajah/Figure' tetapi diletakkan di ATAS"
                            " imej/graf. Format menetapkan tajuk Rajah MESTI di"
                            " BAWAH."
                        ),
                        "bbox": cap_bbox,
                    })

    return ralat


def check_font_violations(page, allowed_fonts=["arial"], min_font_size=8.0):
    # =========================================================
    # 🟢 SAKELAR FASA PENGUJIAN (DISABLE / ENABLE FONT)
    # =========================================================
    DISABLE_FONT_CHECK = (
        True  # Set True = BYPASS (Abaikan) | Set False = ON (Semak semula)
    )

    if DISABLE_FONT_CHECK:
        return []  # Terus kembalikan senarai kosong (Abaikan semakan font)
    # =========================================================

    page_text = page.get_text("text")

    # 📌 1. ABAIKAN SEPENUHNYA JIKA MUKA SURAT INI ADALAH "LAMPIRAN"
    if re.search(r"\bLAMPIRAN\b", page_text, re.IGNORECASE):
        return []

    raw_font_name_errors = []
    raw_font_size_errors = []
    text_page = page.get_text("dict")

    # 📌 2. KUMPULKAN KAWASAN VISUAL (GRAF / CARTA GANTT / IMEJ / VEKTOR)
    visual_bboxes = []
    for b in text_page.get("blocks", []):
        if b.get("type") == 1:  # Gambar / Imej
            visual_bboxes.append(b["bbox"])

    for d in page.get_drawings():  # Garisan Vector / Jadual / Carta Gantt
        rect = d["rect"]
        visual_bboxes.append((rect.x0, rect.y0, rect.x1, rect.y1))

    # Helper untuk semak sama ada teks berada di dalam kawasan Graf/Carta/Jadual
    def is_text_inside_visual(span_bbox):
        sx0, sy0, sx1, sy1 = span_bbox
        scx = (sx0 + sx1) / 2
        scy = (sy0 + sy1) / 2

        for vx0, vy0, vx1, vy1 in visual_bboxes:
            if (vx1 - vx0) > 15 and (vy1 - vy0) > 15:
                if vx0 - 5 <= scx <= vx1 + 5 and vy0 - 5 <= scy <= vx1 + 5:
                    return True
        return False

    # 📌 3. SEMAKAN TEKS DOKUMEN
    for block in text_page.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                raw_text = (
                    span.get("text", "")
                    .replace("\xa0", " ")
                    .replace("\u200b", "")
                    .strip()
                )

                if not raw_text or not any(c.isalnum() for c in raw_text):
                    continue

                bbox = span.get("bbox")
                if not bbox:
                    continue

                # 📌 ABAIKAN (SKIP) FONT & SAIZ JIKA TEKS DALAM CARTA / JADUAL / GRAF
                if is_text_inside_visual(bbox):
                    continue

                font_name = span.get("font", "")
                font_size = span.get("size", 0.0)

                # 1. Bersihkan nama font (buang prefix PDF 'ABCDEF+', sengkang, dan ruang)
                font_clean = (
                    font_name.split("+")[-1]
                    .lower()
                    .replace(" ", "")
                    .replace("-", "")
                    .replace("_", "")
                )

                # 📌 Letak betul-betul sebelum pembolehubah 'is_valid_font'
                print(
                    f"🔍 DEBUG FONT -> Teks: '{raw_text}' | Font PDF:"
                    f" '{font_name}' | Allowed: {allowed_fonts}"
                )

                # 2. Semak padanan fleksibel
                is_valid_font = False
                for f in allowed_fonts:
                    f_clean = (
                        f.lower()
                        .replace(" ", "")
                        .replace("-", "")
                        .replace("_", "")
                    )
                    # Jika perkataan 'aptos' wujud dalam nama font PDF (cth: 'aptosdisplay'), luluskan!
                    if f_clean in font_clean or font_clean in f_clean:
                        is_valid_font = True
                        break

                if not is_valid_font:
                    raw_font_name_errors.append({
                        "font_name": font_name,
                        "bbox": bbox,
                        "text": raw_text,
                    })

                if font_size < min_font_size:
                    raw_font_size_errors.append({
                        "font_size": font_size,
                        "bbox": bbox,
                        "text": raw_text,
                    })

    # 🔍 KODE DEBUGGING: Cetak ke terminal
    print("--- DETEKSI FONT ERROR ---")
    for err in raw_font_name_errors:
        print(
            f"Font: '{err['font_name']}', Teks: '{err['text']}', BBox:"
            f" {err['bbox']}"
        )
    print("--------------------------")

    errors = []

    # 📌 SEMAKAN JENIS FONT (FONT NAME)
    if raw_font_name_errors:
        all_bboxes = [e["bbox"] for e in raw_font_name_errors]
        unique_fonts = list(set(e["font_name"] for e in raw_font_name_errors))
        fonts_str = ", ".join(unique_fonts[:2])
        count = len(raw_font_name_errors)
        sample_text = raw_font_name_errors[0]["text"][:25]

        # 🟢 GUNA KAMPOI: Jika < 3 lokasi, kekal kotak kecil berasingan
        processed_bboxes = kampoi_bbox(all_bboxes, ambang_kampoi=3)

        msg = (
            f"Jenis font tidak sah ({fonts_str} pada teks '{sample_text}':"
            f" {count} lokasi dikesan). Digalakkan guna Arial."
        )

        for bbox in processed_bboxes:
            errors.append({"msg": msg, "bbox": bbox})

    # 📌 SEMAKAN SAIZ FONT (FONT SIZE)
    if raw_font_size_errors:
        all_bboxes = [e["bbox"] for e in raw_font_size_errors]
        sizes = [e["font_size"] for e in raw_font_size_errors]
        min_s, max_s = min(sizes), max(sizes)
        count = len(raw_font_size_errors)

        processed_bboxes = kampoi_bbox(all_bboxes, ambang_kampoi=3)

        size_str = (
            f"{min_s:.1f}pt"
            if abs(min_s - max_s) < 0.1
            else f"{min_s:.1f}pt - {max_s:.1f}pt"
        )
        msg = (
            f"Saiz font terlalu kecil ({count} lokasi dikesan: {size_str})."
            f" Minimum {min_font_size:.0f}pt."
        )

        for bbox in processed_bboxes:
            errors.append({"msg": msg, "bbox": bbox})

    return errors

# Pemalar pertukaran unit
PT_TO_MM = 25.4 / 72.0
MM_TO_PT = 72.0 / 25.4

def check_margin_atas_violations(
    page, target_margin_mm=30, tolerance_mm=TOLERANCE_MM
):
    errors = []

    # 📌 BETULKAN FORMULA: Tukar (mm) ke (pt) dengan mendarab MM_TO_PT (atau bahagi PT_TO_MM)
    limit_pt = (target_margin_mm - tolerance_mm) * MM_TO_PT
    text_page = page.get_text("dict")

    for block in text_page.get("blocks", []):
        for line in block.get("lines", []):
            x0, y0, x1, y1 = line["bbox"]
            line_text = "".join(
                [s.get("text", "") for s in line.get("spans", [])]
            ).strip()

            # Jika kedudukan Y0 (atas) lebih kecil daripada had limit margin
            if y0 < limit_pt and line_text:
                if not is_page_number(line_text):
                    msg = format_margin_msg(
                        "Teks", line_text, y0, target_margin_mm, "Atas"
                    )
                    errors.append({"bbox": (x0, y0, x1, y1), "msg": msg})

    return errors


def check_margin_bawah_violations(
    page, target_margin_mm=25, tolerance_mm=TOLERANCE_MM
):
    errors = []

    # 📌 BETULKAN FORMULA: Tukar (mm) ke (pt)
    limit_pt = (target_margin_mm - tolerance_mm) * MM_TO_PT
    page_height = page.rect.height
    text_page = page.get_text("dict")

    for block in text_page.get("blocks", []):
        for line in block.get("lines", []):
            x0, y0, x1, y1 = line["bbox"]
            line_text = "".join(
                [s.get("text", "") for s in line.get("spans", [])]
            ).strip()
            dist_from_edge_pt = page_height - y1

            # Jika jarak dari bawah lebih kecil daripada had limit margin
            if dist_from_edge_pt < limit_pt and line_text:
                if not is_page_number(line_text):
                    msg = format_margin_msg(
                        "Teks",
                        line_text,
                        dist_from_edge_pt,
                        target_margin_mm,
                        "Bawah",
                    )
                    errors.append({"bbox": (x0, y0, x1, y1), "msg": msg})

    return errors


def is_page_number(text):
    """Semak jika teks ialah digit, angka Romani, atau format nombor muka surat (cth: '1', 'i', '- 1 -', '1.')"""
    if not text:
        return False

    txt = text.strip().lower()

    # 1. Buang aksara hiasan biasa seperti sengkang (-), kurungan (), titik (.), atau jarak
    cleaned_txt = re.sub(r"[\s\-\(\)\.]", "", txt)

    if not cleaned_txt:
        return False

    # 2. Semak jika angka digit (cth: "1", "- 1 -", "(1)", "1.")
    if cleaned_txt.isdigit():
        return True

    # 3. Semak jika angka Romani (cth: "i", "iv", "xii")
    if re.match(r"^[ivxlcdm]+$", cleaned_txt):
        return True

    return False


def tambah_garis_margin_ke_pdf_page(page):
    """Melukis garisan margin putus-putus secara terus pada fail PDF (vektor)."""
    MM_TO_PT = 72.0 / 25.4
    rect = page.rect
    width_pt, height_pt = rect.width, rect.height

    # Orientasi
    if height_pt >= width_pt:  # Portrait
        top_mm, right_mm, bottom_mm, left_mm = 25.0, 25.0, 25.0, 40.0
    else:  # Landscape
        top_mm, right_mm, bottom_mm, left_mm = 40.0, 25.0, 25.0, 25.0

    margin_rect = fitz.Rect(
        left_mm * MM_TO_PT,
        top_mm * MM_TO_PT,
        width_pt - (right_mm * MM_TO_PT),
        height_pt - (bottom_mm * MM_TO_PT),
    )

    page.draw_rect(
        margin_rect,
        color=(1, 0, 0),
        width=0.8,
        dashes="[3 3] 0",
        overlay=True,
    )


# ==============================================================================
# 2. FUNGSI CALLBACK SESSION STATE (STREAMLIT)
# ==============================================================================

# 📌 DISELARASKAN: Menggunakan nama 'ignored_issues' secara konsisten
if "ignored_issues" not in st.session_state:
    st.session_state.ignored_issues = set()


def reset_semua_abaikan():
    """Memadam semua senarai abaikan dan mereset checkbox UI."""
    st.session_state.ignored_issues.clear()
    for key in list(st.session_state.keys()):
        if key.startswith("bypass_chk_"):
            st.session_state[key] = False


def toggle_ignore_issue(issue_id, chk_key):
    """Fungsi callback tunggal untuk mengendalikan checkbox abaikan."""
    if st.session_state.get(chk_key, False):
        st.session_state.ignored_issues.add(issue_id)
    else:
        st.session_state.ignored_issues.discard(issue_id)


# ==============================================================================
# 3. LOGIK PAPARAN DAN UI STREAMLIT
# ==============================================================================

# Kumpul semua isu daripada fungsi semakan anda
semua_isu = [...]  # Senarai isu daripada semak_keseluruhan_dokumen

# Tambah ID unik untuk setiap isu
# for i, err in enumerate(semua_isu):
#     err["id"] = f"issue_{i}"

# Tapis isu yang BELUM diabaikan
isu_aktif = [
    err
    for idx, err in enumerate(semua_isu)
    if idx not in st.session_state.ignored_issues
]

# Paparkan butang reset HANYA jika ada isu yang sedang diabaikan
if len(st.session_state.ignored_issues) > 0:
    st.button(
        f"🔄 Reset Semula Semua Abaikan ({len(st.session_state.ignored_issues)} diabaikan)",
        on_click=reset_semua_abaikan,
    )

# --- PAPARAN SENARAI ISU (Muka Surat & Expander) ---
for idx, err in enumerate(semua_isu):
    chk_key = f"bypass_chk_{idx}"
    is_checked = idx in st.session_state.ignored_issues


def lukis_garis_margin_gpsta(page, image_pil):
    """
    Melukis garis putus-putus merah samar (Margin Boundary) pada imej halaman PDF.

    Spesifikasi GPPTA 2026:
    - Portrait : Atas, Kanan, Bawah = 25mm | Kiri = 40mm
    - Landscape: Atas = 40mm | Kanan, Bawah, Kiri = 25mm
    """
    # 1. Tukar mm kepada Points (1 mm = 72 / 25.4 points)
    MM_TO_PT = 72.0 / 25.4

    # 2. Dapatkan saiz sebenar halaman PDF (dalam points)
    rect = page.rect
    width_pt = rect.width
    height_pt = rect.height

    # 3. Tentukan orientasi dan tetapkan nilai margin (dalam mm)
    is_portrait = height_pt >= width_pt

    if is_portrait:
        top_mm, right_mm, bottom_mm, left_mm = 25.0, 25.0, 25.0, 40.0
    else:  # Landscape
        top_mm, right_mm, bottom_mm, left_mm = 40.0, 25.0, 25.0, 25.0

    # 4. Tukar margin mm kepada koordinat Points
    top_pt = top_mm * MM_TO_PT
    right_pt = width_pt - (right_mm * MM_TO_PT)
    bottom_pt = height_pt - (bottom_mm * MM_TO_PT)
    left_pt = left_mm * MM_TO_PT

    # 5. Dapatkan nisbah skala imej PIL berbanding saiz asal PDF
    img_w, img_h = image_pil.size
    scale_x = img_w / width_pt
    scale_y = img_h / height_pt

    # 6. Tukar koordinat ke skala Piksel Imej
    x1 = left_pt * scale_x
    y1 = top_pt * scale_y
    x2 = right_pt * scale_x
    y2 = bottom_pt * scale_y

    # 7. Lukis Garis Putus-Putus Merah Samar menggunakan Pillow
    # Warna RGBA: Merah (255, 0, 0) dengan Saluran Alfa/Keperluasan (80-100 untuk kesan samar)
    draw = ImageDraw.Draw(image_pil, "RGBA")

    # Warna merah samar (RGBA)
    merah_samar = (255, 0, 0, 90)
    dash_length = 10  # Panjang garis putus-putus (piksel)
    space_length = 6  # Jarak antara garis (piksel)

    def draw_dashed_line(draw_obj, p1, p2, color, width=2):
        """Fungsi pembantu untuk melukis garis putus-putus"""
        x_start, y_start = p1
        x_end, y_end = p2

        # Hitung jarak
        dx = x_end - x_start
        dy = y_end - y_start
        distance = (dx**2 + dy**2) ** 0.5

        if distance == 0:
            return

        # Vektor unit
        ux = dx / distance
        uy = dy / distance

        curr_dist = 0
        while curr_dist < distance:
            next_dist = min(curr_dist + dash_length, distance)
            start_pos = (x_start + ux * curr_dist, y_start + uy * curr_dist)
            end_pos = (x_start + ux * next_dist, y_start + uy * next_dist)

            draw_obj.line([start_pos, end_pos], fill=color, width=width)
            curr_dist += dash_length + space_length

    # Lukis 4 garisan pembatas kotak margin
    draw_dashed_line(draw, (x1, y1), (x2, y1), merah_samar, width=2)  # Garis Atas
    draw_dashed_line(draw, (x2, y1), (x2, y2), merah_samar, width=2)  # Garis Kanan
    draw_dashed_line(draw, (x2, y2), (x1, y2), merah_samar, width=2)  # Garis Bawah
    draw_dashed_line(draw, (x1, y2), (x1, y1), merah_samar, width=2)  # Garis Kiri

    return image_pil


# =========================================================
# AMBIL TETAPAN SYSTEM DARI SUPABASE
# =========================================================
def ambil_tetapan_sistem():
    try:
        res = supabase.table("tetapan_sistem").select("*").eq("id", 1).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        pass

    return {
        "margin_kiri": 40.0,
        "margin_kanan": 25.0,
        "margin_atas": 25.0,
        "margin_bawah": 25.0,
        "dibenarkan_semak": True,
    }


# =========================================================
# 1. TETAPAN HALAMAN & SESSION STATE (WAJIB DI ATAS)
# =========================================================
st.set_page_config(page_title="e-Semak PTA", page_icon="🔍", layout="wide")

if "user" not in st.session_state:
    st.session_state["user"] = None


# =========================================================
# 2. SAMBUNGAN SUPABASE
# =========================================================
@st.cache_resource
def init_supabase():
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


supabase = init_supabase()
tetapan_semasa = ambil_tetapan_sistem()

# --- PENETAPAN CONSTANT GLOBAL ---
MM_TO_PT = 72 / 25.4

MATH_SYMBOL_FONTS = [
    "cambriamath",
    "symbol",
    "mtextra",
    "math",
    "wingdings",
    "webdings",
    "msmincho",
    "segoeui-symbol",
]

TABLE_PREFIX_REGEX = re.compile(
    r"^\s*(Table|Jadual)\s+\d+(\.\d+)*", re.IGNORECASE
)
FIGURE_PREFIX_REGEX = re.compile(
    r"^\s*(Figure|Rajah)\s+\d+(\.\d+)*", re.IGNORECASE
)
IN_TEXT_CITATION_REGEX = re.compile(
    r"^\s*(Figure|Rajah|Table|Jadual)\s+\d+(\.\d+)*\.\s", re.IGNORECASE
)
DOT_LEADER_REGEX = re.compile(r"\.{3,}\s*\d+|\b\d+\s*$", re.IGNORECASE)
VERB_KEYWORDS_REGEX = re.compile(
    r"\b("
    r"shows?|showing|showed|presents?|presenting|presented|"
    r"summarizes?|summarised|summarising|summarize|summarise|summary|"
    r"illustrates?|illustrating|illustrated|depicts?|depicting|depicted|"
    r"lists?|listing|listed|compares?|comparing|compared|"
    r"indicates?|indicating|indicated|displays?|displaying|displayed|"
    r"describes?|describing|described|provides?|providing|provided|"
    r"menunjukkan|menyenaraikan|mencatatkan|memaparkan|menggambarkan|merumuskan|membandingkan|menyediakan|memberikan"
    r")\b",
    re.IGNORECASE,
)

import re


def is_valid_figure_caption(full_line_text):
    line = full_line_text.strip()

    # 1. Harus diawali dengan Rajah / Figure
    if not FIGURE_PREFIX_REGEX.match(line):
        return False

    # 2. Filter kata kerja paragraf (menunjukkan, memaparkan, dll.)
    if VERB_KEYWORDS_REGEX.search(line):
        return False

    # 3. Filter jika teks terlalu panjang (> 12 kata)
    if len(line.split()) > 12:
        return False

    # 4. CIRI UTAMA JUDUL ASLI: Memiliki pemisah ':' atau '-' setelah nomor
    # Contoh valid: "RAJAH 3 : MAKLUMAT RESPONDEN"
    # Contoh tidak valid: "Rajah 3 menunjukkan..." (tidak ada titik dua)
    has_separator = bool(
        re.search(
            r"^\s*(Figure|Rajah)\s+\d+(\.\d+)*\s*[:\-–]",
            line,
            re.IGNORECASE,
        )
    )

    if not has_separator:
        # Jika tidak ada titik dua/strip dan jumlah kata > 5, dipastikan ini paragraf biasa
        if len(line.split()) > 5:
            return False

    return True


def is_roman_numeral(val_str):
    """Fungsi menyemak secara dinamik sama ada perkataan ialah nombor Roman valid"""
    val_str = val_str.lower().strip()
    if not val_str:
        return False
    roman_pattern = r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$"
    return bool(re.match(roman_pattern, val_str, re.IGNORECASE))


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def label_peranan(kod):
    pemetaan = {
        "pengguna": "Pengguna",
        "admin": "Admin",
        "super_admin": "Super Admin",
    }
    return pemetaan.get(str(kod).strip().lower(), str(kod).title())


def label_status(kod):
    pemetaan = {"aktif": "Aktif", "tidak_aktif": "Tidak Aktif"}
    return pemetaan.get(str(kod).strip().lower(), str(kod).title())


# =========================================================
# 3. GOOGLE WEBHOOK LOGGING
# =========================================================
GOOGLE_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbzODbqg8fx4wduxDmbjhFdzzj_k6xkIsb0oMo9FR10UKkXs0tVmt6HyIakaLmmaSORc/exec"


def _proses_hantar_background(data_log):
    try:
        response = requests.post(
            GOOGLE_WEBHOOK_URL,
            json=data_log,
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        print(f"[Log Google Sheets] Status Penghantaran: {response.status_code}")
    except Exception as e:
        print(f"[Ralat Webhook Log]: {e}")


def hantar_log_penggunaan(
    environment,
    filename,
    file_size_mb,
    processing_time_sec,
    total_pages,
    total_errors,
):
    tz_my = timezone(timedelta(hours=8))
    data_log = {
        "timestamp": datetime.now(tz_my).strftime("%Y-%m-%d %H:%M:%S"),
        "environment": environment,
        "filename": filename,
        "file_size_mb": file_size_mb,
        "processing_time_sec": processing_time_sec,
        "total_pages": total_pages,
        "total_errors": total_errors,
    }
    thread = threading.Thread(
        target=_proses_hantar_background, args=(data_log,)
    )
    thread.start()

# BAHAGIAN 6:
def paparkan_footer_maklumat():
    st.markdown(
        """
        <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px 20px; margin-top: 30px; text-align: center;">
            <p style="margin: 0 0 4px 0; font-weight: 700; color: #1e293b; font-size: 0.85rem;">
                © 2026 Ts. Muhammad Taufik Ramli / KV Nibong Tebal. Hak Cipta Terpelihara.
            </p>
            <p style="margin: 0 0 4px 0; color: #64748b; font-size: 0.8rem;">
                📍 Program Teknologi Elektronik, Kolej Vokasional Nibong Tebal, 14300 Nibong Tebal, Pulau Pinang
            </p>
            <p style="margin: 0; color: #64748b; font-size: 0.8rem;">
                ✉️ Hubungi Sokongan: <a href="mailto:mtaufikramli@gmail.com" style="color: #2563eb; text-decoration: none; font-weight: 600;">mtaufikramli@gmail.com</a> | 📱 Tel/WhatsApp: <span style="font-weight: 600; color: #334155;">+60 13-222 4610</span>
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# 4. LOGIK SUPABASE (BAKI & LESEN)
# =========================================================
def semak_dan_kemaskini_baki(user):
    def safe_update(data_dict):
        """Fungsi pembantu untuk cuba semula (retry) jika sambungan Supabase terputus"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return (
                    supabase.table("pengguna")
                    .update(data_dict)
                    .eq("id", user["id"])
                    .execute()
                )
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(0.5)  # Tunggu 0.5 saat sebelum cuba semula
                else:
                    print(f"Ralat Supabase selepas 3 kali percubaan: {e}")

    tarikh_today = str(datetime.now().date())
    tarikh_last = str(user.get("tarikh_terakhir", ""))

    had_harian = user.get("had_harian", 5)
    baki = user.get("baki_semakan", had_harian)

    if tarikh_last != tarikh_today:
        baki = had_harian
        safe_update(
            {"baki_semakan": had_harian, "tarikh_terakhir": tarikh_today}
        )

    status_lesen = str(user.get("status_lesen", "aktif")).strip().lower()
    tarikh_tamat = user.get("tarikh_tamat_lesen")

    # Auto tukar ke "tidak_aktif" jika tarikh tamat lesen sudah berlalu
    if (
        tarikh_tamat
        and status_lesen == "aktif"
        and str(tarikh_tamat) < tarikh_today
    ):
        status_lesen = "tidak_aktif"
        safe_update({"status_lesen": "tidak_aktif"})

    return baki, status_lesen


def tolak_baki_semakan(user_id, baki_semasa):
    baki_baru = max(0, baki_semasa - 1)
    supabase.table("pengguna").update({"baki_semakan": baki_baru}).eq(
        "id", user_id
    ).execute()
    return baki_baru


# =========================================================
# 5. SKRIN LOG MASUK
# =========================================================
if st.session_state["user"] is None:
    _, col_center, _ = st.columns([1, 2, 1])

    with col_center:
        st.markdown(
            """
            <style>
            .login-header {
                background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 50%, #2563eb 100%);
                padding: 30px 20px;
                border-radius: 16px;
                text-align: center;
                color: white;
                box-shadow: 0 10px 25px -5px rgba(30, 58, 138, 0.3);
                margin-bottom: 20px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            .login-header h2 {
                font-size: 28px;
                font-weight: 700;
                margin: 0;
                letter-spacing: -0.5px;
            }
            .login-header p {
                font-size: 14px;
                color: #93c5fd;
                margin-top: 6px;
                margin-bottom: 0;
            }
            </style>

            <div class="login-header">
                <h2>🔒 e-Semak PTA</h2>
                <p>Sistem Semakan Format Laporan Projek Tahun Akhir</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        tab1, tab2 = st.tabs(["🔑 Log Masuk", "📝 Daftar Akaun Baharu"])

        with tab1:
            st.markdown("<br>", unsafe_allow_html=True)
            with st.form("form_login"):
                username = st.text_input(
                    "Nama Pengguna (Username)",
                    placeholder="Masukkan username anda...",
                )
                password = st.text_input(
                    "Kata Laluan",
                    type="password",
                    placeholder="Masukkan kata laluan...",
                )
                submit_login = st.form_submit_button(
                    "🚀 Log Masuk Akses", type="primary", width="stretch"
                )

            if submit_login:
                username_bersih = username.strip()
                if username_bersih and password:
                    res = (
                        supabase.table("pengguna")
                        .select("*")
                        .ilike("username", username_bersih)
                        .execute()
                    )
                    if res.data:
                        user_data = res.data[0]
                        if user_data["password_hash"] == hash_password(
                            password
                        ):
                            # Simpan keseluruhan record (termasuk perlu_tukar_pass)
                            st.session_state["user"] = user_data
                            st.success("Log masuk berjaya!")
                            st.rerun()
                        else:
                            st.error("🔑 Kata laluan salah!")
                    else:
                        st.error("👤 Pengguna tidak dijumpai!")
                else:
                    st.warning("⚠️ Sila isi semua ruang.")

        with tab2:
            st.markdown("<br>", unsafe_allow_html=True)
            with st.form("form_register"):
                new_user = st.text_input(
                    "Nama Pengguna (Username):",
                    placeholder="Contoh: ahmad_pta (tanpa ruang kosong)",
                )
                new_pass = st.text_input(
                    "Kata Laluan Baharu:",
                    type="password",
                    placeholder="Masukkan kata laluan (min 6 aksara)...",
                )
                confirm_pass = st.text_input(
                    "Sahkan Kata Laluan Baharu:",
                    type="password",
                    placeholder="Taip semula kata laluan anda...",
                )

                submit_register = st.form_submit_button(
                    "✨ Daftar Akaun Baharu", type="primary", width="stretch"
                )

            if submit_register:
                new_user_bersih = new_user.strip()

                # Semakan Validasi Pendaftaran
                if not new_user_bersih or not new_pass or not confirm_pass:
                    st.warning("⚠️ Sila isi semua maklumat yang diperlukan.")
                elif " " in new_user_bersih:
                    st.warning(
                        "⚠️ Nama pengguna tidak boleh mengandungi ruang kosong."
                    )
                elif len(new_pass) < 6:
                    st.warning(
                        "⚠️ Kata laluan mestilah mengandungi"
                        " sekurang-kurangnya 6 aksara."
                    )
                elif new_pass != confirm_pass:
                    st.error(
                        "❌ Kata laluan dan pengesahan kata laluan tidak padan!"
                    )
                else:
                    # Semak kewujudan pengguna di Supabase
                    cek = (
                        supabase.table("pengguna")
                        .select("*")
                        .ilike("username", new_user_bersih)
                        .execute()
                    )
                    if cek.data:
                        st.error(
                            "⚠️ Nama pengguna ini telah berdaftar dalam sistem."
                        )
                    else:
                        try:
                            # KIRA TARIKH TAMAT LESEN: 6 BULAN (180 HARI) DARI TARIKH HARI INI
                            tarikh_tamat_default = (
                                datetime.now().date() + timedelta(days=180)
                            )

                            supabase.table("pengguna").insert({
                                "username": new_user_bersih,
                                "password_hash": hash_password(new_pass),
                                "baki_semakan": 5,
                                "tarikh_terakhir": str(
                                    datetime.now().date()
                                ),
                                "tarikh_tamat_lesen": str(
                                    tarikh_tamat_default
                                ),  # <--- HANTAR TARIKH 6 BULAN
                                "status_lesen": "aktif",
                                "had_harian": 5,
                                "perlu_tukar_pass": False,
                            }).execute()

                            st.success(
                                "🎉 Akaun berjaya didaftarkan! Sila log masuk"
                                " menggunakan tab di sebelah."
                            )
                        except Exception as e:
                            st.error(f"❌ Gagal mendaftar akaun: {str(e)}")

        st.markdown(
            f"""
            <div style="text-align: center; margin-top: 24px; color: #64748b; font-size: 0.8rem;">
                Hak Cipta © 2026 KV Nibong Tebal • Versi {APP_VERSION}
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.stop()

    # =========================================================
    # KAWALAN KESELAMATAN: PAKSA TUKAR KATA LALUAN RESET
    # =========================================================
    user_data = st.session_state.get("user")

    if user_data and user_data.get("perlu_tukar_pass"):
        _, col_center, _ = st.columns([1, 2, 1])

        with col_center:
            st.warning(
                "⚠️ **Akses Terhad:** Kata laluan anda telah di-reset oleh"
                " Pentadbir. Sila tetapkan kata laluan baharu untuk meneruskan."
            )

            with st.form("form_force_change_pass"):
                pass1 = st.text_input(
                    "Kata Laluan Baharu:",
                    type="password",
                    placeholder="Masukkan kata laluan baharu...",
                )
                pass2 = st.text_input(
                    "Sahkan Kata Laluan Baharu:",
                    type="password",
                    placeholder="Taip semula kata laluan baharu...",
                )

                simpan_pass = st.form_submit_button(
                    "🔒 Simpan Kata Laluan Baharu & Teruskan",
                    type="primary",
                    width="stretch",
                )

                if simpan_pass:
                    if not pass1 or not pass2:
                        st.error("⚠️ Sila isi kedua-dua ruangan kata laluan.")
                    elif pass1 != pass2:
                        st.error("❌ Kata laluan tidak padan!")
                    elif pass1 == "123456":
                        st.warning(
                            "⚠️ Sila gunakan kata laluan lain selain kata"
                            " laluan default '123456'."
                        )
                    elif len(pass1) < 6:
                        st.warning(
                            "⚠️ Kata laluan mestilah sekurang-kurangnya 6"
                            " aksara."
                        )
                    else:
                        try:
                            new_hash = hash_password(pass1)

                            # Kemaskini database & nyahaktifkan perlu_tukar_pass
                            supabase.table("pengguna").update({
                                "password_hash": new_hash,
                                "perlu_tukar_pass": False,
                            }).eq(
                                "username", user_data["username"]
                            ).execute()

                            # Kemaskini session tempatan
                            st.session_state["user"]["perlu_tukar_pass"] = (
                                False
                            )

                            st.success(
                                "🎉 Kata laluan berjaya dikemaskini! Memuat"
                                " naik sistem..."
                            )
                            st.rerun()
                        except Exception as e:
                            st.error(
                                f"❌ Gagal kemaskini kata laluan: {str(e)}"
                            )

        # Menyekat akses ke sidebar/dashboard selagi belum tukar password
        st.stop()

# =========================================================
# SEMAKAN WAJIB TUKAR KATA LALUAN (FORCE PASSWORD RESET)
# =========================================================
current_u = st.session_state.get("user")

if current_u and current_u.get("perlu_tukar_pass") is True:
    st.markdown(
        """
        <style>
        .force-card {
            background: #ffffff;
            padding: 30px;
            border-radius: 16px;
            box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1);
            border: 1px solid #e2e8f0;
            margin-top: 20px;
        }
        .force-header {
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            padding: 24px;
            border-radius: 12px;
            color: white;
            text-align: center;
            margin-bottom: 20px;
        }
        .force-header h2 {
            color: white !important;
            margin: 0 !important;
            font-size: 1.6rem;
            font-weight: 700;
        }
        .force-header p {
            color: #e2e8f0 !important;
            margin: 5px 0 0 0 !important;
            font-size: 0.9rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    _, col_box, _ = st.columns([1, 2, 1])

    with col_box:
        # Header Utama Pengenalan Sistem
        st.markdown(
            """
            <div class="force-header">
                <h2>🔒 Penukaran Kata Laluan Wajib</h2>
                <p>Sistem e-Semak PTA</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Mesej Amaran Keselamatan
        st.warning(
            "🔒 **Keselamatan Akaun:** Kata laluan anda telah di-reset oleh"
            " Pentadbir atau akaun baru dicipta. Sila cipta kata laluan baharu"
            " untuk meneruskan."
        )

        # Borang Input Kata Laluan Baharu
        with st.form("form_force_reset"):
            p1 = st.text_input(
                "Kata Laluan Baharu:",
                type="password",
                placeholder="Minimum 6 aksara",
            )
            p2 = st.text_input(
                "Sahkan Kata Laluan Baharu:",
                type="password",
                placeholder="Ulang kata laluan baharu",
            )
            btn_submit = st.form_submit_button(
                "✨ Simpan Kata Laluan & Masuk",
                type="primary",
                width="stretch",
            )

            if btn_submit:
                if not p1 or not p2:
                    st.error("⚠️ Sila isi kedua-dua ruangan.")
                elif p1 != p2:
                    st.error("❌ Kata laluan tidak padan!")
                elif p1 == "123456":
                    st.warning("⚠️ Sila gunakan kata laluan selain '123456'.")
                elif len(p1) < 6:
                    st.warning("⚠️ Kata laluan sekurang-kurangnya 6 aksara.")
                else:
                    try:
                        new_h = hash_password(p1)

                        # Kemaskini database Supabase
                        supabase.table("pengguna").update({
                            "password_hash": new_h,
                            "perlu_tukar_pass": False,
                        }).eq("username", current_u["username"]).execute()

                        # Kemaskini session tempatan
                        st.session_state["user"]["perlu_tukar_pass"] = False

                        st.success(
                            "🎉 Kata laluan berjaya dikemaskini! Memuat"
                            " semula..."
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Ralat kemaskini: {str(e)}")

    # SEKAT PAPARAN LAIN SEHINGGA SELESAI TUKAR PASS
    st.stop()

# =========================================================
# 6. SEMAKAN KESELAMATAN & KAWALAN AKSES LESEN
# =========================================================
current_user = st.session_state["user"]
baki_semasa, status_lesen = semak_dan_kemaskini_baki(current_user)
user_role = str(current_user.get("peranan", "pengguna")).strip().lower()

if status_lesen != "aktif":
    st.error("⏳ **LESEN PERISIAN TIDAK AKTIF ATAU TELAH TAMAT TEMPOH**")
    mesej_wa = (
        "Assalamualaikum/Salam Sejahtera Ts. Muhammad Taufik,\n\n"
        f"Saya (Username: {current_user['username']}) ingin memohon"
        " pembaharuan/pengaktifan semula lesen bagi perisian *e-Semak PTA*.\n\n"
        "Terima kasih."
    )
    link_whatsapp = (
        f"https://wa.me/60132224610?text={urllib.parse.quote(mesej_wa)}"
    )
    st.info(
        "Akaun anda telah digantung atau tamat tempoh lesen.\n\n"
        "### 📞 Maklumat Perhubungan Pentadbir System:\n"
        "* **Pentadbir:** Ts. Muhammad Taufik Ramli\n"
        "* **Institusi:** Program Teknologi Elektronik, KV Nibong Tebal\n"
        "* **E-mel:** mtaufikramli@gmail.com / g-25076822@moe-dl.edu.my\n"
        "* **Tel:** +60 13-222 4610\n"
        f"* **WhatsApp Direct:** [💬 Klik Sini Untuk WhatsApp Pentadbir]({link_whatsapp})\n\n"
        "Sila hubungi pihak pentadbir di atas untuk pembaharuan lesen perisian."
    )
    st.stop()

if baki_semasa <= 0 and user_role not in ["admin", "super_admin"]:
    st.error("🛑 **HAD SEMAKAN HARIAN TERCAPAI**")
    st.info(
        "Anda telah mencapai had semakan harian yang ditetapkan untuk akaun"
        " anda. Sila cuba lagi esok."
    )
    st.stop()

# =========================================================
# 7. SIDEBAR (PEMILIHAN NAVIGASI & TETAPAN)
# =========================================================
baki = st.session_state["user"].get("baki_semakan", 0)
had = st.session_state["user"].get("had_harian", 5)

# Inisialisasi Tetapan Default daripada Supabase
default_left = float(tetapan_semasa.get("margin_kiri", 40.0))
default_right = float(tetapan_semasa.get("margin_kanan", 25.0))
default_top = float(tetapan_semasa.get("margin_atas", 25.0))
default_bottom = float(tetapan_semasa.get("margin_bawah", 25.0))

# Pembolehubah kawalan laluan (fallback)
margin_left_mm = default_left
margin_right_mm = default_right
margin_top_mm = default_top
margin_bottom_mm = default_bottom
allowed_fonts = ["Arial", "Arial-BoldMT", "ArialMT"]
semak_caption = True
abaikan_teks_dalam_gambar = True
abaikan_appendix = True
abaikan_pagenum_appendix = True

with st.sidebar:
    # =========================================================
    # 1. PAPARAN VERSI SISTEM & BUTANG LOG KEMASKINI
    # =========================================================
    col_ver, col_log = st.columns([3, 1])

    with col_ver:
        st.markdown(
            f"""
            <div style="
                background: #1e293b; 
                padding: 6px 10px; 
                border-radius: 8px; 
                border: 1px solid #334155; 
                color: #38bdf8; 
                font-size: 0.82rem; 
                font-weight: 700; 
                height: 38px;
                display: flex;
                align-items: center;
                box-shadow: 0 2px 6px rgba(0,0,0,0.2);
            ">
                📌 Versi: <span style="color: #4ade80; margin-left: 4px;">v{APP_VERSION}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col_log:
        if st.button(
            "ℹ️", help="Lihat Log Kemaskini", use_container_width=True
        ):
            paparkan_log_kemaskini()

    # =========================================================
    # 2. PAUTAN PUSAT BANTUAN DI SIDEBAR
    # =========================================================
    st.markdown("### 📖 Pusat Bantuan & Panduan")

    url_video_tutorial = "https://drive.google.com/file/d/1IdIwZEQ4xNZ_-iSwcrC6XZSCxQPmgn8R/view?usp=sharing"
    url_pdf_manual = "https://drive.google.com/file/d/17Wt0OdsUj9UILwZbqw6_ghIT0Wg-H9-r/view?usp=sharing"

    st.link_button(
        "📹 Tonton Video Tutorial (3 Min)",
        url_video_tutorial,
        width="stretch",
    )

    st.link_button(
        "📄 Muat Turun Manual Pengguna (PDF)",
        url_pdf_manual,
        width="stretch",
    )

    # Format tarikh tamat lesen ke format DD/MM/YYYY
    tarikh_tamat_raw = current_user.get("tarikh_tamat_lesen")
    if tarikh_tamat_raw:
        try:
            tarikh_tamat_fmt = datetime.strptime(
                str(tarikh_tamat_raw), "%Y-%m-%d"
            ).strftime("%d/%m/%Y")
        except ValueError:
            tarikh_tamat_fmt = str(tarikh_tamat_raw)
    else:
        tarikh_tamat_fmt = "-"

    # 1. Logic paparan kuota & lesen eksklusif
    if user_role in ["admin", "super_admin"]:
        baki_text = "Tanpa Had (Unlimited)"
        lesen_text = "Sepanjang Masa (Lifetime)"
    else:
        baki_text = f"{baki}/{had} semakan"
        lesen_text = tarikh_tamat_fmt

    # 2. Paparan Kad Profil Pengguna
    st.markdown(
        f"""
        <div style="background-color: #1e293b; padding: 14px 16px; border-radius: 12px; border: 1px solid #334155; margin-bottom: 16px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">
            <div style="font-size: 0.75rem; color: #94a3b8; font-weight: 700; letter-spacing: 0.5px;">AKAUN PENGGUNA</div>
            <div style="font-size: 0.95rem; color: #38bdf8; font-weight: 700; margin-top: 6px; display: flex; align-items: center;">
                👤 {current_user['username']} <span style="font-size: 0.7rem; background-color: #0284c7; color: white; padding: 2px 6px; border-radius: 4px; margin-left: 6px; font-weight: 600;">{label_peranan(user_role)}</span>
            </div>
            <div style="font-size: 0.82rem; color: #4ade80; font-weight: 600; margin-top: 8px;">
                📊 Baki Hari Ini: <span style="color: #4ade80;">{baki_text}</span>
            </div>
            <div style="font-size: 0.82rem; color: #fde047; font-weight: 600; margin-top: 6px;">
                📅 Lesen Tamat: <span style="color: #fde047;">{lesen_text}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Letakkan style CSS di luar supaya SEMUA pengguna (Admin & Pengguna Biasa) dapat guna
    st.markdown(
        """
        <style>
        .sidebar-header-slate {
            background: linear-gradient(135deg, #0f172a 0%, #334155 100%);
            color: white !important;
            padding: 8px 14px;
            border-radius: 8px;
            font-weight: 700;
            font-size: 15px;
            margin-bottom: 12px;
        }
        .sidebar-header-blue {
            background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 100%);
            color: white !important;
            padding: 8px 14px;
            border-radius: 8px;
            font-weight: 700;
            font-size: 15px;
            margin-top: 15px;
            margin-bottom: 12px;
        }
        </style>
    """,
        unsafe_allow_html=True,
    )

    if user_role in ["admin", "super_admin"]:
        st.markdown("---")
        # Tajuk Panel Pentadbir (Warna Dark Slate)
        st.markdown(
            '<div class="sidebar-header-slate">🛠️ Panel Pentadbir</div>',
            unsafe_allow_html=True,
        )
        mod_halaman = st.selectbox(
            "Pilih Paparan Halaman:",
            ["📄 Semakan Laporan PTA", "📊 Dashboard Admin"]
        )
    else:
        mod_halaman = "📄 Semakan Laporan PTA"

    if st.button("🚪 Log Keluar", type="secondary", width="stretch"):
        st.session_state["user"] = None
        st.rerun()

    st.divider()

    # TETAPAN HANYA DITUNJUKKAN JIKA DI MOD SEMAKAN PTA
    if mod_halaman == "📄 Semakan Laporan PTA":
        # Tajuk Tetapan Templat (Warna Royal Blue Berkilat)
        st.markdown(
            '<div class="sidebar-header-blue">⚙️ Tetapan Templat Laporan PTA</div>',
            unsafe_allow_html=True,
        )

        preset = st.selectbox(
            "Pilih Templat Garis Panduan",
            ["GPPTA KV (Edisi Ketiga 2026)", "Custom (Manual)"],
        )

        if preset == "GPPTA KV (Edisi Ketiga 2026)":
            # 🟢 DITAMBAH: Aptos dimasukkan ke dalam default_fonts supaya automatik terpilih
            default_fonts = [
                "Arial",
                "Arial-BoldMT",
                "ArialMT",
                "Arial-ItalicMT",
                "Arial-BoldItalicMT",
                "Aptos",
                "Aptos-Bold",
                "Aptos-Italic",
                "Aptos-BoldItalic",
            ]
        else:
            default_fonts = ["Arial", "Times New Roman"]

        # Ambil nilai input langsung dari user
        margin_left_mm = st.number_input(
            "Margin Kiri (mm)",
            min_value=10.0,
            max_value=60.0,
            value=default_left,
            step=1.0,
        )
        margin_right_mm = st.number_input(
            "Margin Kanan (mm)",
            min_value=10.0,
            max_value=60.0,
            value=default_right,
            step=1.0,
        )
        margin_top_mm = st.number_input(
            "Margin Atas (mm)",
            min_value=10.0,
            max_value=60.0,
            value=default_top,
            step=1.0,
        )
        margin_bottom_mm = st.number_input(
            "Margin Bawah (mm)",
            min_value=10.0,
            max_value=60.0,
            value=default_bottom,
            step=1.0,
        )

        # 🟢 DITAMBAH: Aptos dimasukkan ke dalam pilihan senarai font yang wujud
        available_font_options = [
            "Arial",
            "Arial-BoldMT",
            "ArialMT",
            "Arial-ItalicMT",
            "Arial-BoldItalicMT",
            "Aptos",
            "Aptos-Bold",
            "Aptos-Italic",
            "Aptos-BoldItalic",
            "Times New Roman",
            "TimesNewRoman",
            "Calibri",
            "Garamond",
        ]

        allowed_fonts = st.multiselect(
            "Jenis Font Dibenarkan",
            options=available_font_options,
            default=default_fonts,
        )
        semak_caption = st.checkbox(
            "Aktifkan Semakan Format Tajuk Jadual & Rajah", value=True
        )
        abaikan_teks_dalam_gambar = st.checkbox(
            "Abaikan Teks Dalam Gambar / Rajah", value=True
        )
        abaikan_appendix = st.checkbox(
            "Abaikan Semakan Font pada Lampiran (Appendix)", value=True
        )
        abaikan_pagenum_appendix = st.checkbox(
            "Abaikan Semakan No. M/S di Lampiran (Appendices)", value=True
        )
        # --- FUNGSI SPACING DI-KIV / DI-HIDE SEMENTARA ---
        aktifkan_spacing = st.checkbox(
            "Aktifkan Semakan Jarak Baris (Line Spacing)", value=True
        )
        check_line_spacing = (
            False  # Set default False supaya logik kod lain tidak error
        )
        
# =========================================================
# 8. KAWALAN PAPARAN HALAMAN UTAMA (ADMIN VS SEMAKAN)
# =========================================================

if mod_halaman == "📊 Dashboard Admin":
    # Banner Header Dashboard Admin (Biru Berkilat)
    # Banner Header Dashboard Admin (Sekali CSS)
    st.markdown(
        f"""
        <style>
        .header-card-dash {{
            background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 50%, #2563eb 100%);
            padding: 24px 30px;
            border-radius: 16px;
            color: white;
            box-shadow: 0 10px 25px -5px rgba(30, 58, 138, 0.3);
            margin-bottom: 25px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }}
        .header-title-dash {{
            font-size: 26px;
            font-weight: 700;
            margin: 0;
            color: #ffffff;
            letter-spacing: -0.5px;
        }}
        .header-subtitle-dash {{
            font-size: 14px;
            color: #93c5fd;
            margin-top: 6px;
            font-weight: 500;
        }}
        </style>
        
        <div class="header-card-dash">
            <div class="header-title-dash">📊 Dashboard Pentadbir Sistem</div>
            <div class="header-subtitle-dash">Pentadbir Semasa: <b>{current_user['username']}</b> | Peranan: <b>{label_peranan(user_role)}</b></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        res = supabase.table("pengguna").select("*").execute()
        semua_pengguna = res.data if res.data else []
    except Exception as e:
        st.error(f"Gagal mengambil data pengguna: {e}")
        semua_pengguna = []

    # 1. Kira nilai statistik
    total_pengguna = len(semua_pengguna)
    akaun_aktif = sum(
        1
        for u in semua_pengguna
        if str(u.get("status_lesen", "")).strip().lower() == "aktif"
    )
    total_baki = sum(u.get("baki_semakan", 0) for u in semua_pengguna)

    # 2. Paparkan Kad Statistik Moden
    st.markdown(
        f"""
        <style>
        .metric-container {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 25px;
        }}
        .metric-card {{
            background: #ffffff;
            padding: 20px;
            border-radius: 12px;
            border: 1px solid #e2e8f0;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
        }}
        .metric-card-blue {{ border-left: 5px solid #2563eb; }}
        .metric-card-green {{ border-left: 5px solid #10b981; }}
        .metric-card-purple {{ border-left: 5px solid #8b5cf6; }}
        
        .metric-title {{
            font-size: 0.85rem;
            font-weight: 600;
            color: #64748b;
            margin-bottom: 6px;
        }}
        .metric-value {{
            font-size: 1.8rem;
            font-weight: 800;
            color: #0f172a;
        }}
        </style>

        <div class="metric-container">
            <div class="metric-card metric-card-blue">
                <div class="metric-title">👥 JUMLAH PENGGUNA</div>
                <div class="metric-value">{total_pengguna}</div>
            </div>
            <div class="metric-card metric-card-green">
                <div class="metric-title">🟢 AKAUN AKTIF</div>
                <div class="metric-value">{akaun_aktif}</div>
            </div>
            <div class="metric-card metric-card-purple">
                <div class="metric-title">📊 TOTAL BAKI</div>
                <div class="metric-value">{total_baki} <span style="font-size: 0.9rem; font-weight:500; color:#64748b;">semakan</span></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.divider()

    tab_senarai, tab_kemaskini, tab_reset, tab_tambah, tab_tetapan = st.tabs([
        "📋 Senarai Pengguna",
        "✏️ Kemaskini Kuota / Peranan",
        "🔑 Reset Kata Laluan",
        "➕ Tambah Pengguna Baharu",
        "⚙️ Tetapan Sistem (Superadmin)",
    ])

    # TAB 1: SENARAI PENGGUNA
    with tab_senarai:
        if semua_pengguna:
            data_jadual = []
            for u in semua_pengguna:
                # Ambil nilai peranan dan tukar ke huruf kecil sepenuhnya untuk semakan
                raw_role = str(u.get("peranan", "")).lower().strip()

                # Semak jika ada perkataan 'admin' atau 'super_admin' (termasuk 'Admin', 'Super Admin')
                if raw_role in ["admin", "super admin", "super_admin"]:
                    baki_val = "Tanpa Had"
                    had_val = "Unlimited"
                    tarikh_formatted = "Sepanjang Masa (Lifetime)"
                else:
                    baki_val = u.get("baki_semakan")
                    had_val = u.get("had_harian")

                    # Format tarikh ke DD/MM/YYYY jika wujud untuk pengguna biasa
                    tarikh_raw = u.get("tarikh_tamat_lesen")
                    if tarikh_raw:
                        try:
                            tarikh_formatted = datetime.strptime(
                                str(tarikh_raw), "%Y-%m-%d"
                            ).strftime("%d/%m/%Y")
                        except ValueError:
                            tarikh_formatted = str(tarikh_raw)
                    else:
                        tarikh_formatted = "-"

                data_jadual.append({
                    "ID": u.get("id"),
                    "Username": u.get("username"),
                    "Peranan": label_peranan(u.get("peranan")),
                    "Baki Semakan": baki_val,
                    "Had Harian": had_val,
                    "Status": label_status(u.get("status_lesen")),
                    "Tarikh Tamat Lesen": tarikh_formatted,
                })

            st.dataframe(data_jadual, width="stretch", hide_index=True)
        else:
            st.info("Tiada data pengguna dijumpai.")

    # TAB 2: KEMASKINI
    with tab_kemaskini:
        pilihan_user = st.selectbox("Pilih Pengguna:", [u["username"] for u in semua_pengguna])
        user_target = next((u for u in semua_pengguna if u["username"] == pilihan_user), None)

        if user_target:
            with st.form("form_update_user"):
                col_u1, col_u2 = st.columns(2)
                with col_u1:
                    new_baki = st.number_input("Baki Semakan Baharu", min_value=0, value=int(user_target.get("baki_semakan", 0)))
                    new_had = st.number_input("Had Harian Baharu", min_value=0, value=int(user_target.get("had_harian", 5)))
                
                with col_u2:
                    pilihan_p = ["pengguna", "admin", "super_admin"] if user_role == "super_admin" else ["pengguna", "admin"]
                    peranan_semasa = str(user_target.get("peranan", "pengguna")).strip().lower()
                    idx_p = pilihan_p.index(peranan_semasa) if peranan_semasa in pilihan_p else 0
                    new_role = st.selectbox("Peranan", pilihan_p, index=idx_p, format_func=label_peranan)
                    
                    pilihan_s = ["aktif", "tidak_aktif"]
                    status_semasa = str(user_target.get("status_lesen", "aktif")).strip().lower()
                    idx_s = pilihan_s.index(status_semasa) if status_semasa in pilihan_s else 0
                    new_status = st.selectbox("Status Akaun", pilihan_s, index=idx_s, format_func=label_status)

                # --- TAMBAHAN: INPUT TARIKH TAMAT LESEN ---
                tarikh_asal = user_target.get("tarikh_tamat_lesen")
                try:
                    tarikh_default = datetime.strptime(str(tarikh_asal), "%Y-%m-%d").date() if tarikh_asal else datetime.now().date()
                except ValueError:
                    tarikh_default = datetime.now().date()

                new_tarikh_tamat = st.date_input(
                    "📅 Tarikh Tamat Lesen",
                    value=tarikh_default,
                    format="YYYY-MM-DD"
                )

                if st.form_submit_button("💾 Simpan Perubahan", type="primary", width="stretch"):
                    supabase.table("pengguna").update({
                        "baki_semakan": new_baki,
                        "had_harian": new_had,
                        "peranan": new_role,
                        "status_lesen": new_status,
                        "tarikh_tamat_lesen": str(new_tarikh_tamat)  # <--- Simpan tarikh baharu
                    }).eq("id", user_target["id"]).execute()
                    
                    st.success(f"✅ Data {pilihan_user} dan tarikh lesen berjaya dikemaskini!")
                    time.sleep(1)
                    st.rerun()
            
            st.divider()
            st.markdown("#### 🗑️ Padam Pengguna")

            if user_target["username"] == current_user["username"]:
                st.info("ℹ️ Anda tidak boleh memadam akaun sendiri yang sedang log masuk.")
            else:
                confirm_padam = st.checkbox(
                    f"Saya pasti mahu memadam akaun '{pilihan_user}' secara kekal. Tindakan ini tidak boleh diundur.",
                    key=f"confirm_padam_{user_target['id']}"
                )
                if st.button(
                    "🗑️ Padam Pengguna Ini",
                    type="secondary",
                    width="stretch",
                    disabled=not confirm_padam
                ):
                    supabase.table("pengguna").delete().eq("id", user_target["id"]).execute()
                    st.success(f"✅ Akaun '{pilihan_user}' telah dipadam.")
                    time.sleep(1)
                    st.rerun()

    # TAB 3: RESET PASSWORD
    with tab_reset:
        st.markdown("### 🔑 Reset Kata Laluan ke Default")
        st.caption(
            "Kata laluan akan di-reset secara automatik ke **123456**. Pengguna"
            " dipaksa menukar kata laluan semasa log masuk seterusnya."
        )

        senarai_user = [u["username"] for u in semua_pengguna]

        with st.form("form_reset_default"):
            target_user = st.selectbox(
                "Pilih Pengguna:", options=senarai_user, key="reset_user_select"
            )
            hantar_reset = st.form_submit_button(
                "🔄 Reset Kata Laluan (123456)", width="stretch"
            )

            if hantar_reset:
                try:
                    # Set password default 123456 & tandakan perlu_tukar_pass = True
                    default_hash = hash_password("123456")

                    supabase.table("pengguna").update(
                        {"password_hash": default_hash, "perlu_tukar_pass": True}
                    ).eq("username", target_user).execute()

                    st.success(
                        f"✅ Kata laluan **{target_user}** berjaya di-reset ke"
                        " **123456**!"
                    )
                except Exception as e:
                    st.error(f"❌ Gagal reset kata laluan: {str(e)}")

    # TAB 4: TAMBAH PENGGUNA
    with tab_tambah:
        with st.form("form_add_user"):
            admin_new_user = st.text_input(
                "Username Baharu", placeholder="Contoh: PHA0003"
            )
            # AUTO-FILL KATA LALUAN SEMENTARA DI SINI
            admin_new_pass = st.text_input(
                "Kata Laluan Baharu", type="password", value="123456"
            )

            col_t1, col_t2 = st.columns(2)
            with col_t1:
                admin_new_had = st.number_input(
                    "Had Harian Awal", min_value=1, value=5
                )
                pilihan_role_tambah = (
                    ["pengguna", "admin", "super_admin"]
                    if user_role == "super_admin"
                    else ["pengguna", "admin"]
                )
                admin_new_role = st.selectbox(
                    "Peranan", pilihan_role_tambah, format_func=label_peranan
                )

            with col_t2:
                # Default tarikh tamat lesen: 1 tahun dari hari ini (365 hari)
                default_tarikh_tamat = datetime.now().date() + timedelta(days=365)
                admin_new_tarikh_tamat = st.date_input(
                    "📅 Tarikh Tamat Lesen",
                    value=default_tarikh_tamat,
                    format="YYYY-MM-DD",
                )

            if st.form_submit_button("✨ Cipta Akaun", width="stretch"):
                admin_new_user_bersih = admin_new_user.strip()
                if admin_new_user_bersih and admin_new_pass:
                    cek_user = (
                        supabase.table("pengguna")
                        .select("*")
                        .ilike("username", admin_new_user_bersih)
                        .execute()
                    )

                    if cek_user.data:
                        st.error(
                            f"⚠️ Username '{admin_new_user_bersih}' telah digunakan!"
                            " Sila guna username lain."
                        )
                    else:
                        supabase.table("pengguna").insert({
                            "username": admin_new_user_bersih,
                            "password_hash": hash_password(admin_new_pass),
                            "baki_semakan": admin_new_had,
                            "had_harian": admin_new_had,
                            "peranan": admin_new_role,
                            "status_lesen": "aktif",
                            "tarikh_terakhir": str(datetime.now().date()),
                            "tarikh_tamat_lesen": str(admin_new_tarikh_tamat),
                            "perlu_tukar_pass": True,  # PASTI KAN PENGGUNA DIPAKSA TUKAR PASS NANTI
                        }).execute()
                        st.success(
                            f"🎉 Akaun {admin_new_user_bersih}"
                            f" ({label_peranan(admin_new_role)}) berjaya dicipta!"
                        )
                        time.sleep(1)
                        st.rerun()
                else:
                    st.warning("⚠️ Sila isi nama pengguna dan kata laluan.")
        
    # TAB 4: TETAPAN GLOBAL (SUPERADMIN)
    with tab_tetapan:
        if user_role != "super_admin":
            st.error("🚫 Hanya Superadmin sahaja yang dibenarkan mengemas kini tetapan global sistem.")
        else:
            st.subheader("🛠️ Kawalan Tetapan Margin & Sistem Global")
            
            with st.form("form_tetapan_global"):
                col_t1, col_t2 = st.columns(2)
                with col_t1:
                    set_left = st.number_input("Default Margin Kiri (mm)", value=default_left)
                    set_right = st.number_input("Default Margin Kanan (mm)", value=default_right)
                with col_t2:
                    set_top = st.number_input("Default Margin Atas (mm)", value=default_top)
                    set_bottom = st.number_input("Default Margin Bawah (mm)", value=default_bottom)
                
                st.divider()
                set_status_semakan = st.toggle("Aktifkan Mod Semakan PTA untuk Semua Pengguna", value=tetapan_semasa.get("dibenarkan_semak", True))

                if st.form_submit_button("💾 Simpan Tetapan Global", type="primary", width="stretch"):
                    supabase.table("tetapan_sistem").update({
                        "margin_kiri": set_left,
                        "margin_kanan": set_right,
                        "margin_atas": set_top,
                        "margin_bawah": set_bottom,
                        "dibenarkan_semak": set_status_semakan
                    }).eq("id", 1).execute()
                    
                    st.cache_data.clear()
                    st.success("✅ Tetapan sistem berjaya dikemaskini!")
                    time.sleep(1)
                    st.rerun()

elif mod_halaman == "📄 Semakan Laporan PTA":
    # --- HERO HEADER BANNER (GAYA BIRU GELAP BERKILAT) ---
    st.markdown(
        """
        <style>
        .header-card {
            background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 50%, #2563eb 100%);
            padding: 24px 30px;
            border-radius: 16px;
            color: white;
            box-shadow: 0 10px 25px -5px rgba(30, 58, 138, 0.3);
            margin-bottom: 25px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        .header-title {
            font-size: 26px;
            font-weight: 700;
            margin: 0;
            color: #ffffff;
            letter-spacing: -0.5px;
        }
        .header-subtitle {
            font-size: 14px;
            color: #93c5fd;
            margin-top: 6px;
            font-weight: 500;
        }
        </style>
        
        <div class="header-card">
            <div class="header-title">📄 Sistem Semakan Format Laporan PTA</div>
            <div class="header-subtitle">Garis Panduan Pengurusan Projek Tahun Akhir (GPPTA KV 2026)</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    tetapan = ambil_tetapan_sistem()
    mod_semakan_aktif = tetapan.get("dibenarkan_semak", True)

    # Semak sama ada Superadmin ATAU mod semakan aktif
    if user_role == "super_admin" or mod_semakan_aktif:

        uploaded_file = st.file_uploader(
            "Muat Naik Fail PDF Laporan PTA",
            type=["pdf"],
            help="Sila muat naik fail PDF Laporan PTA untuk semakan format automatik.",
        )

        # PERHATIKAN: Kod proses semakan HANYA berjalan jika fail wujud
        if uploaded_file is not None:
            # --- KOD SEMAKAN PDF ANDA DI SINI ---
            st.write("Proses semakan dijalankan...")

    else:
        # Jika di-OFFkan oleh Superadmin, pembolehubah uploaded_file dijadikan None
        uploaded_file = None

        # Jika OFF dan bukan superadmin, paparkan mesej sekatan
        st.error("🚫 **Sistem Semakan Ditutup**")
        st.warning(
            "Mod Semakan PTA telah dimatikan oleh Pentadbir Sistem. Anda tidak boleh membuat semakan buat masa ini."
        )

    # Paparan kad maklumat jika fail belum dimuat naik
    if uploaded_file is None:
        st.markdown(
            """
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin-top: 20px;">
                <div style="background-color: #ffffff; padding: 16px; border-radius: 12px; border: 1px solid #e2e8f0; border-left: 5px solid #2563eb; box-shadow: 0 2px 6px rgba(0,0,0,0.04); display: flex; flex-direction: column; justify-content: space-between;">
                    <div>
                        <div style="font-weight: 700; color: #1e293b; font-size: 0.92rem; margin-bottom: 6px;">🔍 Semakan Automatik</div>
                        <p style="margin: 0; color: #64748b; font-size: 0.8rem; line-height: 1.4;">
                            Mengesan margin, saiz fon, kedudukan tajuk/jadual, dan struktur muka surat mengikut piawaian GPPTA 2026.
                        </p>
                    </div>
                    <p style="font-size: 0.72rem; color: #6c757d; margin-top: 10px; margin-bottom: 0; line-height: 1.3;">
                        <em>💡 <b>Nota:</b> Fungsi spacing, italic & format khas akan dikemaskini dalam versi akan datang.</em>
                    </p>
                </div>
                <div style="background-color: #ffffff; padding: 16px; border-radius: 12px; border: 1px solid #e2e8f0; border-left: 5px solid #d97706; box-shadow: 0 2px 6px rgba(0,0,0,0.04); display: flex; flex-direction: column;">
                    <div style="font-weight: 700; color: #1e293b; font-size: 0.92rem; margin-bottom: 6px;">⚡ Visual Interaktif</div>
                    <p style="margin: 0; color: #64748b; font-size: 0.8rem; line-height: 1.4;">Paparan berkotak warna terus pada PDF untuk memudahkan pembetulan format.</p>
                </div>
                <div style="background-color: #ffffff; padding: 16px; border-radius: 12px; border: 1px solid #e2e8f0; border-left: 5px solid #059669; box-shadow: 0 2px 6px rgba(0,0,0,0.04); display: flex; flex-direction: column;">
                    <div style="font-weight: 700; color: #1e293b; font-size: 0.92rem; margin-bottom: 6px;">📊 Audit Laporan</div>
                    <p style="margin: 0; color: #64748b; font-size: 0.8rem; line-height: 1.4;">Muat turun 3 jenis laporan analisis penuh serentak untuk rujukan penyelia/pelajar.</p>
                </div>
                <div style="background-color: #ffffff; padding: 16px; border-radius: 12px; border: 1px solid #e2e8f0; border-left: 5px solid #7c3aed; box-shadow: 0 2px 6px rgba(0,0,0,0.04); display: flex; flex-direction: column;">
                    <div style="font-weight: 700; color: #1e293b; font-size: 0.92rem; margin-bottom: 6px;">🔒 Terjamin & Selamat</div>
                    <p style="margin: 0; color: #64748b; font-size: 0.8rem; line-height: 1.4;">Dokumen anda diproses secara selamat dan tidak disimpan di dalam pelayan.</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Penukaran Nilai Margin ke Point (PT) secara Dinamik berdasarkan Sidebar
    MARGIN_LEFT_PT = margin_left_mm * MM_TO_PT
    MARGIN_RIGHT_PT = margin_right_mm * MM_TO_PT
    MARGIN_TOP_PT = margin_top_mm * MM_TO_PT
    MARGIN_BOTTOM_PT = margin_bottom_mm * MM_TO_PT

    def is_roman_numeral(val_str):
        """Fungsi menyemak secara dinamik sama ada perkataan ialah nombor Roman valid (i hingga c / 100+)"""
        val_str = val_str.lower().strip()
        if not val_str:
            return False
        # Pattern Regex khas untuk mengesahkan susunan nombor Roman yang sah (i, ii, iv, ix, xiv, xxviii, dsb)
        roman_pattern = r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$"
        return bool(re.match(roman_pattern, val_str, re.IGNORECASE))

    TABLE_PREFIX_REGEX = re.compile(
        r"^\s*(Table|Jadual)\s+\d+(\.\d+)*", re.IGNORECASE
    )
    FIGURE_PREFIX_REGEX = re.compile(
        r"^\s*(Figure|Rajah)\s+\d+(\.\d+)*", re.IGNORECASE
    )
    IN_TEXT_CITATION_REGEX = re.compile(
        r"^\s*(Figure|Rajah|Table|Jadual)\s+\d+(\.\d+)*\.\s", re.IGNORECASE
    )
    DOT_LEADER_REGEX = re.compile(r"\.{3,}\s*\d+|\b\d+\s*$", re.IGNORECASE)

    VERB_KEYWORDS_REGEX = re.compile(
        r"\b("
        r"shows?|showing|showed|"
        r"presents?|presenting|presented|"
        r"summarizes?|summarised|summarising|summarize|summarise|summary|"
        r"illustrates?|illustrating|illustrated|"
        r"depicts?|depicting|depicted|"
        r"lists?|listing|listed|"
        r"compares?|comparing|compared|"
        r"indicates?|indicating|indicated|"
        r"displays?|displaying|displayed|"
        r"describes?|describing|described|"
        r"provides?|providing|provided|"
        r"menunjukkan|menyenaraikan|mencatatkan|memaparkan|menggambarkan|merumuskan|membandingkan|menyediakan|memberikan"
        r")\b",
        re.IGNORECASE
    )

    # =========================================================
    # FUNGSI-FUNGSI PENJANAN LAPORAN PDF & PEMBANTU (HELPERS)
    # =========================================================

    def sanitize_text_for_fpdf(text):
        """Menukar aksara Unicode khas kepada aksara Latin standard yang disokong oleh FPDF (helvetica)."""
        if not isinstance(text, str):
            text = str(text)

        replacements = {
            "–": "-",  # En-dash ke hyphen biasa
            "—": "-",  # Em-dash ke hyphen biasa
            "‘": "'",  # Smart quote kiri ke petik biasa
            "’": "'",  # Smart quote kanan ke petik biasa
            "“": '"',  # Smart double quote kiri
            "”": '"',  # Smart double quote kanan
            "…": "...", # Ellipsis
            "•": "-",  # Bullet point
        }
        for orig, repl in replacements.items():
            text = text.replace(orig, repl)

        return text.encode("latin-1", "replace").decode("latin-1")

    def generate_full_audit_pdf(doc, errors_per_page, ignored_errors):
        """
        Menjana PDF Audit Lanskap Side-by-Side secara bersih 
        tanpa latar belakang / bingkai kelabu bertindih.
        """
        pdf_bytes = doc.tobytes()
        annotated_doc = fitz.open("pdf", pdf_bytes)

        # 1. Tandakan kotak ralat & garisan margin pada dokumen
        for p_num in range(len(annotated_doc)):
            p = annotated_doc[p_num]
            
            # Lukis garisan margin mengikut orientasi sebenar
            tambah_garis_margin_ke_pdf_page(p)

            # Lukis kotak ralat merah
            raw_errs = []
            if isinstance(errors_per_page, list):
                raw_errs = errors_per_page[p_num] if p_num < len(errors_per_page) else []
            elif isinstance(errors_per_page, dict):
                raw_errs = errors_per_page.get(p_num, [])

            for idx, err in enumerate(raw_errs):
                err_id = f"p{p_num+1}_{idx}"
                if isinstance(err, dict) and err_id not in ignored_errors:
                    if err.get("bbox"):
                        p.draw_rect(err["bbox"], color=(1, 0, 0), width=1.5)

        output_pdf = fitz.open()

        # 2. Bina Laporan Side-by-Side
        for page_num in range(len(annotated_doc)):
            src_page = annotated_doc[page_num]
            src_rect = src_page.rect
            
            is_landscape_page = (src_rect.width > src_rect.height) or (src_page.rotation in [90, 270])

            if isinstance(errors_per_page, list):
                raw_issues = errors_per_page[page_num] if page_num < len(errors_per_page) else []
            elif isinstance(errors_per_page, dict):
                raw_issues = errors_per_page.get(page_num, [])
            else:
                raw_issues = []

            page_issues = []
            for idx, err in enumerate(raw_issues):
                err_id = f"p{page_num+1}_{idx}"
                if isinstance(err, dict) and err_id not in ignored_errors:
                    page_issues.append(err)

            issue_index = 0
            total_issues = len(page_issues)
            is_first_subpage = True

            while True:
                # Bina halaman Side-by-Side (A4 Landscape: 842 x 595 pt)
                new_page = output_pdf.new_page(width=842, height=595)

                # --- PANEL KIRI (PRATONTON DOKUMEN SAHAJA) ---
                if is_first_subpage:
                    if is_landscape_page:
                        paper_box = fitz.Rect(15, 140, 405, 420)
                    else:
                        paper_box = fitz.Rect(15, 15, 405, 580)

                    # TAMPAL MUKA SURAT PDF SECARA DIRECT (TANPA DRAW_RECT KELABU)
                    rot_val = src_page.rotation
                    new_page.show_pdf_page(
                        paper_box, 
                        annotated_doc, 
                        page_num, 
                        rotate=rot_val, 
                        keep_proportion=True
                    )
                else:
                    new_page.insert_text(
                        fitz.Point(50, 280), 
                        f"SAMBUNGAN SENARAI ISU\nMUKA SURAT {page_num + 1}", 
                        fontsize=14, 
                        color=(0.3, 0.3, 0.3)
                    )

                # --- GARISAN PEMISAH TENGAH ---
                new_page.draw_line(fitz.Point(420, 15), fitz.Point(420, 580), color=(0.7, 0.7, 0.7), width=1)

                # --- PANEL KANAN (SENARAI ISU) ---
                header_title = f"MUKA SURAT {page_num + 1} - SENARAI ISU DIKESAN"
                if not is_first_subpage:
                    header_title += " (SAMBUNGAN)"

                new_page.insert_text(fitz.Point(445, 40), header_title, fontsize=11, color=(0.1, 0.3, 0.6))
                new_page.draw_line(fitz.Point(445, 48), fitz.Point(812, 48), color=(0.8, 0.8, 0.8), width=0.8)

                y_pos = 70

                if total_issues == 0:
                    new_page.insert_text(fitz.Point(445, y_pos), "✅ Tiada isu dikesan pada muka surat ini.", fontsize=10, color=(0, 0.5, 0))
                    break

                while issue_index < total_issues and y_pos <= 540:
                    issue = page_issues[issue_index]
                    ayat_isu = (
                        issue.get("text") or 
                        issue.get("msg") or 
                        issue.get("label") or 
                        issue.get("description") or 
                        "Ralat Format Margin / Teks"
                    )
                    ayat_isu = ayat_isu.replace("Abaikan (Bypass): ", "").strip()

                    new_page.insert_text(fitz.Point(445, y_pos), f"{issue_index + 1}. ⚠️ {ayat_isu}", fontsize=9.5, color=(0.8, 0.1, 0.1))

                    y_pos += 22
                    issue_index += 1

                if issue_index >= total_issues:
                    break

                is_first_subpage = False

        annotated_doc.close()
        return output_pdf.write()

    def generate_pdf_report(filtered_errors, total_pages):
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # Tajuk Utama
        pdf.set_font("Helvetica", "B", 16)
        title_str = sanitize_text_for_fpdf("Laporan Semakan Format Laporan PTA (GPPTA KV 2026)")
        pdf.cell(
            0,
            10,
            title_str,
            new_x="LMARGIN",
            new_y="NEXT",
            align="C",
        )

        # Sub-tajuk Jumlah Muka Surat
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(
            0,
            6,
            f"Jumlah Muka Surat Diperiksa: {total_pages}",
            new_x="LMARGIN",
            new_y="NEXT",
            align="C",
        )
        pdf.ln(5)

        if not filtered_errors:
            pdf.set_font("Helvetica", "B", 12)
            pdf.cell(
                0,
                10,
                "Tiada isu format dikesan. Laporan PTA mematuhi piawaian!",
                new_x="LMARGIN",
                new_y="NEXT",
            )
        else:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(30, 8, "Muka Surat", border=1, align="C")
            pdf.cell(
                160,
                8,
                "Butiran Isu Format",
                border=1,
                align="C",
                new_x="LMARGIN",
                new_y="NEXT",
            )

            pdf.set_font("Helvetica", "", 10)
            for item in filtered_errors:
                page_str = f"MS {item['page']}"
                raw_msg = item["msg"].replace("*", "")
                clean_issue_str = sanitize_text_for_fpdf(raw_msg)

                pdf.cell(30, 8, page_str, border=1, align="C")
                pdf.cell(
                    160, 8, clean_issue_str[:90], border=1, new_x="LMARGIN", new_y="NEXT"
                )

        return bytes(pdf.output())

    def generate_annotated_report(doc_input, all_pages_errors, ignored_set):
        pdf_buffer = io.BytesIO()
        doc_input.save(pdf_buffer)
        pdf_buffer.seek(0)
        annotated_doc = fitz.open(stream=pdf_buffer.read(), filetype="pdf")

        for page_num, errors in enumerate(all_pages_errors):
            page = annotated_doc[page_num]

            # 1. Lukis garisan margin putus-putus dinamik
            tambah_garis_margin_ke_pdf_page(page)

            # 🛑 2. Kumpulkan semua bbox ralat yang TIDAK diabaikan (Active Errors)
            senarai_bbox_aktif = []
            for i, err in enumerate(errors):
                err_id = f"p{page_num+1}_{i}"
                if err.get("bbox") and err_id not in ignored_set:
                    senarai_bbox_aktif.append(err["bbox"])

            # 🟢 3. Tapis melalui logik kampoi (Isu < 3 kekal kotak terpisah, Isu >= 3 dikampoi)
            bbox_hendak_dilukis = kampoi_bbox(senarai_bbox_aktif, ambang_kampoi=3)

            # 4. Lukis kotak merah ralat
            for bbox in bbox_hendak_dilukis:
                page.draw_rect(bbox, color=(1, 0, 0), width=1.5)

        out_buffer = io.BytesIO()
        # PASTIKAN: Simpan tanpa mengubah kekemasan halaman
        annotated_doc.save(out_buffer)
        annotated_doc.close()
        return out_buffer.getvalue()

    def get_base_filename(uploaded_filename):
        """Mengambil nama fail tanpa ekstensi .pdf."""
        base_name, _ = os.path.splitext(uploaded_filename)
        return base_name.strip()


    def create_download_button_html(file_bytes, filename, button_text, color="#2563eb"):
        b64 = base64.b64encode(file_bytes).decode()
        href = f"data:application/pdf;base64,{b64}"
        return f"""
        <a href="{href}" download="{filename}" style="text-decoration: none;">
            <div style="
                background-color: {color};
                color: white;
                padding: 12px 20px;
                text-align: center;
                border-radius: 8px;
                font-weight: bold;
                font-size: 15px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                transition: 0.3s;
                cursor: pointer;
                margin-top: 10px;">
                {button_text}
            </div>
        </a>
        """

    # =========================================================
    # PROSES SEMAKAN & IMBASAN FAIL PDF
    # =========================================================
    if uploaded_file is not None:
        # 1. Semak baki harian pengguna melalui Supabase
        if baki_semasa <= 0:
            st.error("🛑 **HAD SEMAKAN HARIAN TERCAPAI**")
            st.info("Anda telah mencapai had semakan percuma untuk hari ini. Sila cuba lagi esok.")
            st.stop()

        pdf_bytes = uploaded_file.getvalue()
        if len(pdf_bytes) == 0:
            st.error("Fail PDF yang dimuat naik kelihatan kosong. Sila pilih fail lain.")
            st.stop()

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        # RESET MEMORI DOKUMEN BILA FAIL BAHARU
        if "current_file_bytes" not in st.session_state or st.session_state.current_file_bytes != pdf_bytes:
            st.session_state.current_file_bytes = pdf_bytes
            st.session_state.upload_start_time = time.time()
            st.session_state.report_pdf_bytes = None
            st.session_state.annotated_pdf_bytes = None
            st.session_state.survey_completed_cb = False
            st.session_state.logged_sesi_1 = False

        # Pemotongan Baki Supabase (Hanya bila fail baharu)
        if "last_uploaded_file" not in st.session_state or st.session_state.last_uploaded_file != uploaded_file.name:
            st.session_state.last_uploaded_file = uploaded_file.name
            baki_baru = tolak_baki_semakan(current_user["id"], baki_semasa)
            st.session_state["user"]["baki_semakan"] = baki_baru
            baki_semasa = baki_baru

        st.success(f"Fail '{uploaded_file.name}' Berjaya Diimbas! Baki semakan harian: {baki_semasa}")
        st.success(f"Jumlah muka surat: {len(doc)}")

        # Inisialisasi Session State Sokongan
        if "upload_start_time" not in st.session_state:
            st.session_state.upload_start_time = time.time()
        if "ignored_errors" not in st.session_state:
            st.session_state.ignored_errors = set()
        if "report_pdf_bytes" not in st.session_state:
            st.session_state.report_pdf_bytes = None
        if "annotated_pdf_bytes" not in st.session_state:
            st.session_state.annotated_pdf_bytes = None

        # =========================================================
        # 📌 🚀 PANGGIL FUNGSI SEMAKAN UTAMA DI SINI!
        # =========================================================
        # Panggil fungsi dengan hantar nilai checkbox aktifkan_spacing
        senarai_ralat = semak_keseluruhan_dokumen(doc, aktifkan_spacing=aktifkan_spacing)

        # Simpan keputusan ralat ke dalam session state supaya tidak hilang bila click button
        st.session_state.senarai_ralat = senarai_ralat

        def toggle_bypass(err_id):
            # TAMBAH BARIS INI: Pastikan key wujud sebelum disemak
            if "ignored_errors" not in st.session_state:
                st.session_state.ignored_errors = set()

            if err_id in st.session_state.ignored_errors:
                st.session_state.ignored_errors.remove(err_id)
            else:
                st.session_state.ignored_errors.add(err_id)

            st.session_state.report_pdf_bytes = None
            st.session_state.annotated_pdf_bytes = None
            st.rerun()

        # ✅ TAMBAH FUNGSI INI (Rapat ke margin kiri / bertaraf global)
        def reset_all_ignored():
            # 1. Bersihkan set ralat
            st.session_state.ignored_errors = set()

            # 2. Reset SEMUA key checkbox Streamlit ke False
            for key in list(st.session_state.keys()):
                if key.startswith("cb_"):
                    st.session_state[key] = False

        def toggle_bypass_page(page_err_ids):
            all_ignored = all(eid in st.session_state.ignored_errors for eid in page_err_ids)
            for eid in page_err_ids:
                if all_ignored:
                    st.session_state.ignored_errors.discard(eid)
                else:
                    st.session_state.ignored_errors.add(eid)

            st.session_state.report_pdf_bytes = None
            st.session_state.annotated_pdf_bytes = None
            st.rerun()

        detected_issues = []
        all_pages_errors_list = []
        is_previous_list_page = False
        in_appendix_section = False

        # IMBASAN GELUNG (PAGE LOOP)
        for page_num in range(len(doc)):
            page = doc[page_num]
            rect = page.rect
            blocks = page.get_text("dict")["blocks"]
            
            # 📌 1. INISIALISASI SENARAI RALAT
            page_errors = []
            page_errors.extend(semak_saiz_kertas(page))

            # Ekstrak data imej/lukisan
            images_info = page.get_image_info() if abaikan_teks_dalam_gambar or semak_caption else []
            drawings = page.get_drawings() if semak_caption else []

            full_page_text = page.get_text()
            page_text_lower = full_page_text.lower()

            # ---------------------------------------------------------
            # 📌 1.1 TAKRIF PEMBOLEH UBAH PENGECUALIAN (DI ATAS SEKALI!)
            # ---------------------------------------------------------
            skip_justify_keywords = [
                "pengakuan penulis",
                "pengakuan",
                "perakuan penyelia",
                "perakuan pegawai",
                "pengesahan luaran",
                "isi kandungan",
                "table of contents",
                "kandungan",
                "senarai jadual",
                "senarai rajah",
                "senarai lampiran",
                "senarai singkatan",
                "list of tables",
                "list of figures"
            ]

            is_cover_page = (page_num == 0)
            is_appendix_page = any(
                k in page_text_lower for k in ["appendix", "appendices", "lampiran"]
            )
            # Mengesan M/S Isi Kandungan (termasuk M/S sambungan tanpa tajuk)
            is_front_matter = is_toc_page(page, skip_justify_keywords)

            if page_num == 48:  # MS 49
                print("DEBUG MS49:", "front_matter=", is_front_matter, "| appendix=", is_appendix_page, "| cover=", is_cover_page, flush=True)
                if page_num == 48:
                    _t = page.get_text().upper()
                    print("DEBUG KW:", [k for k in skip_justify_keywords if k.upper() in _t], flush=True)
            # ---------------------------------------------------------
            # 📌 2. TENTUKAN ORIENTASI & MARGIN SASARAN
            # ---------------------------------------------------------
            is_landscape = rect.width > rect.height
            target_kiri_mm = 25 if is_landscape else 40
            target_atas_mm = 40 if is_landscape else 25
            target_kanan_mm = 25
            target_bawah_mm = 25

            # Kira koordinat margin dalam unit 'pt'
            MM_TO_PT = 2.83465
            cur_m_left = target_kiri_mm * MM_TO_PT
            cur_m_top = target_atas_mm * MM_TO_PT
            cur_m_right = rect.width - (target_kanan_mm * MM_TO_PT)
            cur_m_bottom = rect.height - (target_bawah_mm * MM_TO_PT)

            # ---------------------------------------------------------
            # 📌 3. SEMAKAN MARGIN (4 SISI)
            # ---------------------------------------------------------
            page_errors.extend(check_margin_kiri_violations(page, target_margin_mm=target_kiri_mm))
            page_errors.extend(check_margin_atas_violations(page, target_margin_mm=target_atas_mm))
            page_errors.extend(check_margin_kanan_violations(page, target_margin_mm=target_kanan_mm))
            page_errors.extend(check_margin_bawah_violations(page, target_margin_mm=target_bawah_mm))

            # ---------------------------------------------------------
            # 📌 SEMAKAN KEDUKAN TAJUK JADUAL & RAJAH
            # ---------------------------------------------------------
            page_errors.extend(semak_kedudukan_tajuk_jadual(page))  # Tajuk Jadual MESTI di ATAS
            page_errors.extend(semak_kedudukan_tajuk_rajah(page))   # Tajuk Rajah MESTI di BAWAH

            if not is_front_matter:
                page_errors.extend(semak_tajuk_bab_capital(page))

            # ---------------------------------------------------------
            # 4. SEMAKAN FONT & SAIZ (DILANGKAU JIKA LAMPIRAN)
            # ---------------------------------------------------------
            if not is_appendix_page:
                page_errors.extend(check_font_violations(page, allowed_fonts=allowed_fonts))

            # ---------------------------------------------------------
            # 📌 4. SEMAKAN SPACING & FON
            # ---------------------------------------------------------
            # Jalankan semakan line spacing HANYA jika bukan muka surat pengecualian
            if aktifkan_spacing:
                page_errors.extend(semak_jarak_baris_perenggan(page))

            # Semakan font dikekalkan untuk semua muka surat
            #page_errors.extend(check_font_violations(page, allowed_fonts=["arial"], min_font_size=8.0))

            # ---------------------------------------------------------
            # 📌 5. SEMAKAN JUSTIFY PERENGGAN
            # ---------------------------------------------------------
            if not (is_cover_page or is_front_matter or is_appendix_page):
                ralat_justify = semak_justify_perenggan(page, tolerance_pt=6.0)
                page_errors.extend(ralat_justify)

            # ---------------------------------------------------------
            # 📌 6. PENGESANAN MUKA SURAT SENARAI / KANDUNGAN
            # ---------------------------------------------------------
            has_list_header = any(
                k in page_text_lower
                for k in [
                    "list of tables", "list of figures", "senarai jadual",
                    "senarai rajah", "table of contents", "kandungan"
                ]
            )

            has_dot_leaders = bool(DOT_LEADER_REGEX.search(full_page_text))
            is_list_page = has_list_header or (is_previous_list_page and has_dot_leaders)
            is_previous_list_page = is_list_page

            # ---------------------------------------------------------
            # 📌 7. PASS 1: PRE-SCANNING NOMBOR MUKA SURAT
            # ---------------------------------------------------------
            pagenum_bboxes = []
            has_pagenum_found = False

            w_rect, h_rect = rect.width, rect.height
            is_landscape_page = is_landscape or (w_rect > h_rect) or (page.rotation in [90, 270])

            for b in blocks:
                if "lines" not in b:
                    continue
                for line in b["lines"]:
                    for span in line["spans"]:
                        text_str = span["text"].strip()
                        
                        # Pintas teks kosong atau nombor sub-tajuk (seperti 3.4 dan 3.3)
                        if not text_str or "." in text_str:
                            continue

                        clean_w = re.sub(r"[^a-zA-Z0-9]", "", text_str.lower())
                        
                        if clean_w.isdigit() or is_roman_numeral(clean_w):
                            # Abaikan jika digit terlalu panjang (bukan nombor muka surat)
                            if len(clean_w) > 4:
                                continue

                            sx0, sy0, sx1, sy1 = span["bbox"]

                            # Semak kawasan margin/luar kandungan
                            is_in_margin_zone = (
                                (sx0 < 200) or (sy0 < 200) or 
                                (sy0 > (h_rect - 200)) or (sx0 > (w_rect - 200))
                            )

                            if is_in_margin_zone:
                                has_pagenum_found = True
                                pagenum_bboxes.append((sx0, sy0, sx1, sy1))

                                s_dir = span.get("dir", (1, 0))
                                is_horizontal_text = abs(s_dir[0]) > 0.5  # True jika mendatar 0°

                                if is_landscape_page:
                                    # Standard GPPTA Lanskap: Nombor M/S MESTI diputar 90°
                                    if is_horizontal_text:
                                        page_errors.append({
                                            "msg": f"Orientasi Nombor M/S '{text_str}' Salah (Mesti Diputar 90° Mengikut Format Jilid)",
                                            "bbox": (sx0, sy0, sx1, sy1)
                                        })
                                else:
                                    # Zon Nombor M/S Portrait
                                    if sy0 > (h_rect - 100):
                                        right_min = w_rect * 0.55
                                        if sx0 < right_min:
                                            loc_name = "bawah tengah" if sx0 >= (w_rect * 0.33) else "bawah kiri"
                                            # page_errors.append(...)

            # =========================================================================
            # SEMAKAN MUKA SURAT KOSONG (BLANK PAGE)
            # =========================================================================
            full_page_text_clean = page.get_text().strip()
            page_images = page.get_images()  # Dapatkan senarai gambar pada muka surat

            # Jika tiada teks DAN tiada gambar dikesan
            if not full_page_text_clean and len(page_images) == 0:
                page_errors.append(
                    {
                        "msg": "Muka surat kosong (blank page) dikesan. Sila padam muka surat ini daripada fail PDF.",
                        "bbox": None,
                    }
                )

            # PASS 2: SEMAKAN MARGIN & TEKS
            # =========================================================================
            # 1. SEMAKAN MARGIN UNTUK IMEJ / GAMBAR (Cth: Carta Gantt Format Imej)
            # =========================================================================
            # 📌 Tambah 2 baris ini di atas 'if images_info:'
            target_top_mm = 40 if is_landscape else 25
            target_bottom_mm = 25

            # 1. SEMAKAN MARGIN UNTUK IMEJ / GAMBAR
            if images_info:
                for img in images_info:
                    ix0, iy0, ix1, iy1 = img["bbox"]
                    
                    # Semak jika imej melangkaui Margin Atas
                    if iy0 < (cur_m_top - 2):
                        msg = format_margin_msg("Imej/Gambar", "", iy0, target_top_mm, "Atas")
                        page_errors.append({"msg": msg, "bbox": (ix0, iy0, ix1, iy1)})
                        
                    # Semak jika imej melangkaui Margin Bawah
                    if iy1 > (cur_m_bottom + 2):
                        msg = format_margin_msg("Imej/Gambar", "", iy1, target_bottom_mm, "Bawah")
                        page_errors.append({"msg": msg, "bbox": (ix0, iy0, ix1, iy1)})

            # Kriteria Halaman Sampul/Tajuk: Muka Surat 1 ATAU mengandungi kata kunci 'DIPLOMA' & 'KOLEJ VOKASIONAL'
            # =========================================================================
            # 2. SEMAKAN TEKS & MARGIN TEKS
            # =========================================================================

            # Dapatkan teks penuh muka surat dalam huruf besar untuk pengecaman kata kunci
            page_text_upper = page.get_text().upper()

            # =========================================================================
            # 📍 BAHAGIAN 1: PENGECAMAN JENIS MUKA SURAT KHUSUS (GPPTA KV)
            # =========================================================================

            page_text_upper = page.get_text().upper()

            # 1. Halaman Tajuk Dalam (Lampiran 4)
            is_inner_cover = (
                "LAPORAN PROJEK YANG DIKEMUKAKAN" in page_text_upper 
                or "BAGI MEMENUHI SEBAHAGIAN" in page_text_upper
            )

            # 2. Halaman Borang / Awalan (Perakuan, Pengakuan, Penghargaan, Abstrak, dll)
            is_borang_awalan = any(k in page_text_upper for k in [
                "PENGAKUAN PENULIS",
                "PERAKUAN PENYELIA",
                "PERAKUAN PEGAWAI",
                "DENGAN INI SAYA AKUI",
                "MEMPERAKUI BAHAWA",
                "PENGHARGAAN",
                "ABSTRAK",
                "ABSTRACT",
                "SENARAI KANDUNGAN",
                "SENARAI JADUAL",
                "SENARAI RAJAH"
            ])

            # 3. Kulit Muka Hadapan / Hardcover (Lampiran 2)
            # Mesti BUKAN Halaman Tajuk Dalam dan BUKAN Halaman Borang Awalan
            is_outer_cover = (
                (page_num == 0 or ("DIPLOMA" in page_text_upper and "KOLEJ VOKASIONAL" in page_text_upper))
                and not is_inner_cover
                and not is_borang_awalan
            )

            for b in blocks:
                if "lines" in b:
                    for line in b["lines"]:
                        full_line_text = "".join([s["text"] for s in line["spans"]]).strip()

                        for span in line["spans"]:
                            text = span["text"].strip()
                            size = round(span["size"], 1)
                            font_name = span["font"]
                            bbox = span["bbox"]

                            if not text:
                                continue

                            x0, y0, x1, y1 = bbox

                            is_this_pagenum_span = False
                            for p_box in pagenum_bboxes:
                                if abs(y0 - p_box[1]) < 15 and abs(x0 - p_box[0]) < 30:
                                    is_this_pagenum_span = True
                                    break

                            if is_this_pagenum_span:
                                continue

                            # 📌 1. Semakan Ralat Margin Bawah (Kemaskini dengan MM)
                            if y1 > (cur_m_bottom + 2):
                                msg = format_margin_msg("Teks", full_line_text, y1, target_bottom_mm, "Bawah")
                                page_errors.append({"msg": msg, "bbox": bbox})

                            # 📌 2. Semakan Ralat Margin Atas (Kemaskini dengan MM)
                            if y0 < (cur_m_top - 2):
                                msg = format_margin_msg("Teks", full_line_text, y0, target_top_mm, "Atas")
                                page_errors.append({"msg": msg, "bbox": bbox})

                            # Semakan Jenis & Saiz Font
                            skip_font_check = in_appendix_section or (abaikan_appendix and is_appendix_page)

                            if not skip_font_check:
                                font_name_clean = font_name.lower().replace(" ", "")
                                is_math_font = any(mf in font_name_clean for mf in MATH_SYMBOL_FONTS)

                                is_inside_image = False
                                if abaikan_teks_dalam_gambar and images_info:
                                    for img in images_info:
                                        img_bbox = img["bbox"]
                                        text_center_x = (x0 + x1) / 2
                                        text_center_y = (y0 + y1) / 2

                                        if (img_bbox[0] <= text_center_x <= img_bbox[2]) and \
                                        (img_bbox[1] <= text_center_y <= img_bbox[3]):
                                            is_inside_image = True
                                            break
                                
                                    # ✅ BAHAGIAN 2: SEMAKAN SAIZ FONT KHUSUS GPPTA
                                    if len(text.strip()) > 1:
                                        
                                        # ---------------------------------------------------------------------
                                        # 1. KULIT MUKA HADAPAN / HARDCOVER (LAMPIRAN 2) - SEMUA 18pt
                                        # ---------------------------------------------------------------------
                                        if is_outer_cover:
                                            if not (16.5 <= size <= 18.5):
                                                page_errors.append({
                                                    "msg": f"Kulit Hadapan: Teks mestilah saiz 18pt (dikesan {size}pt): '{text[:25]}...'",
                                                    "bbox": bbox
                                                })

                                        # ---------------------------------------------------------------------
                                        # 2. MUKA HADAPAN DALAM (LAMPIRAN 4) - PELBAGAI SAIZ FONT
                                        # ---------------------------------------------------------------------
                                        elif is_inner_cover:
                                            text_clean = text.strip()
                                            text_upper = text_clean.upper()

                                            # A. Perkataan "Oleh" -> Wajib Saiz 16pt (15.0 - 16.5pt)
                                            if text_clean.lower() == "oleh":
                                                if not (15.0 <= size <= 16.5):
                                                    page_errors.append({
                                                        "msg": f"Muka Hadapan Dalam: 'Oleh' mestilah saiz 16pt (dikesan {size}pt)",
                                                        "bbox": bbox
                                                    })

                                            # B. Program & Tahun (Contoh: "PROGRAM TEKNOLOGI..." / "2024") -> Wajib Saiz 14pt (13.5 - 14.8pt)
                                            elif "PROGRAM" in text_upper or (text_clean.isdigit() and len(text_clean) == 4):
                                                if not (13.5 <= size <= 14.8):
                                                    page_errors.append({
                                                        "msg": f"Muka Hadapan Dalam: Program/Tahun mestilah saiz 14pt (dikesan {size}pt): '{text[:25]}...'",
                                                        "bbox": bbox
                                                    })

                                            # C. Ayat Pengesahan (13pt) -> Merangkumi semua baris pecahan ayat pengesahan
                                            elif any(k in text_upper for k in ["LAPORAN PROJEK", "DIKEMUKAKAN", "MEMENUHI", "SEBAHAGIAN", "KEPERLUAN"]):
                                                if not (12.0 <= size <= 13.5):
                                                    page_errors.append({
                                                        "msg": f"Muka Hadapan Dalam: Ayat pengesahan mestilah saiz 13pt (dikesan {size}pt): '{text[:25]}...'",
                                                        "bbox": bbox
                                                    })

                                            # D. Tajuk Projek & Nama Pelajar -> Wajib Saiz 18pt (16.5 - 18.5pt)
                                            else:
                                                if len(text_clean) > 3 and not (16.5 <= size <= 18.5):
                                                    page_errors.append({
                                                        "msg": f"Muka Hadapan Dalam: Tajuk/Nama Penulis mestilah saiz 18pt (dikesan {size}pt): '{text[:25]}...'",
                                                        "bbox": bbox
                                                    })

                                        # ---------------------------------------------------------------------
                                        # 3. MUKA SURAT KANDUNGAN BIASA (8.5pt - 14.0pt)
                                        # ---------------------------------------------------------------------
                                        else:
                                            if size < 8.5:
                                                page_errors.append({
                                                    "msg": f"Saiz font terlalu kecil ({size}pt, min 8.5pt): '{text[:25]}...'",
                                                    "bbox": bbox
                                                })
                                            elif size > 14.5:
                                                page_errors.append({
                                                    "msg": f"Saiz font ({size}pt) melebihi had tajuk kandungan (max 14.0pt): '{text[:25]}...'",
                                                    "bbox": bbox
                                                })

                            # Semakan Tajuk Jadual / Rajah
                            if semak_caption and not is_list_page:
                                is_dot_leader_line = bool(DOT_LEADER_REGEX.search(full_line_text))
                                is_sentence = bool(VERB_KEYWORDS_REGEX.search(full_line_text))
                                is_in_text_citation = bool(IN_TEXT_CITATION_REGEX.match(full_line_text))

                                # 🟢 TAMBAH BARAIS INI DI BAWAHNYA:
                                # Jika baris ini ada kata kerja (perenggan biasa), ada dot leader TOC, atau rujukan dalam teks, ABAIKAN!
                                if is_sentence or is_in_text_citation or is_dot_leader_line:
                                    continue  # Skip baris ini daripada disemak sebagai tajuk rajah/jadual

            # SEMAKAN KEHADIRAN NOMBOR MUKA SURAT
            if not in_appendix_section:
                lines = [line.strip().upper() for line in full_page_text.split("\n") if line.strip()]
                for line in lines:
                    if (line.startswith("APPENDIX") or line.startswith("LAMPIRAN")) and len(line) < 60:
                        in_appendix_section = True
                        break

            is_other_exempted = any(k in page_text_lower for k in ["list of publications", "publication"])
            skip_pagenum_check = (in_appendix_section and abaikan_pagenum_appendix) or is_other_exempted

            if page_num >= 2 and not skip_pagenum_check and not has_pagenum_found:
                loc_label = "sebelah kiri/atas" if is_landscape else "bahagian bawah tengah"
                page_errors.append(
                    {
                        "msg": f"Nombor muka surat tidak dikesan di {loc_label}.",
                        "bbox": None,
                    }
                )

            unique_page_errors = []
            seen_msgs = set()
            for e in page_errors:
                # Ambil nilai 'msg' jika ada, jika tiada ambil 'mesej'
                msg_text = e.get("msg") or e.get("mesej", "")
                bb = e.get("bbox")
                # Jadual berbeza dengan mesej sama mesti kekal berasingan
                if "(Jadual)" in msg_text and bb:
                    key = (msg_text, tuple(round(v) for v in bb))
                else:
                    key = msg_text
                if key not in seen_msgs:
                    seen_msgs.add(key)
                    unique_page_errors.append(e)

            all_pages_errors_list.append(unique_page_errors)

            # Kumpul ralat aktif untuk laporan
            for i, err in enumerate(unique_page_errors):
                err_id = f"p{page_num+1}_{i}"
                if err_id not in st.session_state.ignored_errors:
                    mesej_ralat = err.get("msg") or err.get("mesej", "Terdapat ralat format.")
                    detected_issues.append({"page": page_num + 1, "msg": mesej_ralat})

        # =========================================================================
        # 📌 GABUNGKAN SEMAKAN PENOMBORAN DOKUMEN KE DALAM all_pages_errors_list
        # =========================================================================
        # Panggil fungsi semakan penomboran GPPTA
        numbering_errors = semak_penomboran_gppta(doc)

        # Masukkan ralat penomboran mengikut index muka surat (0-based)
        for err in numbering_errors:
            page_idx = err["page"] - 1  # Tukar nombor muka surat (1-based) ke index (0-based)
            if 0 <= page_idx < len(doc):
                all_pages_errors_list[page_idx].append({
                    "msg": err["msg"],
                    "bbox": err.get("bbox")  # Kotak merah akan dilukis automatik jika bbox wujud
                })

        # =========================================================================
        # 📌 2. GABUNGKAN SEMAKAN FORMAT RUJUKAN APA
        # =========================================================================
        rujukan_errors = semak_format_rujukan_apa(doc)

        for err in rujukan_errors:
            page_idx = err["page"] - 1
            if 0 <= page_idx < len(doc):
                all_pages_errors_list[page_idx].append({
                    "msg": err["msg"],
                    "bbox": err.get("bbox")  # Kotak merah dilukis terus pada baris rujukan bermasalah
                })

        # =========================================================================
        # 🔍 PRATONTON VISUAL PER MUKA SURAT
        # =========================================================================
        st.markdown("---")
        st.subheader("🔍 Mod Semakan & Pratonton Visual")

        # 📌 1. MASTER TOGGLE (BYPASS ALL PAGES)
        bypass_all_doc = st.checkbox(
            "🌐 Abaikan Semua Isu Dokumen (Bypass All Pages)",
            value=st.session_state.get("bypass_all_doc", False),
            key="bypass_all_doc",
            help="Tanda di sini jika anda mahu mengabaikan semua isu yang dikesan di seluruh muka surat sekaligus."
        )

        # 📌 2. KIRA JUMLAH ISU ASAL & ISU AKTIF
        all_detected_issues = [err for p_errs in all_pages_errors_list for err in p_errs]
        total_original_count = len(all_detected_issues)  # Ini akan sentiasa dapat 281 isu asal

        if bypass_all_doc:
            active_issues_count = 0
        else:
            # Kira isu yang belum diabaikan secara individu
            active_issues_count = sum(
                1 for page_num, p_errs in enumerate(all_pages_errors_list)
                for i, err in enumerate(p_errs)
                if f"p{page_num+1}_{i}" not in st.session_state.get("ignored_errors", set())
            )

        # PAPARAN TAJUK JUMLAH ISU
        st.write(
            f"Jumlah isu aktif yang disahkan untuk dilaporkan: **{active_issues_count} isu** "
            f"*(daripada {total_original_count} isu dikesan)*"
        )

        # ✅ GANTI DENGAN KOD BAHARU INI:
        if not bypass_all_doc and active_issues_count < total_original_count:
            num_diabaikan = total_original_count - active_issues_count
            st.button(
                f"🔄 Reset Semula Semua Abaikan (Munculkan Balik {num_diabaikan} Isu)",
                key="reset_ignored_btn",
                on_click=reset_all_ignored,  # <-- Memanggil fungsi yang kita buat di Langkah 1
            )

        # 📌 3. GELUNG PAPARAN PER MUKA SURAT
        for page_num in range(len(doc)):
            unique_page_errors = all_pages_errors_list[page_num]
            is_landscape = doc[page_num].rect.width > doc[page_num].rect.height

            # Kira isu aktif muka surat ini
            if bypass_all_doc:
                active_error_count = 0
            else:
                active_error_count = sum(
                    1
                    for i in range(len(unique_page_errors))
                    if f"p{page_num+1}_{i}"
                    not in st.session_state.get("ignored_errors", set())
                )

            # Paparkan status ikon pada Expander
            if active_error_count > 0:
                status_icon = f"⚠️ Ada Isu: {active_error_count}"
            elif unique_page_errors:
                status_icon = (
                    "👁️ Diabaikan (Bypassed)"  # Status khas jika isu diabaikan
                )
            else:
                status_icon = "✅ Baik / Disemak"

            tag_landscape = " [Landscape]" if is_landscape else ""

            with st.expander(
                f"Muka Surat {page_num + 1}{tag_landscape} - ({status_icon})"
            ):
                doc_page = doc[page_num]
                col_img, col_details = st.columns([1, 1])

                # =========================================================================
                # 📌 LUKIS KOTAK SEMPADAN SAIZ A4 KABUR (PAGE BORDER)
                # =========================================================================
                shape_a4 = doc_page.new_shape()
                shape_a4.draw_rect(doc_page.rect)
                shape_a4.finish(
                    color=(0.4, 0.4, 0.4), width=1, dashes="[4 4] 0"
                )
                shape_a4.commit()

                # 📌 LUKIS KOTAK MERAH (HANYA JIKA MASTER BYPASS TIDAK AKTIF)
                if not bypass_all_doc:
                    for i, err in enumerate(unique_page_errors):
                        err_id = f"p{page_num+1}_{i}"
                        if err_id not in st.session_state.get(
                            "ignored_errors", set()
                        ):

                            bboxes_to_draw = err.get("bboxes", [])
                            if not bboxes_to_draw and err.get("bbox"):
                                bboxes_to_draw = [err["bbox"]]

                            for b in bboxes_to_draw:
                                shape = doc_page.new_shape()
                                shape.draw_rect(b)
                                shape.finish(color=(1, 0, 0), width=1.5)
                                shape.commit()

                # Render imej pratonton
                pix = doc_page.get_pixmap(dpi=120)
                img = Image.frombytes(
                    "RGB", [pix.width, pix.height], pix.samples
                )

                with col_img:
                    img = lukis_garis_margin_gpsta(doc[page_num], img)
                    st.image(
                        img,
                        caption=f"Pratonton MS {page_num + 1}",
                        width="stretch",
                    )

                with col_details:
                    if bypass_all_doc:
                        st.info(
                            "🌐 Semua isu untuk muka surat ini telah diabaikan"
                            " (Master Bypass Aktif)."
                        )
                    elif not unique_page_errors:
                        # 🟢 HANYA PAPAR INI JIKA MUKA SURAT MEMANG TIADA ISU SEJAK ASAL
                        st.success(
                            "Muka surat ini mematuhi piawai GPPTA KV 2026"
                            " (Bebas ralat)."
                        )
                    else:
                        # 🟡 JIKA ADA ISU ASAL (SAMA ADA AKTIF ATAU DIBAIKAN/BYPASSED)
                        if active_error_count == 0:
                            st.info(
                                "💡 Semua isu pada muka surat ini telah"
                                " diabaikan."
                            )

                        st.write("**Senarai Isu Dikesan:**")
                        page_err_ids = []

                        for i, err in enumerate(unique_page_errors):
                            err_id = f"p{page_num+1}_{i}"
                            page_err_ids.append(err_id)
                            is_ignored = err_id in st.session_state.get(
                                "ignored_errors", set()
                            )

                            c_box, c_text = st.columns(
                                [1.2, 3], vertical_alignment="center"
                            )

                            with c_box:
                                st.checkbox(
                                    "Abaikan (Byp...",
                                    key=f"cb_{err_id}",
                                    value=is_ignored,
                                    on_change=toggle_bypass,
                                    args=(err_id,),
                                )

                            with c_text:
                                st.text_input(
                                    label=f"label_{err_id}",
                                    value=err["msg"],
                                    disabled=True,
                                    label_visibility="collapsed",
                                    key=f"txt_{err_id}",
                                )

                        if page_err_ids:
                            st.divider()

                            all_page_ignored = all(
                                eid
                                in st.session_state.get("ignored_errors", set())
                                for eid in page_err_ids
                            )
                            st.checkbox(
                                "Abaikan Semua Isu Muka Surat Ini (Bypass All)",
                                key=f"cb_all_p{page_num+1}",
                                value=all_page_ignored,
                                on_change=toggle_bypass_page,
                                args=(page_err_ids,),
                            )

        # =========================================================================
        # 📋 KAJI SELIDIK & MAKLUM BALAS PENGGUNA (DILETAKKAN SEBELUM JANA PDF)
        # =========================================================================
        st.markdown("---")
        st.subheader("📋 Kaji Selidik & Maklum Balas Pengguna")
        st.info("Sila luangkan masa 1 minit untuk menilai pengalaman penggunaan e-Semak PTA demi penambahbaikan berterusan.")

        st.link_button(
            "⭐ 1. Klik Di Sini Untuk Isi Borang Kaji Selidik",
            "https://forms.gle/C4sLEf1zmCrbneqT8",
            type="primary",
            width="stretch"
        )

        # Callback untuk menghantar Log Sesi 1 sebaik sahaja pengguna mentandakan checkbox
        def on_survey_check():
            if st.session_state.get("survey_completed_cb", False) and not st.session_state.get("logged_sesi_1", False):
                st.session_state.logged_sesi_1 = True

                try:
                    import winreg
                    env_type = "Local (Windows)"
                except ImportError:
                    env_type = "Online (Cloud)"

                saiz_mb = round(len(pdf_bytes) / (1024 * 1024), 2) if 'pdf_bytes' in locals() else 0.0
                start_t = st.session_state.get("upload_start_time", time.time() - 1)
                masa_proses = round(max(time.time() - start_t, 1.0), 2)

                hantar_log_penggunaan(
                    environment=f"{env_type} [Sesi 1: Klik Survey]",
                    filename=uploaded_file.name if uploaded_file else "Dokumen_PDF",
                    file_size_mb=saiz_mb,
                    processing_time_sec=masa_proses,
                    total_pages=len(doc),
                    total_errors=len(detected_issues)
                )

        st.checkbox(
            "✅ Saya telah / sedang mengisi borang kaji selidik di atas",
            key="survey_completed_cb",
            on_change=on_survey_check
        )

        # =========================================================================
        # 📄 SEKSYEN JANA & MUAT TURUN DOKUMEN AKHIR (SEKATAN KAJI SELIDIK)
        # =========================================================================
        st.markdown("---")
        st.subheader("📄 Jana & Muat Turun Dokumen Akhir")

        if not st.session_state.get("survey_completed_cb", False):
            st.warning("🔒 **Butang Jana Dokumen Terkunci:** Sila isi borang kaji selidik dan tandakan kotak pengesahan di atas terlebih dahulu untuk membuka kunci penjanaan laporan.")
        else:
            st.success("🔓 **Kunci Dibuka:** Terima kasih! Anda kini boleh menjana dan memuat turun dokumen akhir.")
            st.write(
                f"Jumlah isu aktif yang disahkan untuk dilaporkan: **{len(detected_issues)} isu**"
            )

            if st.button(
                "⚙️ Jana Dokumen PDF Akhir",
                type="primary",
                width="stretch",  # Pembetulan ralat parameter width='stretch'
            ):
                with st.spinner("Menjana kesemua variasi laporan PDF... Sila tunggu sebentar."):
                    # 1. Jana Laporan Ringkasan
                    st.session_state.report_pdf_bytes = generate_pdf_report(
                        detected_issues, len(doc)
                    )

                    # 2. Jana Laporan Visual Berkotak
                    st.session_state.annotated_pdf_bytes = generate_annotated_report(
                        doc, all_pages_errors_list, st.session_state.ignored_errors
                    )

                    # 3. Jana Laporan Audit Penuh (Side-by-Side dengan Garisan Pemisah)
                    st.session_state.full_audit_pdf_bytes = generate_full_audit_pdf(
                        doc, all_pages_errors_list, st.session_state.ignored_errors
                    )

                    # --- HANTAR LOG SESI 2 KE GOOGLE SHEETS ---
                    try:
                        import winreg
                        env_type = "Local (Windows)"
                    except ImportError:
                        env_type = "Online (Cloud)"

                    saiz_mb = round(len(pdf_bytes) / (1024 * 1024), 2) if 'pdf_bytes' in locals() else 0.0
                    start_t = st.session_state.get("upload_start_time", time.time() - 1)
                    masa_proses = round(max(time.time() - start_t, 1.0), 2)

                    hantar_log_penggunaan(
                        environment=f"{env_type} [Sesi 2: Jana PDF]",
                        filename=uploaded_file.name if uploaded_file else "Dokumen_PDF",
                        file_size_mb=saiz_mb,
                        processing_time_sec=masa_proses,
                        total_pages=len(doc),
                        total_errors=len(detected_issues)
                    )

                st.success("Kesemua 3 fail PDF telah sedia untuk dimuat turun!")

            # --- PAPARAN 3 BUTANG MUAT TURUN ---
            if (
                st.session_state.get("report_pdf_bytes") is not None
                and st.session_state.get("annotated_pdf_bytes") is not None
                and st.session_state.get("full_audit_pdf_bytes") is not None
            ):
                base_filename = get_base_filename(uploaded_file.name)

                name_summary = f"Laporan_Ringkasan ({base_filename}).pdf"
                name_visual = f"Laporan_Visual ({base_filename}).pdf"
                name_audit = f"Laporan_Audit_SideBySide ({base_filename}).pdf"

                col_down1, col_down2, col_down3 = st.columns(3)

                with col_down1:
                    btn_html_1 = create_download_button_html(
                        st.session_state.report_pdf_bytes,
                        name_summary,
                        "📥 1. Laporan Ringkasan (PDF)",
                        color="#2563eb",
                    )
                    st.markdown(btn_html_1, unsafe_allow_html=True)

                with col_down2:
                    btn_html_2 = create_download_button_html(
                        st.session_state.annotated_pdf_bytes,
                        name_visual,
                        "📥 2. Visual Berkotak (PDF)",
                        color="#059669",
                    )
                    st.markdown(btn_html_2, unsafe_allow_html=True)

                with col_down3:
                    btn_html_3 = create_download_button_html(
                        st.session_state.full_audit_pdf_bytes,
                        name_audit,
                        "📥 3. Audit Penuh Side-by-Side (PDF)",
                        color="#d97706",
                    )
                    st.markdown(btn_html_3, unsafe_allow_html=True)

        # ==================== BUTANG KEMBALI KE ATAS ====================
        st.markdown("---")
        components.html(
            """
            <div style="text-align: center; font-family: sans-serif;">
                <button id="scrollToTopBtn" style="
                    padding: 10px 24px;
                    background-color: #ffffff;
                    color: #31333F;
                    border: 1px solid #d4d6db;
                    border-radius: 8px;
                    font-weight: 600;
                    cursor: pointer;
                    box-shadow: 0px 2px 4px rgba(0,0,0,0.05);
                    transition: all 0.2s ease;
                ">
                    ⬆️ Kembali ke Atas
                </button>
            </div>

            <script>
            const btn = document.getElementById('scrollToTopBtn');
            btn.addEventListener('click', function() {
                const mainDoc = window.parent.document;
                const mainContainer = mainDoc.querySelector('[data-testid="stMain"]') 
                                            || mainDoc.querySelector('.main') 
                                            || window.parent;

                mainContainer.scrollTo({
                    top: 0,
                    behavior: 'smooth'
                });
            });
            </script>
            """,
            height=70
        )

# =========================================================
# PAPARKAN FOOTER MAKLUMAT (DI LUAR BLOK IF UPLOADED_FILE)
# =========================================================
# PERHATIKAN: Tiada sebarang tab / indentation di hadapan garisan ini!
paparkan_footer_maklumat()
