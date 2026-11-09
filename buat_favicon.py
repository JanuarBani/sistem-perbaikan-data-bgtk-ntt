# buat_favicon.py
# Jalankan: python buat_favicon.py
from PIL import Image
import os

# Path logo sumber & tujuan
LOGO_PATH = 'static/images/logo.webp'
FAVICON_PATH = 'static/images/favicon.ico'

# Cek file logo ada
if not os.path.exists(LOGO_PATH):
    print(f"❌ Logo tidak ditemukan: {LOGO_PATH}")
    print("   Pastikan file logo.webp ada di folder static/images/")
    exit(1)

print(f"📂 Membaca logo: {LOGO_PATH}")
img = Image.open(LOGO_PATH)

print(f"📐 Ukuran asli: {img.size[0]}×{img.size[1]} px")

# Konversi ke RGBA (agar transparansi tetap terjaga)
if img.mode != 'RGBA':
    img = img.convert('RGBA')

# Buat favicon multi-size
sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

try:
    img.save(
        FAVICON_PATH,
        format='ICO',
        sizes=sizes
    )
    file_size = os.path.getsize(FAVICON_PATH)
    print(f"✅ Favicon berhasil dibuat: {FAVICON_PATH}")
    print(f"📦 Ukuran file: {file_size / 1024:.1f} KB")
    print(f"📏 Ukuran di dalam ICO: {', '.join(f'{w}×{h}' for w, h in sizes)}")
    print()
    print("💡 Selanjutnya: refresh browser (Ctrl+F5) untuk melihat favicon.")
except Exception as e:
    print(f"❌ Gagal membuat favicon: {e}")
