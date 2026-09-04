import base64
import io
import re
from fpdf import FPDF
from PIL import Image
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
from datetime import datetime, timezone, timedelta
import time
import threading
import requests

# URL Webhook Google Apps Script anda
GOOGLE_WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbzODbqg8fx4wduxDmbjhFdzzj_k6xkIsb0oMo9FR10UKkXs0tVmt6HyIakaLmmaSORc/exec"

def _proses_hantar_background(data_log):
    """Fungsi pembantu yang berjalan di latar belakang (background thread)."""
    try:
        # requests mengendalikan HTTP 302 Redirect Google Apps Script secara automatik
        response = requests.post(
            GOOGLE_WEBHOOK_URL,
            json=data_log,
            headers={"Content-Type": "application/json"},
            timeout=10.0  # Masa yang cukup untuk pelayan Google memproses
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
    """Menghantar log pemprosesan PDF terus ke Google Sheets di latar belakang secara senyap."""
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

    # Hantar log melalui Thread berasingan supaya UI aplikasi serta-merta lancar
    thread = threading.Thread(target=_proses_hantar_background, args=(data_log,))
    thread.start()

# =========================================================
# TETAPAN MAKLUMAT PENTADBIR & HAK CIPTA (SETEMPAT)
# =========================================================
def paparkan_footer_maklumat():
    """Fungsi setempat untuk memaparkan maklumat penyeragaman hak cipta & perhubungan."""
    st.markdown("""
    <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px 20px; margin-top: 30px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.02);">
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
    """, unsafe_allow_html=True)

# =========================================================
# 1. TETAPAN PEMBANGUN (DEVELOPER TESTING TOGGLES)
# =========================================================
# Set True/False mengikut apa yang anda nak uji:
DEV_BYPASS_LIMIT = False    # True = Abaikan had upload PDF (boleh upload unlimit)
DEV_BYPASS_EXPIRED = False  # True = Abaikan tarikh luput (lesen sentiasa aktif)

HAD_HARIAN = 30

# TETAPAN TARIKH & MASA LUPUT (Tahun, Bulan, Hari, Jam, Minit, Saat)
# Uji tarikh/masa tertentu di sini:
MASA_LUPUT = datetime(2026, 9, 8, 1, 00, 0) # Contoh: 8 Sept 2026, 1:00:00 AM

SECRET_KEY = "KVNT_MIPAC_2026_SECRET"
REG_PATH = r"Software\eSemakPTA\UsageData"

def jana_hash(jumlah, tarikh):
    raw_data = f"{jumlah}-{tarikh}-{SECRET_KEY}"
    return hashlib.sha256(raw_data.encode()).hexdigest()

# =========================================================
# 1. LOGIK REGISTRY & PEMERIKSAAN MASA (IMPROVED)
# =========================================================
def dapatkan_masa_sebenar():
    """Semak masa dari Internet dahulu, jika offline guna masa komputer."""
    try:
        url = 'http://worldtimeapi.org/api/ip'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
            masa_str = data['datetime'][:19]
            return datetime.fromisoformat(masa_str)
    except Exception:
        return datetime.now()

def baca_penggunaan_registry(masa_semasa):
    hari_ini = str(masa_sekarang.date())
    
    # Abaikan carian Windows Registry jika berjalan di Streamlit Cloud (Linux)
    if winreg is None:
        return {"jumlah": 0, "tarikh": hari_ini, "tamper_jam": False}

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
        jumlah, _ = winreg.QueryValueEx(key, "Jumlah")
        tarikh, _ = winreg.QueryValueEx(key, "Tarikh")
        
        # Semak LastRun secara selamat (elak ralat jika kunci belum wujud)
        try:
            last_run_str, _ = winreg.QueryValueEx(key, "LastRun")
            masa_terakhir = datetime.fromisoformat(last_run_str)
        except FileNotFoundError:
            masa_terakhir = None
            
        winreg.CloseKey(key)

        # 1. Semak Anti-Clock Rollback (Jam komputer diundurkan)
        if masa_terakhir and masa_semasa < masa_terakhir:
            return {"jumlah": 999, "tarikh": hari_ini, "tamper_jam": True}

        # Reset automatik jika bertukar hari
        if tarikh != hari_ini:
            return {"jumlah": 0, "tarikh": hari_ini, "tamper_jam": False}

        return {"jumlah": jumlah, "tarikh": tarikh, "tamper_jam": False}

    except FileNotFoundError:
        return {"jumlah": 0, "tarikh": hari_ini, "tamper_jam": False}

def simpan_penggunaan_registry(rekod, masa_semasa):
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        hash_baru = jana_hash(rekod["jumlah"], rekod["tarikh"])
        
        winreg.SetValueEx(key, "Jumlah", 0, winreg.REG_DWORD, rekod["jumlah"])
        winreg.SetValueEx(key, "Tarikh", 0, winreg.REG_SZ, str(rekod["tarikh"]))
        winreg.SetValueEx(key, "Hash", 0, winreg.REG_SZ, hash_baru)
        winreg.SetValueEx(key, "LastRun", 0, winreg.REG_SZ, masa_semasa.isoformat())
        
        winreg.CloseKey(key)
    except Exception as e:
        print(f"Ralat Registry: {e}")

# =========================================================
# 2. SEMAKAN KESELAMATAN & STATUS SIDEBAR
# =========================================================
masa_sekarang = dapatkan_masa_sebenar()
rekod_penggunaan = baca_penggunaan_registry(masa_sekarang)

# Simpan jejak LastRun setiap kali app dibuka (jika jam tidak diusik)
if not rekod_penggunaan.get("tamper_jam"):
    simpan_penggunaan_registry(rekod_penggunaan, masa_sekarang)

tarikh_luput_formatted = MASA_LUPUT.strftime("%d/%m/%Y %I:%M:%S %p")

# A. SEKATAN JIKA JAM DIUNDURKAN (ANTI-CLOCK ROLLBACK)
if not DEV_BYPASS_EXPIRED and rekod_penggunaan.get("tamper_jam"):
    st.error("🚨 **AMARAN KESELAMATAN: JAM SISTEM DIUBAH**")
    st.info("Sistem mengesan tarikh/masa komputer anda telah diundurkan secara tidak sah. Sila betulkan tetapan masa Windows anda.")
    st.stop()

# SEKATAN JIKA DAH EXPIRED
if not DEV_BYPASS_EXPIRED:
    if masa_sekarang > MASA_LUPUT:
        st.error("⏳ **LESEN PERISIAN TELAH TAMAT TEMPOH**")

        # 1. Sediakan mesej automatik WhatsApp
        mesej_wa = (
            "Assalamualaikum/Salam Sejahtera Ts. Muhammad Taufik,\n\n"
            "Saya ingin memohon pembaharuan lesen bagi perisian *e-Semak PTA*.\n"
            f"Lesen saya telah tamat tempoh pada: {tarikh_luput_formatted}.\n\n"
            "Terima kasih."
        )
        
        # 2. Encoded mesej supaya selamat digunakan dalam URL
        link_whatsapp = f"https://wa.me/60132224610?text={urllib.parse.quote(mesej_wa)}"

        # 3. Paparkan Maklumat
        st.info(
            f"Masa percubaan/lesen perisian ini telah tamat pada **{tarikh_luput_formatted}**.\n\n"
            "### 📞 Maklumat Perhubungan Pentadbir System:\n"
            "* **Pentadbir:** Ts. Muhammad Taufik Ramli\n"
            "* **Institusi:** Program Teknologi Elektronik, KV Nibong Tebal\n"
            "* **E-mel:** mtaufikramli@gmail.com / g-25076822@moe-dl.edu.my\n"
            "* **Tel:** +60 13-222 4610\n"
            f"* **WhatsApp Direct:** [💬 Klik Sini Untuk WhatsApp Pentadbir]({link_whatsapp})\n\n"
            "Sila hubungi pihak pentadbir di atas untuk pembaharuan lesen perisian."
        )
        
        paparkan_footer_maklumat()
        st.stop()

# C. SEMAK HAD HARIAN (Dikawal oleh DEV_BYPASS_LIMIT)
if not DEV_BYPASS_LIMIT:
    if rekod_penggunaan.get("jumlah") == 999:
        st.error("🚨 **KESELAMATAN TERGANGGU**: Rekod sistem telah diubah suai secara tidak sah!")
        st.stop()

    if rekod_penggunaan.get("jumlah", 0) >= HAD_HARIAN:
        st.error(f"🛑 **HAD SEMAKAN HARIAN TERCAPAI ({HAD_HARIAN}/{HAD_HARIAN})**")
        st.info(f"Anda telah mencapai had {HAD_HARIAN} kali semakan percuma untuk hari ini. Sila cuba lagi esok.")
        st.stop()

# Watermark Kredit Kekal di Sidebar
st.sidebar.markdown("---")
st.sidebar.markdown("**e-Semak PTA v1.1**")
# st.sidebar.markdown("Hak Cipta © 2026 KV Nibong Tebal")

# ==========================================
# ⚙️ TETAPAN AWAL APPLIKASI STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Semakan Format PTA KV (GPPTA 2026)",
    layout="wide",
    page_icon="📄"
)

