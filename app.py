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
import urllib.parse
from datetime import datetime, timezone, timedelta
import time
import threading
import requests
from supabase import create_client

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
        "dibenarkan_semak": True
    }

# =========================================================
# 1. TETAPAN HALAMAN & SESSION STATE (WAJIB DI ATAS)
# =========================================================
st.set_page_config(
    page_title="e-Semak PTA",
    page_icon="🔍",
    layout="wide"
)

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
    "cambriamath", "symbol", "mtextra", "math", 
    "wingdings", "webdings", "msmincho", "segoeui-symbol"
]

TABLE_PREFIX_REGEX = re.compile(r"^\s*(Table|Jadual)\s+\d+(\.\d+)*", re.IGNORECASE)
FIGURE_PREFIX_REGEX = re.compile(r"^\s*(Figure|Rajah)\s+\d+(\.\d+)*", re.IGNORECASE)
IN_TEXT_CITATION_REGEX = re.compile(r"^\s*(Figure|Rajah|Table|Jadual)\s+\d+(\.\d+)*\.\s", re.IGNORECASE)
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
    re.IGNORECASE
)

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
    pemetaan = {"pengguna": "Pengguna", "admin": "Admin", "super_admin": "Super Admin"}
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
            timeout=10.0
        )
        print(f"[Log Google Sheets] Status Penghantaran: {response.status_code}")
    except Exception as e:
        print(f"[Ralat Webhook Log]: {e}")

def hantar_log_penggunaan(environment, filename, file_size_mb, processing_time_sec, total_pages, total_errors):
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
    thread = threading.Thread(target=_proses_hantar_background, args=(data_log,))
    thread.start()

def paparkan_footer_maklumat():
    st.markdown("""
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
    """, unsafe_allow_html=True)

# =========================================================
# 4. LOGIK SUPABASE (BAKI & LESEN)
# =========================================================
def semak_dan_kemaskini_baki(user):
    tarikh_today = str(datetime.now().date())
    tarikh_last = str(user.get("tarikh_terakhir", ""))
    
    had_harian = user.get("had_harian", 5)
    baki = user.get("baki_semakan", had_harian)
    
    if tarikh_last != tarikh_today:
        baki = had_harian
        supabase.table("pengguna").update({
            "baki_semakan": had_harian,
            "tarikh_terakhir": tarikh_today
        }).eq("id", user["id"]).execute()

    status_lesen = str(user.get("status_lesen", "aktif")).strip().lower()
    tarikh_tamat = user.get("tarikh_tamat_lesen")

    # Auto tukar ke "tidak_aktif" jika tarikh tamat lesen sudah berlalu
    if tarikh_tamat and status_lesen == "aktif" and str(tarikh_tamat) < tarikh_today:
        status_lesen = "tidak_aktif"
        supabase.table("pengguna").update({
            "status_lesen": "tidak_aktif"
        }).eq("id", user["id"]).execute()

    return baki, status_lesen

