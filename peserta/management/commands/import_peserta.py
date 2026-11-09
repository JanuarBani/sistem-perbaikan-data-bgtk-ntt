# peserta/management/commands/import_peserta.py
import re
import unicodedata
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from openpyxl import load_workbook, Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from datetime import datetime
from collections import Counter
from peserta.models import Peserta, Perbaikan
from peserta.utils import parse_nip_nik, derive_tanggal_lahir, normalisasi_nama


TARGET_SHEET_NAME = 'Sheet1'

EXCEL_ERROR_TOKENS = {
    '#n/a', '#value!', '#ref!', '#div/0!', '#name?', '#null!',
    '#num!', '#spill!', '#calc!', '#getting_data',
    'n/a',
}


def _norm_key(s):
    return ' '.join(str(s).lower().split())


def _digits_only(s):
    return ''.join(c for c in str(s) if c.isdigit())


def _is_excel_error(s):
    """True jika string merupakan error token Excel (mis. '#N/A')."""
    if s is None:
        return False
    return str(s).strip().lower() in EXCEL_ERROR_TOKENS


class Command(BaseCommand):
    help = (
        'Import data peserta dari sheet "Sheet1". '
        'Perbaikan X diterapkan ke Peserta SEKALIGUS dicatat di tabel Perbaikan '
        '(status approved, nilai_lama & nilai_baru tersimpan). '
        'Rekening disimpan ke field terenkripsi Peserta. '
        'Sel berisi error Excel (#N/A, #REF!, dll) diperlakukan kosong.'
    )

    COLUMN_CANDIDATES = {
        'Nama':                   ['Nama'],
        'NIP/NIK':                ['NIP/NIK', 'NIP / NIK', 'NIP-NIK', 'NIP NIK'],
        'NIP':                    ['NIP'],
        'NIK':                    ['NIK'],
        'NUPTK':                  ['NUPTK'],
        'Tempat Tugas':           ['Tempat Tugas'],
        'NPSN':                   ['NPSN'],
        'Kecamatan':              ['Kecamatan'],
        'Kabupaten':              ['Kabupaten'],
        'Provinsi':               ['Provinsi'],
        'Jenjang':                ['Jenjang'],
        'Jabatan':                ['Jabatan'],
        'Perbaikan NUPTK':        ['Perbaikan NUPTK'],
        'Perbaikan NIK':          ['Perbaikan NIK'],
        'Perbaikan Nama':         ['Perbaikan Nama'],
        'Perbaikan Tempat Tugas': ['Perbaikan Tempat Tugas'],
        'Nomor Rekening':         ['Nomor Rekening', 'No Rekening', 'No. Rekening', 'NoRek', 'Rekening'],
        'Bank':                   ['Bank', 'Nama Bank'],
        'Atas Nama Rekening':     ['Atas Nama Rekening', 'Atas Nama', 'Pemilik Rekening', 'Nama Pemilik Rekening'],
    }

    def add_arguments(self, parser):
        parser.add_argument('file', type=str, help='Path ke file .xlsx')
        parser.add_argument('--dry-run', action='store_true',
                            help='Cek data tanpa simpan ke database')
        parser.add_argument('--report', type=str, default=None,
                            help='Path output laporan Excel')
        parser.add_argument('--expected', type=int, default=None,
                            help='Total baris yang diharapkan (warning jika berbeda)')

    # ============================================================
    def handle(self, *args, **options):
        path = options['file']
        dry_run = options['dry_run']
        report_path = options['report']
        expected = options.get('expected')

        self.stdout.write(f'Membaca file: {path}')
        if dry_run:
            self.stdout.write(self.style.WARNING(
                'MODE DRY-RUN — tidak menyimpan ke DB\n'))

        wb_in = load_workbook(path, read_only=True, data_only=True)
        ws_in = self._pilih_sheet(wb_in)
        self.stdout.write(self.style.SUCCESS(
            f'📄 Sheet dibaca: "{ws_in.title}"\n'))

        rows = list(ws_in.iter_rows(values_only=True))
        if not rows:
            raise CommandError('File kosong.')

        # === HEADER ===
        header_raw = [str(c).strip() if c else '' for c in rows[0]]
        col_idx = {}
        for i, h in enumerate(header_raw):
            if not h:
                continue
            key = _norm_key(h)
            if key not in col_idx:
                col_idx[key] = i

        def cari_header(*kandidat):
            for nama in kandidat:
                idx = col_idx.get(_norm_key(nama))
                if idx is not None:
                    return idx
            return None

        COL_MAP = {f: cari_header(*c)
                   for f, c in self.COLUMN_CANDIDATES.items()}

        self.stdout.write('Kolom terdeteksi:')
        for k, v in COL_MAP.items():
            if v is not None:
                self.stdout.write(
                    f'  ✓ {k:<24} → kolom {get_column_letter(v+1)} ("{header_raw[v]}")'
                )
            else:
                self.stdout.write(f'  ✗ {k:<24} (tidak ada)')
        self.stdout.write('')

        if all(v is None for v in COL_MAP.values()):
            raise CommandError(
                'Tidak ada kolom yang dikenali. Cek header sheet.')

        # === STATISTIK ===
        stats = {
            'total': 0, 'nip_only': 0, 'nik_only': 0, 'kosong': 0,
            'panjang_aneh': 0, 'nama_kosong': 0,
            'duplikat_nip': 0, 'duplikat_nik': 0,
            'dengan_rekening': 0,
            'perbaikan_nama': 0, 'perbaikan_nik': 0,
            'perbaikan_nuptk': 0, 'perbaikan_tempat': 0,
            'kosong_baris': 0, 'perbaikan_records': 0,
            'error_cells_skipped': 0,
        }

        # =========================================================
        # FUNGSI PEMBERSIH KARAKTER (INVISIBLE, HTML, MATH, BOLD)
        # =========================================================
        def clean_text(text, key=''):
            if not text:
                return ''
            s = str(text)

            # 1. Ubah huruf matematika/tebal/miring/fullwidth menjadi ASCII normal
            s = unicodedata.normalize('NFKC', s)

            # 2. Hapus tag HTML (<b>, <i>, <span>, dll)
            s = re.sub(r'<[^>]+>', '', s)

            # 3. Hapus karakter tak terlihat (zero-width space, dsb)
            s = re.sub(r'[\u200b\u200c\u200d\ufeff\xa0]', ' ', s)

            # 4. Bersihkan berdasarkan jenis kolom
            if key in ['NIP', 'NIK', 'NUPTK', 'Nomor Rekening']:
                # Paksa hanya berisi angka 0-9
                s = re.sub(r'[^0-9]', '', s)
            elif key == 'NIP/NIK':
                # Hanya izinkan angka dan slash (untuk pemisah)
                s = re.sub(r'[^0-9/]', '', s)
            else:
                # Teks biasa: hapus karakter kontrol & rapikan spasi
                s = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', s)
                s = ' '.join(s.split())

            return s.strip()

        def val(row, key):
            i = COL_MAP.get(key)
            if i is None or i >= len(row):
                return ''
            v = row[i]
            if v is None:
                return ''

            # Bersihkan nilai
            s = clean_text(v, key=key)

            # Cek error token Excel
            if _is_excel_error(s):
                stats['error_cells_skipped'] += 1
                return ''
            return s

        def row_has_data(row):
            for v in row:
                if v is None:
                    continue
                s = str(v).strip()
                if not s or _is_excel_error(s):
                    continue
                return True
            return False

        errors = []
        panjang_aneh_list = []
        nip_occurrences, nik_occurrences = {}, {}
        peserta_objs = []
        perbaikan_batch = []
        rekening_records = []
        perbaikan_records = []

        for line_no, row in enumerate(rows[1:], start=2):
            if not row_has_data(row):
                stats['kosong_baris'] += 1
                continue
            stats['total'] += 1

            try:
                # ---- Data asli ----
                nama_asli = val(row, 'Nama')
                nuptk_asli = val(row, 'NUPTK')
                tempat_asli = val(row, 'Tempat Tugas')
                nip_nik_raw = val(row, 'NIP/NIK')

                nip, nik = parse_nip_nik(nip_nik_raw)
                nip_kolom, nik_kolom = val(row, 'NIP'), val(row, 'NIK')
                if nip_kolom and not nip:
                    nip = nip_kolom[:20]
                if nik_kolom and not nik:
                    nik = nik_kolom[:20]

                # ---- Perbaikan (override) ----
                perbaikan_nama = val(row, 'Perbaikan Nama')
                perbaikan_nik = val(row, 'Perbaikan NIK')
                perbaikan_nuptk = val(row, 'Perbaikan NUPTK')
                perbaikan_tempat = val(row, 'Perbaikan Tempat Tugas')

                nip_before, nik_before = nip, nik
                if perbaikan_nik:
                    stats['perbaikan_nik'] += 1
                    nip, nik = self._apply_perbaikan_nik(
                        perbaikan_nik, nip, nik)

                nama = perbaikan_nama or nama_asli
                if perbaikan_nama:
                    stats['perbaikan_nama'] += 1
                if not nama:
                    stats['nama_kosong'] += 1
                    nama = '(tanpa nama)'

                nuptk = perbaikan_nuptk or nuptk_asli or None
                if perbaikan_nuptk:
                    stats['perbaikan_nuptk'] += 1

                tempat_tugas = perbaikan_tempat or tempat_asli
                if perbaikan_tempat:
                    stats['perbaikan_tempat'] += 1

                # ---- Deteksi panjang aneh ----
                raw_cek = perbaikan_nik or nip_nik_raw
                if raw_cek and not (nip or nik):
                    stats['panjang_aneh'] += 1
                    kat, saran, status = self._analisis_masalah(raw_cek)
                    panjang_aneh_list.append(
                        (line_no, nama, raw_cek, len(raw_cek),
                         kat, saran, status)
                    )

                # ---- Klasifikasi ----
                if nip:
                    stats['nip_only'] += 1
                    nip_occurrences.setdefault(nip, []).append(
                        (line_no, nama, nip_nik_raw or perbaikan_nik))
                elif nik:
                    stats['nik_only'] += 1
                    nik_occurrences.setdefault(nik, []).append(
                        (line_no, nama, nip_nik_raw or perbaikan_nik))
                else:
                    stats['kosong'] += 1

                tgl = derive_tanggal_lahir(nip, nik)
                nama_norm, gelar = normalisasi_nama(nama)

                # ---- Rekening ----
                nomor_rek = val(row, 'Nomor Rekening')
                bank = val(row, 'Bank')
                atas_nama = val(row, 'Atas Nama Rekening')
                if nomor_rek:
                    stats['dengan_rekening'] += 1
                    rekening_records.append({
                        'line': line_no, 'nama': nama,
                        'nomor_rekening': nomor_rek,
                        'bank': bank, 'atas_nama': atas_nama,
                    })

                # ---- Susun objek Peserta ----
                p = Peserta(
                    nip_nik_raw=nip_nik_raw or perbaikan_nik or '',
                    nip=nip, nik=nik, nuptk=nuptk,
                    nama=nama[:255],
                    nama_normalized=nama_norm,
                    nama_gelar=gelar,
                    tanggal_lahir=tgl,
                    tempat_tugas=tempat_tugas,
                    npsn=val(row, 'NPSN'),
                    kecamatan=val(row, 'Kecamatan'),
                    kabupaten=val(row, 'Kabupaten'),
                    provinsi=val(row, 'Provinsi'),
                    jenjang=val(row, 'Jenjang'),
                    jabatan=val(row, 'Jabatan'),
                    nomor_rekening=nomor_rek or None,
                    bank=bank or None,
                    atas_nama_rekening=atas_nama or None,
                )
                p.status_akun = 'aktif' if (
                    nip or nik or nuptk) else 'belum_identitas'
                peserta_objs.append(p)

                # ---- Kumpulkan perbaikan records ----
                perbaikan_items = []
                if perbaikan_nama:
                    perbaikan_items.append(
                        ('nama', nama_asli or '', perbaikan_nama))
                if perbaikan_nik:
                    if nik_before != nik and nik:
                        perbaikan_items.append(
                            ('nik', nik_before or '', nik))
                    elif nip_before != nip and nip:
                        perbaikan_items.append(
                            ('nip', nip_before or '', nip))
                    else:
                        perbaikan_items.append(
                            ('nik', nik_before or '', perbaikan_nik))
                if perbaikan_nuptk:
                    perbaikan_items.append(
                        ('nuptk', nuptk_asli or '', perbaikan_nuptk))
                if perbaikan_tempat:
                    perbaikan_items.append(
                        ('tempat_tugas', tempat_asli or '', perbaikan_tempat))

                if perbaikan_items:
                    perbaikan_batch.append({
                        'peserta_index': len(peserta_objs) - 1,
                        'line': line_no,
                        'nama': nama,
                        'items': perbaikan_items,
                    })
                    stats['perbaikan_records'] += len(perbaikan_items)
                    perbaikan_records.append({
                        'line': line_no,
                        'nama_lama': nama_asli,
                        'nama_baru': perbaikan_nama,
                        'nip_nik_lama': nip_nik_raw,
                        'nik_baru': perbaikan_nik,
                        'nuptk_lama': nuptk_asli,
                        'nuptk_baru': perbaikan_nuptk,
                        'tempat_lama': tempat_asli,
                        'tempat_baru': perbaikan_tempat,
                    })

            except Exception as e:
                errors.append((line_no, f'{type(e).__name__}: {e}'))
                try:
                    nama_fb = (val(row, 'Perbaikan Nama') or val(row, 'Nama')
                               or f'(baris {line_no})')
                    nomor_rek = val(row, 'Nomor Rekening')
                    p = Peserta(
                        nip_nik_raw=(val(row, 'NIP/NIK')
                                     or val(row, 'Perbaikan NIK') or ''),
                        nip=None, nik=None, nuptk=None,
                        nama=nama_fb[:255],
                        nama_normalized='', nama_gelar='',
                        nomor_rekening=nomor_rek or None,
                        bank=val(row, 'Bank') or None,
                        atas_nama_rekening=(
                            val(row, 'Atas Nama Rekening') or None),
                        status_akun='belum_identitas',
                    )
                    peserta_objs.append(p)
                    perbaikan_batch.append({
                        'peserta_index': len(peserta_objs) - 1,
                        'line': line_no, 'nama': nama_fb, 'items': [],
                    })
                    if nomor_rek:
                        stats['dengan_rekening'] += 1
                except Exception as e2:
                    errors.append((line_no, f'FALLBACK FAILED: {e2}'))

        # === Duplikat ===
        duplikat_nip_list = [(k, v)
                             for k, v in nip_occurrences.items() if len(v) > 1]
        duplikat_nik_list = [(k, v)
                             for k, v in nik_occurrences.items() if len(v) > 1]
        stats['duplikat_nip'] = sum(len(v) - 1 for _, v in duplikat_nip_list)
        stats['duplikat_nik'] = sum(len(v) - 1 for _, v in duplikat_nik_list)

        # === SIMPAN ===
        n_peserta_saved, n_perbaikan_saved = 0, 0
        if not dry_run and peserta_objs:
            n_peserta_saved, n_perbaikan_saved = self._simpan_semua(
                peserta_objs, perbaikan_batch, errors)

        # === Cek expected ===
        if expected is not None:
            if stats['total'] != expected:
                self.stdout.write(self.style.WARNING(
                    f'⚠️  Expected {expected} baris, terbaca '
                    f'{stats["total"]} (selisih {expected - stats["total"]})'
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f'✅ Jumlah baris cocok: {expected}'))

        # === CONSOLE REPORT ===
        self._print_console(stats, peserta_objs, n_peserta_saved,
                            n_perbaikan_saved, dry_run, errors)

        # === EXCEL REPORT ===
        if report_path:
            self._generate_excel_report(
                report_path, stats, panjang_aneh_list,
                duplikat_nip_list, duplikat_nik_list, errors,
                rekening_records, perbaikan_records,
            )
            self.stdout.write(self.style.SUCCESS(
                f'\n📄 Laporan lengkap: {report_path}'))

    # ============================================================
    def _pilih_sheet(self, wb):
        if TARGET_SHEET_NAME in wb.sheetnames:
            return wb[TARGET_SHEET_NAME]

        target_norm = _norm_key(TARGET_SHEET_NAME)
        for s in wb.sheetnames:
            if _norm_key(s) == target_norm:
                return wb[s]

        for s in wb.sheetnames:
            norm = s.lower().replace(' ', '').replace('_', '').replace('-', '')
            if 'clearing' in norm and 'rekening' in norm:
                return wb[s]

        raise CommandError(
            f'Sheet "{TARGET_SHEET_NAME}" tidak ditemukan. '
            f'Sheet tersedia: {wb.sheetnames}'
        )

    # ============================================================
    def _simpan_semua(self, peserta_objs, perbaikan_batch, errors):
        can_get_pk = connection.features.can_return_rows_from_bulk_insert
        created = []

        try:
            if can_get_pk:
                with transaction.atomic():
                    created = Peserta.objects.bulk_create(
                        peserta_objs, batch_size=500)
            else:
                with transaction.atomic():
                    for p in peserta_objs:
                        p.save()
                    created = list(peserta_objs)
        except Exception as e:
            self.stdout.write(self.style.ERROR(
                f'bulk_create Peserta gagal: {e}'))
            created = []
            for idx, p in enumerate(peserta_objs):
                try:
                    p.save()
                    created.append(p)
                except Exception as e2:
                    line = (perbaikan_batch[idx].get('line', '?')
                            if idx < len(perbaikan_batch) else '?')
                    errors.append((line, f'save peserta: {e2}'))

        perbaikan_objs = []
        for pb in perbaikan_batch:
            idx = pb['peserta_index']
            if idx >= len(peserta_objs):
                continue
            p = peserta_objs[idx]
            if not getattr(p, 'pk', None):
                continue
            for field_name, nilai_lama, nilai_baru in pb['items']:
                perbaikan_objs.append(Perbaikan(
                    peserta=p,
                    field_name=field_name,
                    nilai_lama=str(nilai_lama or ''),
                    nilai_baru=str(nilai_baru),
                    alasan='Perbaikan dari sheet clearing + rekening',
                    status='approved',
                    catatan_admin='Auto-approved via import clearing',
                    reviewed_at=timezone.now(),
                ))

        n_perbaikan = 0
        if perbaikan_objs:
            try:
                with transaction.atomic():
                    Perbaikan.objects.bulk_create(
                        perbaikan_objs, batch_size=500)
                n_perbaikan = len(perbaikan_objs)
            except Exception as e:
                for obj in perbaikan_objs:
                    try:
                        obj.save()
                        n_perbaikan += 1
                    except Exception as e2:
                        errors.append((
                            'save perbaikan',
                            f'{obj.peserta.nama} / {obj.field_name}: {e2}'))

        return len(created), n_perbaikan

    # ============================================================
    def _apply_perbaikan_nik(self, perbaikan_nik, nip, nik):
        if not perbaikan_nik:
            return nip, nik
        p = str(perbaikan_nik).strip()
        if not p:
            return nip, nik
        digits = _digits_only(p)
        if len(digits) == 16:
            return nip, digits
        if len(digits) == 18:
            return digits, nik
        p_nip, p_nik = parse_nip_nik(p)
        if p_nip or p_nik:
            return p_nip or nip, p_nik or nik
        return nip, p[:20]

    # ============================================================
    def _print_console(self, stats, peserta_objs, n_peserta_saved,
                       n_perbaikan_saved, dry_run, errors):
        sep = '=' * 64
        self.stdout.write('\n' + sep)
        self.stdout.write(self.style.SUCCESS(
            'LAPORAN IMPORT — clearing + rekening'))
        self.stdout.write(sep)
        self.stdout.write(f'Total baris berisi data : {stats["total"]}')
        self.stdout.write(
            f'  • Baris kosong (skip)  : {stats["kosong_baris"]}')
        self.stdout.write(f'  • NIP saja             : {stats["nip_only"]}')
        self.stdout.write(f'  • NIK saja             : {stats["nik_only"]}')
        self.stdout.write(f'  • Kosong identitas     : {stats["kosong"]}')
        self.stdout.write(f'  • Nama kosong          : {stats["nama_kosong"]}')
        self.stdout.write(
            f'  • Panjang aneh         : {stats["panjang_aneh"]}')
        self.stdout.write(
            f'  • Duplikat NIP         : {stats["duplikat_nip"]}')
        self.stdout.write(
            f'  • Duplikat NIK         : {stats["duplikat_nik"]}')
        self.stdout.write(
            f'  • Sel #N/A dibersihkan : {stats["error_cells_skipped"]}')
        self.stdout.write('-' * 64)
        self.stdout.write(self.style.SUCCESS(
            'PERBAIKAN DITERAPKAN (Peserta + Perbaikan):'))
        self.stdout.write(f'  • Nama         : {stats["perbaikan_nama"]}')
        self.stdout.write(f'  • NIK/NIP      : {stats["perbaikan_nik"]}')
        self.stdout.write(f'  • NUPTK        : {stats["perbaikan_nuptk"]}')
        self.stdout.write(f'  • Tempat Tugas : {stats["perbaikan_tempat"]}')
        self.stdout.write(
            f'  • Total baris Perbaikan : {stats["perbaikan_records"]}')
        self.stdout.write('-' * 64)
        self.stdout.write(self.style.SUCCESS('REKENING:'))
        self.stdout.write(f'  • Dengan rekening : {stats["dengan_rekening"]}')
        self.stdout.write('-' * 64)
        self.stdout.write(self.style.SUCCESS('HASIL PENYIMPANAN:'))
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'  [DRY-RUN] {len(peserta_objs)} peserta siap disimpan'))
            self.stdout.write(self.style.WARNING(
                f'  [DRY-RUN] {stats["perbaikan_records"]} perbaikan siap disimpan'))
        else:
            color = (self.style.SUCCESS
                     if n_peserta_saved == len(peserta_objs)
                     else self.style.ERROR)
            self.stdout.write(color(
                f'  ✅ Peserta TERSIMPAN   : {n_peserta_saved} / {len(peserta_objs)}'))
            self.stdout.write(self.style.SUCCESS(
                f'  ✅ Perbaikan TERSIMPAN : '
                f'{n_perbaikan_saved} / {stats["perbaikan_records"]}'))
            if n_peserta_saved == 0 and len(peserta_objs) > 0:
                self.stdout.write(self.style.ERROR(
                    '  ❌ SEMUA GAGAL DISIMPAN. Cek error di atas.'))
        if errors:
            self.stdout.write(self.style.WARNING(
                f'⚠️  Error: {len(errors)} (lihat sheet "8. Error")'))
        self.stdout.write(sep)

    # ============================================================
    def _analisis_masalah(self, raw):
        s = str(raw).strip()
        low = s.lower()
        if s in ('-', '', 'N/A', 'NA', 'TIDAK ADA', 'TIDAKADA', '0', '.', '..'):
            return ('Placeholder kosong',
                    'Peserta tanpa identitas — gunakan fitur aktivasi',
                    'placeholder')
        if 'e+' in low or 'e-' in low:
            return ('Scientific notation (DATA HILANG!)',
                    'Data terpotong oleh Excel. Cari sumber asli.', 'hilang')
        has_alpha = any(c.isalpha() for c in s)
        digits_only = _digits_only(s)
        if has_alpha:
            if len(digits_only) in (16, 18):
                jenis = 'NIK' if len(digits_only) == 16 else 'NIP'
                return ('Mengandung huruf/prefix',
                        f'Hapus prefix → {jenis} {len(digits_only)} digit: '
                        f'{digits_only}',
                        'bisa_dipulihkan')
            return ('Mengandung huruf/prefix',
                    f'Digit tersisa: {len(digits_only)}', 'perlu_cek')
        if not digits_only:
            return ('Bukan angka', 'Sel berisi non-angka', 'hilang')
        n = len(digits_only)
        if n == 16:
            return (f'Bisa dipulihkan ({n})',
                    f'NIK: {digits_only}', 'bisa_dipulihkan')
        if n == 18:
            return (f'Bisa dipulihkan ({n})',
                    f'NIP: {digits_only}', 'bisa_dipulihkan')
        if n == 15:
            return (f'NIK kurang 1 ({n})', 'Cek KTP asli', 'perlu_cek')
        if n == 17:
            return ('17 digit (NIP kurang / NIK lebih)',
                    'Cek sumber', 'perlu_cek')
        if n == 19:
            return (f'NIP lebih 1 ({n})', 'Hapus digit ekstra', 'perlu_cek')
        if 20 <= n <= 24:
            return (f'2 nomor tergabung ({n})', 'Pisahkan', 'perlu_cek')
        if n <= 14:
            return (f'Data parsial ({n})', 'Cek sumber', 'perlu_cek')
        return (f'Panjang tidak standar ({n})', 'Cek sumber', 'perlu_cek')

    # ============================================================
    def _generate_excel_report(self, output_path, stats, panjang_aneh_list,
                               duplikat_nip_list, duplikat_nik_list, errors,
                               rekening_records, perbaikan_records):
        wb = Workbook()
        header_font = Font(bold=True, color='FFFFFF', size=11)
        header_fill = PatternFill('solid', fgColor='2563EB')
        title_font = Font(bold=True, size=14, color='2563EB')
        red_fill = PatternFill('solid', fgColor='FEE2E2')
        yellow_fill = PatternFill('solid', fgColor='FEF3C7')
        green_fill = PatternFill('solid', fgColor='D1FAE5')
        gray_fill = PatternFill('solid', fgColor='F3F4F6')
        blue_fill = PatternFill('solid', fgColor='DBEAFE')
        thin_border = Border(
            left=Side(style='thin', color='D1D5DB'),
            right=Side(style='thin', color='D1D5DB'),
            top=Side(style='thin', color='D1D5DB'),
            bottom=Side(style='thin', color='D1D5DB'))

        def style_header(ws, row=1):
            for cell in ws[row]:
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(
                    horizontal='center', vertical='center', wrap_text=True)
                cell.border = thin_border

        def autofit(ws, max_width=60):
            for col in ws.columns:
                length = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    if cell.value is not None:
                        length = max(length, len(str(cell.value)))
                ws.column_dimensions[col_letter].width = min(
                    length + 2, max_width)

        def apply_border(ws, start_row=1):
            for row in ws.iter_rows(min_row=start_row):
                for cell in row:
                    cell.border = thin_border
                    cell.alignment = Alignment(
                        wrap_text=True, vertical='top')

        # ===== 1. RINGKASAN =====
        ws = wb.active
        ws.title = "1. RINGKASAN"
        kategori_count = Counter(p[6] for p in panjang_aneh_list)
        ws.append(['LAPORAN IMPORT — clearing + rekening'])
        ws.merge_cells('A1:B1')
        ws['A1'].font = title_font
        ws['A1'].alignment = Alignment(horizontal='center')
        now_wib = timezone.localtime(timezone.now())
        ws.append([f'Dibuat: {now_wib.strftime("%d %B %Y, %H:%M:%S")} WIB'])
        ws.merge_cells('A2:B2')
        ws.append([])
        ws.append(['STATISTIK UTAMA', 'JUMLAH'])
        style_header(ws, row=4)
        ws.append(['Total baris berisi data', stats['total']])
        ws.append(['Baris kosong (skip)', stats['kosong_baris']])
        ws.append(['NIP saja', stats['nip_only']])
        ws.append(['NIK saja', stats['nik_only']])
        ws.append(['Kosong identitas', stats['kosong']])
        ws.append(['Nama kosong (placeholder)', stats['nama_kosong']])
        ws.append(['Panjang NIP/NIK aneh', stats['panjang_aneh']])
        ws.append(['Duplikat NIP (baris)', stats['duplikat_nip']])
        ws.append(['Duplikat NIK (baris)', stats['duplikat_nik']])
        ws.append(['Sel #N/A dianggap kosong', stats['error_cells_skipped']])
        ws.append(['Total error', len(errors)])
        ws.append([])
        ws.append(['PERBAIKAN DITERAPKAN', 'JUMLAH'])
        style_header(ws, row=ws.max_row)
        ws.append(['Perbaikan Nama', stats['perbaikan_nama']])
        ws.append(['Perbaikan NIK', stats['perbaikan_nik']])
        ws.append(['Perbaikan NUPTK', stats['perbaikan_nuptk']])
        ws.append(['Perbaikan Tempat Tugas', stats['perbaikan_tempat']])
        ws.append(['Total record di tabel Perbaikan',
                   stats['perbaikan_records']])
        ws.append([])
        ws.append(['DATA REKENING', 'JUMLAH'])
        style_header(ws, row=ws.max_row)
        ws.append(['Peserta dengan rekening', stats['dengan_rekening']])
        ws.append([])
        ws.append(['MASALAH PANJANG ANEH', 'JUMLAH'])
        style_header(ws, row=ws.max_row)
        for key, label in [('hilang', 'Data hilang (scientific)'),
                           ('perlu_cek', 'Perlu cek manual'),
                           ('bisa_dipulihkan', 'Bisa dipulihkan otomatis'),
                           ('placeholder', 'Placeholder')]:
            ws.append([label, kategori_count.get(key, 0)])
        ws.column_dimensions['A'].width = 55
        ws.column_dimensions['B'].width = 55
        apply_border(ws, start_row=4)

        # ===== 2. PANJANG ANEH =====
        ws = wb.create_sheet("2. Panjang Aneh")
        ws.append(['No', 'Baris', 'Nama', 'Nilai',
                   'Panjang', 'Kategori', 'Saran', 'Status'])
        style_header(ws)
        for i, item in enumerate(panjang_aneh_list, start=1):
            line_no, nama, raw, length, kat, saran, status = item
            ws.append([i, line_no, nama, str(raw), length,
                       kat, saran, self._status_label(status)])
            fill = {'hilang': red_fill, 'perlu_cek': yellow_fill,
                    'bisa_dipulihkan': green_fill,
                    'placeholder': gray_fill}.get(status)
            if fill:
                for cell in ws[ws.max_row]:
                    cell.fill = fill
        autofit(ws)
        apply_border(ws)
        ws.freeze_panes = 'A2'

        # ===== 3. DUPLIKAT NIP =====
        ws = wb.create_sheet("3. Duplikat NIP")
        ws.append(['No', 'NIP', 'Kemunculan', 'Baris', 'Nama', 'Nilai Asli'])
        style_header(ws)
        for i, (nip, occs) in enumerate(duplikat_nip_list, start=1):
            for line_no, nama, raw in occs:
                ws.append([i, nip, len(occs), line_no, nama, raw])
                for cell in ws[ws.max_row]:
                    cell.fill = red_fill
        autofit(ws)
        apply_border(ws)
        ws.freeze_panes = 'A2'

        # ===== 4. DUPLIKAT NIK =====
        ws = wb.create_sheet("4. Duplikat NIK")
        ws.append(['No', 'NIK', 'Kemunculan', 'Baris', 'Nama', 'Nilai Asli'])
        style_header(ws)
        for i, (nik, occs) in enumerate(duplikat_nik_list, start=1):
            for line_no, nama, raw in occs:
                ws.append([i, nik, len(occs), line_no, nama, raw])
                for cell in ws[ws.max_row]:
                    cell.fill = red_fill
        autofit(ws)
        apply_border(ws)
        ws.freeze_panes = 'A2'

        # ===== 5. REKAP PANJANG =====
        ws = wb.create_sheet("5. Rekap Panjang")
        ws.append(['Panjang', 'Jumlah', 'Kemungkinan'])
        style_header(ws)
        counter = Counter(p[3] for p in panjang_aneh_list)
        for length in sorted(counter):
            ws.append([length, counter[length],
                       self._kemungkinan_panjang(length)])
        autofit(ws)
        apply_border(ws)

        # ===== 6. DATA REKENING =====
        ws = wb.create_sheet("6. Data Rekening")
        ws.append(['No', 'Baris', 'Nama', 'Nomor Rekening',
                   'Bank', 'Atas Nama'])
        style_header(ws)
        for i, rec in enumerate(rekening_records, start=1):
            ws.append([i, rec['line'], rec['nama'],
                       rec['nomor_rekening'], rec['bank'], rec['atas_nama']])
            for cell in ws[ws.max_row]:
                cell.fill = blue_fill
        autofit(ws)
        apply_border(ws)
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions

        # ===== 7. DATA PERBAIKAN =====
        ws = wb.create_sheet("7. Data Perbaikan")
        ws.append(['No', 'Baris', 'Nama Lama', 'Nama Baru',
                   'NIP/NIK Lama', 'Perbaikan NIK',
                   'NUPTK Lama', 'Perbaikan NUPTK',
                   'Tempat Lama', 'Perbaikan Tempat'])
        style_header(ws)
        for i, rec in enumerate(perbaikan_records, start=1):
            ws.append([i, rec['line'], rec['nama_lama'], rec['nama_baru'],
                       rec['nip_nik_lama'], rec['nik_baru'],
                       rec['nuptk_lama'], rec['nuptk_baru'],
                       rec['tempat_lama'], rec['tempat_baru']])
            for cell in ws[ws.max_row]:
                cell.fill = yellow_fill
        autofit(ws)
        apply_border(ws)
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions

        # ===== 8. ERROR =====
        if errors:
            ws = wb.create_sheet("8. Error")
            ws.append(['No', 'Baris', 'Pesan'])
            style_header(ws)
            for i, (line_no, msg) in enumerate(errors, start=1):
                ws.append([i, line_no, msg])
                for cell in ws[ws.max_row]:
                    cell.fill = red_fill
            autofit(ws)
            apply_border(ws)

        # ===== 9. PANDUAN =====
        ws = wb.create_sheet("9. Panduan")
        panduan = [
            ('JENIS', 'PENJELASAN'),
            ('Perbaikan Nama/NIK/NUPTK/Tempat Tugas',
             'Diterapkan ke tabel Peserta (menimpa nilai asli) DAN dicatat di '
             'tabel Perbaikan (status=approved, nilai_lama & nilai_baru '
             'tersimpan untuk audit).'),
            ('Rekening (Nomor/Bank/Atas Nama)',
             'Disimpan ke field terenkripsi Peserta. Tidak membuat record '
             'Perbaikan karena ini data baru, bukan koreksi.'),
            ('Sel berisi #N/A, #REF!, #VALUE!, dll',
             'Diperlakukan kosong. Tidak masuk DB, tidak muncul di laporan '
             'rekening/perbaikan. Baris yang isinya HANYA error Excel akan '
             'dianggap baris kosong dan di-skip.'),
            ('Nama kosong', 'Diisi "(tanpa nama)" — tetap disimpan.'),
            ('Data salah', 'Tetap disimpan apa adanya (sesuai permintaan).'),
            ('Duplikat', 'Tidak dihapus otomatis — hanya dilaporkan di '
                         'sheet 3 & 4.'),
        ]
        for row in panduan:
            ws.append(row)
        ws['A1'].font = header_font
        ws['A1'].fill = header_fill
        ws['B1'].font = header_font
        ws['B1'].fill = header_fill
        for cell in ws[1]:
            cell.alignment = Alignment(
                horizontal='center', vertical='center')
        ws.column_dimensions['A'].width = 45
        ws.column_dimensions['B'].width = 95
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(
                    wrap_text=True, vertical='top')
        apply_border(ws)

        wb.save(output_path)

    def _status_label(self, status):
        return {
            'hilang': '🔴 HILANG', 'perlu_cek': '🟡 CEK',
            'bisa_dipulihkan': '🟢 PULIHKAN',
            'placeholder': '⚪ PLACEHOLDER',
        }.get(status, status)

    def _kemungkinan_panjang(self, n):
        return {
            1: 'Placeholder', 8: 'Tanggal lahir dari NIP',
            11: '⚠️ Scientific — DATA HILANG',
            15: 'NIK kurang 1', 16: 'NIK standar',
            17: 'NIP kurang / NIK lebih 1', 18: 'NIP standar',
            19: 'NIP lebih 1', 20: '2 nomor tergabung',
            21: '2 nomor tergabung', 22: '2 nomor tergabung',
            23: '2 nomor tergabung', 24: '2 nomor tergabung',
        }.get(n, 'Tidak dikenal')