st.markdown("""
    <style>
        html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
            scroll-behavior: smooth !important;
        }
    </style>
    <div id="top-anchor"></div>
""", unsafe_allow_html=True)

PASSWORD_RAHSIA = "KVNT2026"
APP_VERSION = "v1.1.2 (Optimized Layout & Appendix Fix)"

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False


def logout():
    st.session_state.authenticated = False
    st.rerun()


if not st.session_state.authenticated:
    st.markdown("<div id='top-of-page'></div>", unsafe_allow_html=True)
    
    # 1. HERO BANNER ATAS (BERGRADIENT BLUE)
    st.markdown(f"""
        <div style="background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%); padding: 24px 28px; border-radius: 16px; color: white; margin-bottom: 25px; box-shadow: 0 4px 15px rgba(30, 58, 138, 0.12);">
            <h1 style="margin: 0; font-size: 1.75rem; font-weight: 800; color: #ffffff; display: flex; align-items: center; gap: 10px;">
                📄 Semakan Format Laporan PTA (GPPTA KV)
            </h1>
            <p style="margin: 8px 0 0 0; opacity: 0.9; font-size: 0.88rem; font-weight: 500; color: #e0f2fe;">
                📌 <b>Versi Sistem:</b> {APP_VERSION}
            </p>
        </div>
    """, unsafe_allow_html=True)

    # 2. RUANG FORM LOG MASUK
    col_login, _ = st.columns([1.5, 1])
    with col_login:
        with st.form("login_form"):
            st.markdown("""
                <h3 style="margin: 0 0 10px 0; color: #1e293b; font-size: 1.15rem; font-weight: 700;">
                    🔒 Log Masuk Akses
                </h3>
            """, unsafe_allow_html=True)
            
            password_input = st.text_input(
                "Masukkan Kata Laluan Akses:",
                type="password",
                placeholder="Masukkan kata laluan di sini...",  # <--- Arahan ringkas dlm kotak
                help="Hubungi pentadbir jika anda terlupa kata laluan."
            )
            submit_button = st.form_submit_button(
                "🔑 Log Masuk", use_container_width=True, type="primary"
            )

            if submit_button:
                if password_input == PASSWORD_RAHSIA:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("🔑 Kata laluan salah. Sila cuba lagi!")

    st.markdown("<br>", unsafe_allow_html=True)

    # 3. KOTAK PENAFIAN (DISCLAIMER) DENGAN REKA BENTUK MODEN
    st.warning("""
    ### ⚠️ Penafian (Disclaimer) & Panduan Penggunaan Sistem

    1. **Alat Bantuan & Visual Interaktif:**
    Sistem ini berfungsi sebagai **penyemak automatik peringkat awal** untuk mengesan ralat *margin*, saiz/jenis fon, struktur muka surat, dan tajuk mengikut **GPPTA 2026**.

    2. **Kelulusan & Keputusan Mutlak:**
    Laporan audit dan paparan visual interaktif yang dijana adalah untuk **tujuan rujukan sahaja**. Keputusan akhir penetapan dan kelulusan format Laporan PTA adalah tertakluk sepenuhnya kepada **Penyelia, Panel Penilai, dan Jawatankuasa PTA KV Nibong Tebal**.

    3. **Kerahsiaan & Keselamatan Data:**
    Semua fail PDF yang dimuat naik diproses secara *in-memory* (sementara) dan **tidak disimpan dalam mana-mana pelayan (server) atau pangkalan data**. Dokumen anda kekal selamat dan rahsia.

    4. **Sokongan & Maklum Balas:**
    Jika anda mengesan sebarang ketidakselarian semakan atau ralat teknikal, sila hubungi pentadbir sistem menerusi maklumat perhubungan di bahagian bawah halaman.
    """)

    # 4. FOOTER HAK CIPTA
    paparkan_footer_maklumat()

    st.stop()