def tolak_baki_semakan(user_id, baki_semasa):
    baki_baru = max(0, baki_semasa - 1)
    supabase.table("pengguna").update({
        "baki_semakan": baki_baru
    }).eq("id", user_id).execute()
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
                username = st.text_input("Nama Pengguna (Username)", placeholder="Masukkan username anda...")
                password = st.text_input("Kata Laluan", type="password", placeholder="Masukkan kata laluan...")
                submit_login = st.form_submit_button("🚀 Log Masuk Akses", type="primary", use_container_width=True)

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
                        if user_data["password_hash"] == hash_password(password):
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
                    "✨ Daftar Akaun Baharu", type="primary", use_container_width=True
                )

            if submit_register:
                new_user_bersih = new_user.strip()

                # Semakan Validasi Pendaftaran
                if not new_user_bersih or not new_pass or not confirm_pass:
                    st.warning("⚠️ Sila isi semua maklumat yang diperlukan.")
                elif " " in new_user_bersih:
                    st.warning("⚠️ Nama pengguna tidak boleh mengandungi ruang kosong.")
                elif len(new_pass) < 6:
                    st.warning(
                        "⚠️ Kata laluan mestilah mengandungi sekurang-kurangnya 6"
                        " aksara."
                    )
                elif new_pass != confirm_pass:
                    st.error("❌ Kata laluan dan pengesahan kata laluan tidak padan!")
                else:
                    # Semak kewujudan pengguna di Supabase
                    cek = (
                        supabase.table("pengguna")
                        .select("*")
                        .ilike("username", new_user_bersih)
                        .execute()
                    )
                    if cek.data:
                        st.error("⚠️ Nama pengguna ini telah berdaftar dalam sistem.")
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
                                "tarikh_terakhir": str(datetime.now().date()),
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

        st.markdown("""
            <div style="text-align: center; margin-top: 24px; color: #64748b; font-size: 0.8rem;">
                Hak Cipta © 2026 KV Nibong Tebal • Versi 1.1.2
            </div>
        """, unsafe_allow_html=True)

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
                    use_container_width=True,
                )

                if simpan_pass:
                    if not pass1 or not pass2:
                        st.error("⚠️ Sila isi kedua-dua ruangan kata laluan.")
                    elif pass1 != pass2:
                        st.error("❌ Kata laluan tidak padan!")
                    elif pass1 == "123456":
                        st.warning(
                            "⚠️ Sila gunakan kata laluan lain selain kata laluan"
                            " default '123456'."
                        )
                    elif len(pass1) < 6:
                        st.warning(
                            "⚠️ Kata laluan mestilah sekurang-kurangnya 6 aksara."
                        )
                    else:
                        try:
                            new_hash = hash_password(pass1)

                            # Kemaskini database & nyahaktifkan perlu_tukar_pass
                            supabase.table("pengguna").update({
                                "password_hash": new_hash,
                                "perlu_tukar_pass": False,
                            }).eq("username", user_data["username"]).execute()

                            # Kemaskini session tempatan
                            st.session_state["user"]["perlu_tukar_pass"] = False

                            st.success(
                                "🎉 Kata laluan berjaya dikemaskini! Memuat"
                                " naik sistem..."
                            )
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ Gagal kemaskini kata laluan: {str(e)}")

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
                use_container_width=True,
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
        f"Saya (Username: {current_user['username']}) ingin memohon pembaharuan/pengaktifan semula lesen bagi perisian *e-Semak PTA*.\n\n"
        "Terima kasih."
    )
    link_whatsapp = f"https://wa.me/60132224610?text={urllib.parse.quote(mesej_wa)}"
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
    st.info("Anda telah mencapai had semakan harian yang ditetapkan untuk akaun anda. Sila cuba lagi esok.")
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
            <div style="font-size: 0.95rem; color: #38bdf8; font-weight: 700; margin-top: 4px; display: flex; align-items: center;">
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

    if st.button("🚪 Log Keluar", type="secondary", use_container_width=True):
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
            default_fonts = ["Arial", "Arial-BoldMT", "ArialMT", "Arial-ItalicMT", "Arial-BoldItalicMT"]
        else:
            default_fonts = ["Arial", "Times New Roman"]

        # Ambil nilai input langsung dari user
        margin_left_mm = st.number_input("Margin Kiri (mm)", min_value=10.0, max_value=60.0, value=default_left, step=1.0)
        margin_right_mm = st.number_input("Margin Kanan (mm)", min_value=10.0, max_value=60.0, value=default_right, step=1.0)
        margin_top_mm = st.number_input("Margin Atas (mm)", min_value=10.0, max_value=60.0, value=default_top, step=1.0)
        margin_bottom_mm = st.number_input("Margin Bawah (mm)", min_value=10.0, max_value=60.0, value=default_bottom, step=1.0)

        available_font_options = ["Arial", "Arial-BoldMT", "ArialMT", "Arial-ItalicMT", "Arial-BoldItalicMT", "Times New Roman", "TimesNewRoman", "Calibri", "Garamond"]
        allowed_fonts = st.multiselect("Jenis Font Dibenarkan", options=available_font_options, default=default_fonts)
        semak_caption = st.checkbox("Aktifkan Semakan Format Tajuk Jadual & Rajah", value=True)
        abaikan_teks_dalam_gambar = st.checkbox("Abaikan Teks Dalam Gambar / Rajah", value=True)
        abaikan_appendix = st.checkbox("Abaikan Semakan Font pada Lampiran (Appendix)", value=True)
        abaikan_pagenum_appendix = st.checkbox("Abaikan Semakan No. M/S di Lampiran (Appendices)", value=True)

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

            st.dataframe(data_jadual, use_container_width=True, hide_index=True)
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

                if st.form_submit_button("💾 Simpan Perubahan", type="primary", use_container_width=True):
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
                    use_container_width=True,
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
                "🔄 Reset Kata Laluan (123456)", use_container_width=True
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

            if st.form_submit_button("✨ Cipta Akaun", use_container_width=True):
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

                if st.form_submit_button("💾 Simpan Tetapan Global", type="primary", use_container_width=True):
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

    if uploaded_file is None:
        st.markdown(
            """
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
        """Menjana PDF Audit Lanskap dengan sokongan halaman sambungan jika isu terlalu banyak."""
        output_pdf = fitz.open()

        for page_num in range(len(doc)):
            # 1. Tapis isu yang aktif bagi muka surat ini berdasarkan ID Dinamik (p{page_num+1}_{i})
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

                # Cetak senarai isu sehingga bawah panel (y_pos <= 540)
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
            for i, err in enumerate(errors):
                err_id = f"p{page_num+1}_{i}"
                if err.get("bbox") and err_id not in ignored_set:
                    page.draw_rect(err["bbox"], color=(1, 0, 0), width=1.5)

        out_buffer = io.BytesIO()
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

        def toggle_bypass(err_id):
            if err_id in st.session_state.ignored_errors:
                st.session_state.ignored_errors.remove(err_id)
            else:
                st.session_state.ignored_errors.add(err_id)

            st.session_state.report_pdf_bytes = None
            st.session_state.annotated_pdf_bytes = None
            st.rerun()

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
            
            # Ekstrak data imej/lukisan hanya jika tetapan diaktifkan (Optimumkan Prestasi)
            images_info = page.get_image_info() if abaikan_teks_dalam_gambar or semak_caption else []
            drawings = page.get_drawings() if semak_caption else []

            full_page_text = page.get_text()
            page_text_lower = full_page_text.lower()

            is_appendix_page = any(
                k in page_text_lower for k in ["appendix", "appendices", "lampiran"]
            )

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
                        if wx0 < 150 or wy0 < 120 or wy0 > (rect.height - 120):
                            has_pagenum_found = True
                            pagenum_bboxes.append((wx0, wy0, wx1, wy1))
                    else:
                        if wy0 > (rect.height - 100):  # Zon Footer
                            right_min = rect.width * 0.60  
                            if wx0 >= right_min:
                                has_pagenum_found = True
                                pagenum_bboxes.append((wx0, wy0, wx1, wy1))
                            else:
                                loc_name = "bawah tengah" if wx0 >= (rect.width * 0.33) else "bawah kiri"
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

                                if not is_math_font and not is_inside_image:
                                    font_matched = any(
                                        f.lower().replace(" ", "") in font_name_clean for f in allowed_fonts
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

            status_icon = "⚠️ Ada Isu" if has_active_errors else "✅ Baik / Disemak"
            tag_landscape = " [Landscape]" if is_landscape else ""

            with st.expander(
                f"Muka Surat {page_num + 1}{tag_landscape} - ({status_icon})"
            ):
                col_img, col_details = st.columns([1, 1])
                
                # Buat salinan berasingan untuk render pratonton visual (menghindari penumpukan garisan merah)
                doc_page = doc[page_num]

                # Lukis kotak ralat sementara hanya untuk ralat yang TIDAK DIABAIKAN
                for i, err in enumerate(unique_page_errors):
                    err_id = f"p{page_num+1}_{i}"
                    if err.get("bbox") and err_id not in st.session_state.ignored_errors:
                        # Guna shape overlay supaya tidak merosakkan struktur teks PDF asal
                        shape = doc_page.new_shape()
                        shape.draw_rect(err["bbox"])
                        shape.finish(color=(1, 0, 0), width=1.5)
                        shape.commit()

                # Render imej pratonton
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
                            all_page_ignored = all(
                                eid in st.session_state.ignored_errors for eid in page_err_ids
                            )
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
                use_container_width=True,  # Pembetulan ralat parameter width='stretch'
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
