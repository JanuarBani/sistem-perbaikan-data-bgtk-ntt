# peserta/utils.py
import re
import unicodedata
from datetime import date, datetime


# peserta/utils.py
def parse_nip_nik(raw):
    """
    Parse kolom 'NIP/NIK' menjadi (nip, nik).
    Data sudah bersih (tanpa '/'):
      - 18 digit angka → NIP
      - 16 digit angka → NIK
      - kosong → (None, None)
      - panjang lain / ada karakter selain angka → (None, None)
    """
    if raw is None:
        return None, None

    # Konversi ke string, hapus spasi & karakter pemisah umum
    raw = str(raw).strip().replace(' ', '').replace('\t', '')
    if not raw:
        return None, None

    # Bersihkan tanda baca umum yang mungkin masih tersisa
    raw = raw.replace('.', '').replace('-', '')

    if not raw.isdigit():
        return None, None

    if len(raw) == 18:
        return raw, None
    if len(raw) == 16:
        return None, raw

    # Panjang tidak standar → return kosong, biar masuk laporan error
    return None, None


def derive_tanggal_lahir(nip, nik):
    """
    Derive tanggal lahir dari NIK (digit 7-12: DDMMYY) atau NIP (digit 1-8: YYYYMMDD).
    Prioritas: NIK (format KTP). Fallback ke NIP.
    """
    # Coba NIK dulu
    if nik and len(nik) == 16 and nik.isdigit():
        try:
            dd = int(nik[6:8])
            mm = int(nik[8:10])
            yy = int(nik[10:12])
            # Perempuan: dd + 40
            if dd > 40:
                dd -= 40
            # Asumsi abad: jika yy > 30 → 19xx, else 20xx
            year = 1900 + yy if yy > 30 else 2000 + yy
            return date(year, mm, dd)
        except (ValueError, IndexError):
            pass
    # Fallback ke NIP
    if nip and len(nip) >= 8 and nip[:8].isdigit():
        try:
            year = int(nip[0:4])
            month = int(nip[4:6])
            day = int(nip[6:8])
            return date(year, month, day)
        except (ValueError, IndexError):
            pass
    return None


GELAR_RE = re.compile(
    r',?\s*(S\.|M\.|Dr\.|Drs\.|Ir\.|Hj\.|H\.|Prof\.|A\.Ma\.|S\.Sos\.|S\.Kom\.|S\.Pd\.|M\.Pd\.|M\.Si\.|S\.Si\.|S\.E\.|M\.M\.|S\.H\.|M\.H\.|S\.T\.|M\.T\.).*$', re.IGNORECASE)


def normalisasi_nama(nama):
    """
    Return (nama_normalized, gelar).
    - Lowercase
    - Hapus gelar (S.Pd, M.Si, dll)
    - Trim spasi berlebih
    - Hapus tanda baca di akhir
    """
    if not nama:
        return '', ''
    nama = str(nama).strip()
    # Cari gelar
    match = GELAR_RE.search(nama)
    gelar = ''
    nama_inti = nama
    if match:
        gelar = nama[match.start():].strip(', ')
        nama_inti = nama[:match.start()].strip(', ')
    # Normalisasi
    nama_norm = nama_inti.lower()
    nama_norm = unicodedata.normalize('NFKD', nama_norm)
    nama_norm = re.sub(r'[^\w\s]', '', nama_norm)
    nama_norm = re.sub(r'\s+', ' ', nama_norm).strip()
    return nama_norm, gelar


def normalisasi_wa(nomor):
    """Normalisasi nomor WA ke format 628xxx."""
    if not nomor:
        return ''
    nomor = re.sub(r'[\s\-\(\)]', '', str(nomor))
    if nomor.startswith('+62'):
        return '62' + nomor[3:]
    if nomor.startswith('62'):
        return nomor
    if nomor.startswith('08'):
        return '62' + nomor[1:]
    return nomor


def generate_nomor_referensi():
    """Generate nomor referensi aktivasi: AKT-YYYY-NNNNNN."""
    from .models import PengajuanAktivasi
    year = datetime.now().year
    last = PengajuanAktivasi.objects.filter(
        nomor_referensi__startswith=f'AKT-{year}-'
    ).order_by('-id').first()
    if last:
        try:
            n = int(last.nomor_referensi.split('-')[-1]) + 1
        except (ValueError, IndexError):
            n = 1
    else:
        n = 1
    return f'AKT-{year}-{n:06d}'