# ==================== SIDEBAR & TETAPAN ====================
with st.sidebar:
    # 1. KAD STATUS LESEN
    if DEV_BYPASS_EXPIRED or DEV_BYPASS_LIMIT:
        st.warning(
            "🛠️ **DEV MODE ACTIVE**\n"
            f"- Bypass Limit: `{'AKTIF' if DEV_BYPASS_LIMIT else 'OFF'}`\n"
            f"- Bypass Expired: `{'AKTIF' if DEV_BYPASS_EXPIRED else 'OFF'}`"
        )
    else:
        st.markdown(
            f"""
            <div style="background-color: #1e293b; padding: 12px 16px; border-radius: 10px; border: 1px solid #334155; margin-bottom: 16px;">
                <div style="font-size: 0.75rem; color: #94a3b8; font-weight: 600;">STATUS LESEN</div>
                <div style="font-size: 0.85rem; color: #38bdf8; font-weight: 700; margin-top: 2px;">
                    🗓️ Tamat: {tarikh_luput_formatted}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 2. INFO APLIKASI (SATU SAHAJA DI SINI)
    st.markdown(
        f"""
        <div style="margin-bottom: 12px;">
            <p style="margin: 2px 0 0 0; font-size: 0.8rem; color: #94a3b8;">Hak Cipta © 2026 KV Nibong Tebal</p>
            <p style="margin: 4px 0 0 0; font-size: 0.75rem; color: #f59e0b; font-weight: 600;">📌 Versi: {APP_VERSION}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 3. BUTANG LOG OUT
    if st.button("🚪 Log Out", type="secondary", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

    st.divider()

    # 4. TETAPAN TEMPLAT LAPORAN PTA
    st.markdown("<h4 style='font-size: 0.95rem; font-weight: 700; color: #ffffff;'>⚙️ Tetapan Templat Laporan PTA</h4>", unsafe_allow_html=True)

    default_left, default_right, default_top, default_bottom = 40.0, 25.0, 25.0, 25.0
    default_fonts = ["Arial", "Arial-BoldMT", "ArialMT"]

    preset = st.selectbox(
        "Pilih Templat Garis Panduan",
        ["GPPTA KV (Edisi Ketiga 2026)", "Custom (Manual)"],
    )

    if preset == "GPPTA KV (Edisi Ketiga 2026)":
        default_left, default_right, default_top, default_bottom = 40.0, 25.0, 25.0, 25.0
        default_fonts = [
            "Arial",
            "Arial-BoldMT",
            "ArialMT",
            "Arial-ItalicMT",
            "Arial-BoldItalicMT"
        ]
    else:
        default_left, default_right, default_top, default_bottom = 40.0, 25.0, 25.0, 25.0
        default_fonts = ["Arial", "Times New Roman"]

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

    available_font_options = [
        "Arial", 
        "Arial-BoldMT", 
        "ArialMT", 
        "Arial-ItalicMT", 
        "Arial-BoldItalicMT",
        "Times New Roman",
        "TimesNewRoman",
        "Calibri",
        "Garamond"
    ]

    allowed_fonts = st.multiselect(
        "Jenis Font Dibenarkan",
        options=available_font_options,
        default=default_fonts,
    )

    semak_caption = st.checkbox(
        "Aktifkan Semakan Format Tajuk Jadual & Rajah",
        value=True,
        help="Semak format tajuk jadual (di atas jadual) dan tajuk rajah (di bawah rajah) mengikut saiz font, jenis font, ketebalan (bold), dan susunan perkataan."
    )

    abaikan_teks_dalam_gambar = st.checkbox(
        "Abaikan Teks Dalam Gambar / Rajah",
        value=True,
        help="Abaikan ralat font untuk label atau teks yang bertindih di atas gambar/rajah."
    )

    abaikan_appendix = st.checkbox(
        "Abaikan Semakan Font pada Lampiran (Appendix)",
        value=True,
        help="Abaikan semakan jenis dan saiz font untuk semua muka surat di dalam bahagian Lampiran (Appendices)."
    )

    abaikan_pagenum_appendix = st.checkbox(
        "Abaikan Semakan No. M/S di Lampiran (Appendices)",
        value=True,
        help="Abaikan semakan kehadiran dan kedudukan nombor muka surat bermula dari tajuk Lampiran utama."
    )

st.markdown("""
    <style>
    /* ========================================================= */
    /* 1. LAYOUT UTAMA & PENGATURAN AM                            */
    /* ========================================================= */
    .stApp {
        background-color: #f8fafc;
    }

    /* ========================================================= */
    /* 2. SIDEBAR (DARK CORPORATE THEME)                        */
    /* ========================================================= */
    [data-testid="stSidebar"] {
        background-color: #0f172a !important; /* Dark Navy */
        border-right: 1px solid #1e293b !important;
    }

    /* Warna Teks, Tajuk & Label Sidebar */
    [data-testid="stSidebar"] h1, 
    [data-testid="stSidebar"] h2, 
    [data-testid="stSidebar"] h3, 
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] p {
        color: #f1f5f9 !important;
    }

    /* Teks Muted & Garisan Pemisah */
    [data-testid="stSidebar"] .stMarkdown small,
    [data-testid="stSidebar"] caption {
        color: #94a3b8 !important;
    }

    [data-testid="stSidebar"] hr {
        border-color: #334155 !important;
        margin: 16px 0 !important;
    }

    /* Selectbox / Dropdown Dalam Sidebar */
    [data-testid="stSidebar"] div[data-baseweb="select"] > div {
        background-color: #1e293b !important;
        border-color: #475569 !important;
        color: #ffffff !important;
        border-radius: 8px !important;
    }

    /* Butang Log Out Sidebar (Merah Crimson) */
    [data-testid="stSidebar"] .stButton > button {
        background-color: #991b1b !important;
        color: #ffffff !important;
        border: 1px solid #dc2626 !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        transition: all 0.2s ease !important;
    }

    [data-testid="stSidebar"] .stButton > button:hover {
        background-color: #dc2626 !important;
        border-color: #ef4444 !important;
        box-shadow: 0 4px 12px rgba(220, 38, 38, 0.3) !important;
    }

    /* ========================================================= */
    /* 3. HERO BANNER HEADER & CARDS                              */
    /* ========================================================= */
    .header-card {
        background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #2563eb 100%);
        padding: 28px 32px;
        border-radius: 16px;
        color: white;
        margin-bottom: 24px;
        box-shadow: 0 10px 15px -3px rgba(30, 58, 138, 0.2);
        position: relative;
        overflow: hidden;
    }
    .header-card h1 {
        color: #ffffff !important;
        font-size: 1.85rem !important;
        font-weight: 700 !important;
        margin: 0 !important;
        letter-spacing: -0.5px;
    }
    .header-card p {
        color: #93c5fd !important;
        font-size: 0.95rem;
        margin-top: 6px !important;
        margin-bottom: 0 !important;
        font-weight: 400;
    }

    /* Feature & Info Cards */
    .info-grid {
        display: flex;
        gap: 16px;
        margin-top: 20px;
        margin-bottom: 25px;
    }
    .info-card {
        flex: 1;
        background: #ffffff;
        padding: 16px 20px;
        border-radius: 12px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.02);
    }
    .info-card-title {
        font-size: 0.85rem;
        font-weight: 700;
        color: #1e293b;
        margin-bottom: 4px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .info-card-desc {
        font-size: 0.78rem;
        color: #64748b;
        margin: 0;
    }

    .feature-card {
        background-color: #ffffff;
        border-radius: 10px;
        padding: 16px 20px;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
        border: 1px solid #e2e8f0;
        height: 100%;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .feature-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.08);
    }

    .card-blue { border-left: 4px solid #2563eb !important; }
    .card-amber { border-left: 4px solid #d97706 !important; }
    .card-emerald { border-left: 4px solid #059669 !important; }

    /* ========================================================= */
    /* 4. FORM INPUT & FILE UPLOADER                             */
    /* ========================================================= */
    /* File Uploader Container */
    [data-testid="stFileUploader"] {
        background-color: #f0f9ff !important;
        border: 2px dashed #0284c7 !important;
        border-radius: 12px !important;
        padding: 16px !important;
        transition: all 0.3s ease !important;
    }
    [data-testid="stFileUploader"]:hover {
        background-color: #e0f2fe !important;
        border-color: #0369a1 !important;
        box-shadow: 0 4px 12px rgba(2, 132, 199, 0.15) !important;
    }
    [data-testid="stFileUploader"] label {
        color: #0369a1 !important;
        font-weight: 700 !important;
        font-size: 1rem !important;
    }

    /* Text Input Box */
    div[data-baseweb="input"] {
        background-color: #ffffff !important;
        border: 1.5px solid #94a3b8 !important;
        border-radius: 8px !important;
        padding: 2px 4px !important;
    }
    div[data-baseweb="input"]:focus-within {
        border-color: #2563eb !important;
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.2) !important;
    }
    input::placeholder {
        color: #94a3b8 !important;
        font-style: italic;
        font-size: 0.88rem;
    }

    /* ========================================================= */
    /* 5. GAYA EXPANDER DINAMIK (JEJAK IKON 🟠 & 🟢)            */
    /* ========================================================= */

    /* Muka Surat Ada Isu (Jejak Ikon 🟠) - Tukar Header Penuh Jadi Oren Light */
    div[data-testid="stExpander"]:has(span:contains("🟠")) {
        background-color: #fff7ed !important;
        border: 1px solid #fed7aa !important;
        border-left: 6px solid #f97316 !important;
        border-radius: 10px !important;
        margin-bottom: 12px !important;
    }

    div[data-testid="stExpander"]:has(span:contains("🟠")) summary {
        background-color: #fff7ed !important; /* <--- DI SINI: Dulu 'transparent', tukar ke '#fff7ed' */
        border-radius: 8px !important;
    }

    /* Muka Surat Baik / Disemak (Jejak Ikon 🟢) - Kekal Lutsinar / Asal */
    div[data-testid="stExpander"]:has(span:contains("🟢")) {
        background-color: transparent !important;
        border: 1px solid #e5e7eb !important;
        border-radius: 10px !important;
        margin-bottom: 12px !important;
    }

    div[data-testid="stExpander"]:has(span:contains("🟢")) summary {
        background-color: transparent !important;
    }

    /* ========================================================= */
    /* 6. FOOTER                                                 */
    /* ========================================================= */
    .custom-footer, .footer-card {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 16px 24px;
        text-align: center;
        margin-top: 30px;
        font-size: 0.82rem;
        color: #475569;
        box-shadow: 0 2px 4px rgba(0,0,0,0.02);
    }
    .custom-footer a, .footer-card a {
        color: #2563eb;
        text-decoration: none;
        font-weight: 600;
    }
    .custom-footer a:hover, .footer-card a:hover {
        text-decoration: underline;
    }
    </style>
""", unsafe_allow_html=True)

# --- HERO HEADER BANNER ---
st.markdown("""
    <div class="header-card">
        <h1>📄 Sistem Semakan Format Laporan PTA</h1>
        <p>Garis Panduan Pengurusan Projek Tahun Akhir (GPPTA KV 2026)</p>
    </div>
""", unsafe_allow_html=True)

uploaded_file = st.file_uploader(
    "Muat Naik Fail PDF Laporan PTA", 
    type=["pdf"],
    help="Sila muat naik fail PDF Laporan PTA untuk semakan format automatik."
)

if uploaded_file is None:
    st.markdown("""
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 16px; margin-top: 20px;">
            <div style="background-color: #ffffff; padding: 18px; border-radius: 12px; border: 1px solid #e2e8f0; border-left: 5px solid #2563eb; box-shadow: 0 2px 6px rgba(0,0,0,0.04);">
                <div style="font-weight: 700; color: #1e293b; font-size: 0.95rem; margin-bottom: 6px;">🔍 Semakan Automatik</div>
                <p style="margin: 0; color: #64748b; font-size: 0.82rem; line-height: 1.4;">Mengesan margin, saiz fon, tajuk, dan struktur muka surat mengikut piawaian GPPTA 2026.</p>
            </div>
            <div style="background-color: #ffffff; padding: 18px; border-radius: 12px; border: 1px solid #e2e8f0; border-left: 5px solid #d97706; box-shadow: 0 2px 6px rgba(0,0,0,0.04);">
                <div style="font-weight: 700; color: #1e293b; font-size: 0.95rem; margin-bottom: 6px;">⚡ Visual Interaktif</div>
                <p style="margin: 0; color: #64748b; font-size: 0.82rem; line-height: 1.4;">Paparan berkotak warna terus pada PDF untuk memudahkan pembetulan format.</p>
            </div>
            <div style="background-color: #ffffff; padding: 18px; border-radius: 12px; border: 1px solid #e2e8f0; border-left: 5px solid #059669; box-shadow: 0 2px 6px rgba(0,0,0,0.04);">
                <div style="font-weight: 700; color: #1e293b; font-size: 0.95rem; margin-bottom: 6px;">📊 Audit Laporan</div>
                <p style="margin: 0; color: #64748b; font-size: 0.82rem; line-height: 1.4;">Muat turun 3 jenis laporan analisis penuh serentak untuk rujukan penyelia/pelajar.</p>
            </div>
        </div>
    """, unsafe_allow_html=True)

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

MM_TO_PT = 72 / 25.4  # Nisbah 1 mm ke pt (~2.83465)

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

def generate_full_audit_pdf(doc, errors_per_page, ignored_errors):
    """Menjana PDF Audit Lanskap dengan sokongan halaman sambungan jika isu terlalu banyak."""
    output_pdf = fitz.open()

    for page_num in range(len(doc)):
        # 1. Tapis isu yang aktif bagi muka surat ini
        if isinstance(errors_per_page, list):
            raw_issues = errors_per_page[page_num] if page_num < len(errors_per_page) else []
        elif isinstance(errors_per_page, dict):
            raw_issues = errors_per_page.get(page_num, [])
        else:
            raw_issues = []

        page_issues = [
            err for err in raw_issues
            if isinstance(err, dict) and err.get("id") not in ignored_errors
        ]

        issue_index = 0
        total_issues = len(page_issues)
        is_first_subpage = True

        # Loop ini akan terus cipta muka surat baru selagi isu belum habis dipaparkan
        while True:
            # Cipta Halaman A4 Lanskap (842 x 595 pt)
            new_page = output_pdf.new_page(width=842, height=595)

            # --- PANEL KIRI (PDF Original / Info Sambungan) ---
            rect_left = fitz.Rect(10, 10, 410, 585)
            if is_first_subpage:
                # Papar pratonton muka surat PDF asal pada sub-halaman pertama
                new_page.show_pdf_page(rect_left, doc, page_num)
            else:
                # Jika halaman sambungan, buat kotak maklumat ringkas di sebelah kiri
                new_page.draw_rect(rect_left, color=(0.7, 0.7, 0.7), fill=(0.95, 0.95, 0.95), width=0.5)
                new_page.insert_text(
                    fitz.Point(50, 280), 
                    f"SAMBUNGAN SENARAI ISU\nMUKA SURAT {page_num + 1}", 
                    fontsize=14, 
                    color=(0.3, 0.3, 0.3)
                )

            # --- GARISAN PEMISAH (Kiri vs Kanan) ---
            new_page.draw_line(fitz.Point(420, 15), fitz.Point(420, 580), color=(0.2, 0.2, 0.2), width=1.5)

            # --- PANEL KANAN (Senarai Isu) ---
            panel_rect = fitz.Rect(430, 15, 827, 580)
            new_page.draw_rect(panel_rect, color=(0.85, 0.85, 0.85), fill=(0.98, 0.98, 0.98), width=0.5)

            # Tajuk Panel Kanan
            header_title = f"MUKA SURAT {page_num + 1} - SENARAI ISU DIKESAN"
            if not is_first_subpage:
                header_title += " (SAMBUNGAN)"
            
            new_page.insert_text(fitz.Point(445, 40), header_title, fontsize=11, color=(0.1, 0.3, 0.6))
            new_page.draw_line(fitz.Point(445, 48), fitz.Point(812, 48), color=(0.8, 0.8, 0.8), width=0.8)

            y_pos = 70

            # Jika tiada isu langsung pada muka surat ini
            if total_issues == 0:
                new_page.insert_text(fitz.Point(445, y_pos), "✅ Tiada isu dikesan pada muka surat ini.", fontsize=10, color=(0, 0.5, 0))
                break

            # Cetak senarai isu sehingga bawah panel (y_pos < 550)
            while issue_index < total_issues and y_pos <= 540:
                issue = page_issues[issue_index]

                # Ekstrak ayat isu spesifik UI
                ayat_isu = (
                    issue.get("text") or 
                    issue.get("msg") or 
                    issue.get("label") or 
                    issue.get("description") or 
                    "Ralat Format Margin / Teks"
                )
                ayat_isu = ayat_isu.replace("Abaikan (Bypass): ", "").strip()

                # Cetak Teks Isu
                new_page.insert_text(fitz.Point(445, y_pos), f"{issue_index + 1}. ⚠️ {ayat_isu}", fontsize=9.5, color=(0.8, 0.1, 0.1))
                
                y_pos += 22
                issue_index += 1

            # Jika semua isu muka surat ini dah selesai dicetak, keluar loop
            if issue_index >= total_issues:
                break
            
            # Jika masih ada isu berbaki, tandakan sub-page seterusnya
            is_first_subpage = False

    return output_pdf.write()

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
        
    # Tukar aksara berbaki yang tidak disokong kepada ASCII
    return text.encode("latin-1", "replace").decode("latin-1")

def generate_pdf_report(filtered_errors, total_pages):
    # --- FUNGSI PEMBERSIH AKSARA UNICODE ---
    def sanitize_text(text):
        if not isinstance(text, str):
            text = str(text)
        replacements = {
            "–": "-",  # En-dash ke hyphen biasa
            "—": "-",  # Em-dash ke hyphen biasa
            "‘": "'",  # Smart quote
            "’": "'",
            "“": '"',  # Smart double quote
            "”": '"',
            "…": "...",
            "•": "-",
        }
        for orig, repl in replacements.items():
            text = text.replace(orig, repl)
        return text.encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # Tajuk Utama
    pdf.set_font("Helvetica", "B", 16)
    title_str = sanitize_text("Laporan Semakan Format Laporan PTA (GPPTA KV 2026)")
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
            
            # 1. Ambil mesej asal
            raw_msg = item["msg"].replace("*", "")
            
            # 2. BERSIHKAN TEKS UNICODE KHAS DI SINI
            clean_issue_str = sanitize_text(raw_msg)
            
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
        for i, err in enumerate(errors):
            err_id = f"p{page_num+1}_{i}"
            if err["bbox"] and err_id not in ignored_set:
                page.draw_rect(err["bbox"], color=(1, 0, 0), width=1.5)

    out_buffer = io.BytesIO()
    annotated_doc.save(out_buffer)
    annotated_doc.close()
    return out_buffer.getvalue()

def get_base_filename(uploaded_filename):
    """Mengambil nama fail tanpa ekstensi .pdf."""
    base_name, _ = os.path.splitext(uploaded_filename)
    # Bersihkan aksara berisiko jika ada
    return base_name.strip()

def create_download_button_html(
    file_bytes, filename, button_text, color="#2563eb"
):
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

if uploaded_file is not None:
    # Semak had harian sebelum upload (HANYA JIKA BUKAN BYPASS LIMIT)
    if not DEV_BYPASS_LIMIT and rekod_penggunaan["jumlah"] >= HAD_HARIAN:
        st.error(f"🛑 **HAD SEMAKAN HARIAN TERCAPAI ({HAD_HARIAN}/{HAD_HARIAN})**")
        st.info(f"Anda telah mencapai had {HAD_HARIAN} kali semakan percuma untuk hari ini. Sila cuba lagi esok.")
        st.stop()

    pdf_bytes = uploaded_file.getvalue()
    if len(pdf_bytes) == 0:
        st.error("Fail PDF yang dimuat naik kelihatan kosong. Sila pilih fail lain.")
        st.stop()
        
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    # =========================================================
    # RESET MEMORI DOKUMEN BILA FAIL BAHARU / UMBAS SEMULA
    # =========================================================
    if "current_file_bytes" not in st.session_state or st.session_state.current_file_bytes != pdf_bytes:
        st.session_state.current_file_bytes = pdf_bytes
        st.session_state.upload_start_time = time.time()
        
        # Kosongkan memory PDF yang dijana sebelum ini
        st.session_state.report_pdf_bytes = None
        st.session_state.annotated_pdf_bytes = None
        st.session_state.survey_completed_cb = False
        st.session_state.logged_sesi_1 = False

    # Rekod penggunaan Registry (Simpan jika nama fail berbeza/kali pertama)
    if "last_uploaded_file" not in st.session_state or st.session_state.last_uploaded_file != uploaded_file.name:
        st.session_state.last_uploaded_file = uploaded_file.name
        rekod_penggunaan["jumlah"] += 1
        simpan_penggunaan_registry(rekod_penggunaan, masa_sekarang)
            
    st.success(f"Fail '{uploaded_file.name}' Berjaya Diimbas! Baki semakan harian: {HAD_HARIAN - rekod_penggunaan['jumlah']}")
    st.success(f"Jumlah muka surat: {len(doc)}")

    # Inisialisasi session state sokongan (jika belum ada)
    if "upload_start_time" not in st.session_state:
        st.session_state.upload_start_time = time.time()
    if "ignored_errors" not in st.session_state:
        st.session_state.ignored_errors = set()
    if "report_pdf_bytes" not in st.session_state:
        st.session_state.report_pdf_bytes = None
    if "annotated_pdf_bytes" not in st.session_state:
        st.session_state.annotated_pdf_bytes = None

    def toggle_bypass(err_id):
        if err_id in st.session_state.ignored_errors:
            st.session_state.ignored_errors.remove(err_id)
        else:
            st.session_state.ignored_errors.add(err_id)

        st.session_state.report_pdf_bytes = None
        st.session_state.annotated_pdf_bytes = None
        st.rerun()

    def toggle_bypass_page(page_err_ids):
        # Semak sama ada semua isu dalam ms ini sudah di-ignore
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

    for page_num in range(len(doc)):
        page = doc[page_num]
        rect = page.rect
        blocks = page.get_text("dict")["blocks"]
        images_info = page.get_image_info()
        drawings = page.get_drawings()

        full_page_text = page.get_text()
        page_text_lower = full_page_text.lower()

        is_appendix_page = any(
            k in page_text_lower
            for k in ["appendix", "appendices", "lampiran"]
        )

        has_list_header = any(
            k in page_text_lower
            for k in [
                "list of tables",
                "list of figures",
                "senarai jadual",
                "senarai rajah",
                "table of contents",
                "kandungan",
            ]
        )

        has_dot_leaders = bool(DOT_LEADER_REGEX.search(full_page_text))
        is_list_page = has_list_header or (is_previous_list_page and has_dot_leaders)
        is_previous_list_page = is_list_page

        is_landscape = rect.width > rect.height
        page_errors = []

        if is_landscape:
            cur_m_top = MARGIN_LEFT_PT
            cur_m_bottom = rect.height - MARGIN_RIGHT_PT
            cur_m_left = MARGIN_TOP_PT
            cur_m_right = rect.width - MARGIN_BOTTOM_PT
        else:
            cur_m_top = MARGIN_TOP_PT
            cur_m_bottom = rect.height - MARGIN_BOTTOM_PT
            cur_m_left = MARGIN_LEFT_PT
            cur_m_right = rect.width - MARGIN_RIGHT_PT

        # PASS 1: PRE-SCANNING NOMBOR MUKA SURAT
        pagenum_bboxes = []
        has_pagenum_found = False

        words = page.get_text("words")
        for w in words:
            wx0, wy0, wx1, wy1, word_str = w[0], w[1], w[2], w[3], w[4]
            clean_w = re.sub(r"[^a-zA-Z0-9]", "", word_str.lower())

            is_valid_num = clean_w.isdigit() or is_roman_numeral(clean_w)

            if is_valid_num:
                if is_landscape:
                    # Kawasan Landscape
                    if wx0 < 150 or wy0 < 120 or wy0 > (rect.height - 120):
                        has_pagenum_found = True
                        pagenum_bboxes.append((wx0, wy0, wx1, wy1))
                else:
                    # Kawasan Portrait (GP PTA 2026: Bawah Penjuru Sebelah Kanan)
                    if wy0 > (rect.height - 100):  # Berada di zon footer
                        right_min = rect.width * 0.60  

                        if wx0 >= right_min:
                            has_pagenum_found = True
                            pagenum_bboxes.append((wx0, wy0, wx1, wy1))
                        else:
                            loc_name = (
                                "bawah tengah" if wx0 >= (rect.width * 0.33) else "bawah kiri"
                            )
                            page_errors.append(
                                {
                                    "msg": f"Nombor muka surat '{word_str}' berada di kedudukan tidak sah ({loc_name}). GP PTA 2026 mewajibkan di bahagian bawah penjuru sebelah kanan.",
                                    "bbox": (wx0, wy0, wx1, wy1),
                                }
                            )

        # PASS 2: SEMAKAN MARGIN & TEKS
        for b in blocks:
            if "lines" in b:
                for line in b["lines"]:
                    full_line_text = "".join(
                        [s["text"] for s in line["spans"]]
                    ).strip()

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

                        # Semakan Ralat Margin
                        if y1 > (cur_m_bottom + 2):
                            page_errors.append(
                                {
                                    "msg": f"Luar Margin Bawah: '{full_line_text[:20]}...'",
                                    "bbox": bbox,
                                }
                            )

                        if y0 < (cur_m_top - 2):
                            page_errors.append(
                                {
                                    "msg": f"Luar Margin Atas: '{full_line_text[:20]}...'",
                                    "bbox": bbox,
                                }
                            )

                        # Semakan Jenis & Saiz Font
                        skip_font_check = in_appendix_section or (abaikan_appendix and is_appendix_page)

                        if not skip_font_check:
                            font_name_clean = font_name.lower().replace(" ", "")
                            is_math_font = any(
                                mf in font_name_clean for mf in MATH_SYMBOL_FONTS
                            )

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

                            if not is_math_font and not is_inside_image:
                                font_matched = any(
                                    f.lower().replace(" ", "") in font_name_clean
                                    for f in allowed_fonts
                                )
                                if not font_matched and len(text) > 3:
                                    page_errors.append(
                                        {
                                            "msg": f"Jenis font tidak sah ({font_name}): '{text[:25]}...'",
                                            "bbox": bbox,
                                        }
                                    )

                                # Piawai Saiz Font GPPTA KV 2026
                                if len(text) > 5:
                                    if size < 8.5:
                                        page_errors.append(
                                            {
                                                "msg": f"Saiz font terlalu kecil ({size}pt): '{text[:25]}...'",
                                                "bbox": bbox,
                                            }
                                        )
                                    elif 14.5 < size < 20.0:
                                        page_errors.append(
                                            {
                                                "msg": f"Saiz font melebihi had tajuk PTA ({size}pt): '{text[:25]}...'",
                                                "bbox": bbox,
                                            }
                                        )

                        # Semakan Tajuk Jadual / Rajah
                        if semak_caption and not is_list_page:
                            is_dot_leader_line = bool(DOT_LEADER_REGEX.search(full_line_text))
                            is_sentence = bool(VERB_KEYWORDS_REGEX.search(full_line_text))
                            is_in_text_citation = bool(IN_TEXT_CITATION_REGEX.match(full_line_text))

                            if (
                                TABLE_PREFIX_REGEX.match(full_line_text)
                                and not is_sentence
                                and not is_dot_leader_line
                                and not is_in_text_citation
                            ):
                                table_below_close = False
                                for d in drawings:
                                    d_y0 = d["rect"][1]
                                    if 0 < (d_y0 - y1) < 50:
                                        table_below_close = True
                                        break

                                if not table_below_close:
                                    page_errors.append(
                                        {
                                            "msg": f"Kedudukan Tajuk Jadual Salah (Mesti Di Atas Jadual): '{full_line_text[:35]}...'",
                                            "bbox": bbox,
                                        }
                                    )

                                clean_title_text = full_line_text.strip()
                                if re.match(r"^(Table|Jadual)\s+\d+(\.\d+)*\.?$", clean_title_text, re.IGNORECASE):
                                    page_errors.append(
                                        {
                                            "msg": f"Format Tajuk Jadual Terlangkau/Terpisah Baris: '{clean_title_text}' (Sepatutnya sebaris dengan penerangan)",
                                            "bbox": bbox,
                                        }
                                    )

                            elif (
                                FIGURE_PREFIX_REGEX.match(full_line_text)
                                and not is_sentence
                                and not is_dot_leader_line
                                and not is_in_text_citation
                            ):
                                image_above_close = False
                                image_below_close = False

                                for img in images_info:
                                    img_y0 = img["bbox"][1]
                                    img_y1 = img["bbox"][3]

                                    if 0 < (y0 - img_y1) < 50:
                                        image_above_close = True

                                    if 0 < (img_y0 - y1) < 30:
                                        image_below_close = True

                                if image_below_close and not image_above_close:
                                    page_errors.append(
                                        {
                                            "msg": f"Kedudukan Tajuk Rajah Salah (Mesti Di Bawah Rajah): '{full_line_text[:35]}...'",
                                            "bbox": bbox,
                                        }
                                    )

        # SEMAKAN KEHADIRAN NOMBOR MUKA SURAT
        if not in_appendix_section:
            lines = [line.strip().upper() for line in full_page_text.split("\n") if line.strip()]
            for line in lines:
                if (line.startswith("APPENDIX") or line.startswith("LAMPIRAN")) and len(line) < 60:
                    in_appendix_section = True
                    break

        is_other_exempted = any(
            k in page_text_lower for k in ["list of publications", "publication"]
        )

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
            if e["msg"] not in seen_msgs:
                seen_msgs.add(e["msg"])
                unique_page_errors.append(e)

        all_pages_errors_list.append(unique_page_errors)

        # Kumpul ralat aktif untuk laporan
        for i, err in enumerate(unique_page_errors):
            err_id = f"p{page_num+1}_{i}"
            if err_id not in st.session_state.ignored_errors:
                detected_issues.append({"page": page_num + 1, "msg": err["msg"]})

    # =========================================================================
    # 🔍 PRATONTON VISUAL PER MUKA SURAT
    # =========================================================================
    st.markdown("---")
    st.write(
        f"Jumlah isu aktif yang disahkan untuk dilaporkan: **{len(detected_issues)} isu**"
    )
    st.subheader("🔍 Mod Semakan & Pratonton Visual")

    for page_num in range(len(doc)):
        unique_page_errors = all_pages_errors_list[page_num]
        is_landscape = doc[page_num].rect.width > doc[page_num].rect.height
        
        has_active_errors = any(
            f"p{page_num+1}_{i}" not in st.session_state.ignored_errors
            for i in range(len(unique_page_errors))
        )

        tag_landscape = " [Landscape]" if is_landscape else ""

        # 1. Tentukan teks status dan ikon penanda CSS
        if has_active_errors:
            status_text = "⚠️ Ada Isu"
            exp_icon = "🟠"
        else:
            status_text = "✅ Baik / Disemak"
            exp_icon = "🟢"

        exp_label = f"Muka Surat {page_num + 1}{tag_landscape} - ({status_text})"

        # 2. Buka Expander (Gaya dipicu secara automatik oleh exp_icon)
        with st.expander(exp_label, icon=exp_icon):
            col_img, col_details = st.columns([1, 1])
            doc_page = doc[page_num]

            for i, err in enumerate(unique_page_errors):
                err_id = f"p{page_num+1}_{i}"
                if (
                    err["bbox"]
                    and err_id not in st.session_state.ignored_errors
                ):
                    doc_page.draw_rect(
                        err["bbox"], color=(1, 0, 0), width=1.5
                    )

            pix = doc_page.get_pixmap(dpi=120)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            with col_img:
                st.image(
                    img,
                    caption=f"Pratonton MS {page_num + 1}",
                    use_container_width=True,
                )

            with col_details:
                if not unique_page_errors:
                    st.success("Muka surat ini mematuhi piawai GPPTA KV 2026 (Bebas ralat).")
                else:
                    st.write("**Senarai Isu Dikesan:**")
                    page_err_ids = []
                    
                    for i, err in enumerate(unique_page_errors):
                        err_id = f"p{page_num+1}_{i}"
                        page_err_ids.append(err_id)
                        is_ignored = err_id in st.session_state.ignored_errors

                        st.checkbox(
                            f"Abaikan (Bypass): {err['msg']}",
                            key=f"cb_{err_id}",
                            value=is_ignored,
                            on_change=toggle_bypass,
                            args=(err_id,),
                        )

                    if page_err_ids:
                        st.markdown("---")
                        all_page_ignored = all(eid in st.session_state.ignored_errors for eid in page_err_ids)
                        st.checkbox(
                            "🚫 **Abaikan Semua (Bypass Page Ini)**",
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
        use_container_width=True
    )

    # Callback untuk menghantar Log Sesi 1 sebaik sahaja pengguna mentandakan checkbox
    def on_survey_check():
        if st.session_state.survey_completed_cb and not st.session_state.get("logged_sesi_1", False):
            st.session_state.logged_sesi_1 = True
            
            try:
                import winreg
                env_type = "Local (Windows)"
            except ImportError:
                env_type = "Online (Cloud)"

            saiz_mb = round(len(uploaded_file.getvalue()) / (1024 * 1024), 2)
            start_t = st.session_state.get("upload_start_time", time.time() - 1)
            masa_proses = round(max(time.time() - start_t, 1.0), 2)

            hantar_log_penggunaan(
                environment=f"{env_type} [Sesi 1: Klik Survey]",
                filename=uploaded_file.name,
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
            width="stretch",
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

                saiz_mb = round(len(uploaded_file.getvalue()) / (1024 * 1024), 2)
                start_t = st.session_state.get("upload_start_time", time.time() - 1)
                masa_proses = round(max(time.time() - start_t, 1.0), 2)

                hantar_log_penggunaan(
                    environment=f"{env_type} [Sesi 2: Jana PDF]",
                    filename=uploaded_file.name,
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
            # Dapatkan nama asas fail asal (contoh: 'LAPORAN_PTA_AKMAL_DANI')
            base_filename = get_base_filename(uploaded_file.name)

            # Bina nama fail baharu mengikut kategori
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
# PAPARKAN FOOTER MAKLUMAT (SENTIASA DI BARIS PALING AKHIR)
# =========================================================
paparkan_footer_maklumat()
