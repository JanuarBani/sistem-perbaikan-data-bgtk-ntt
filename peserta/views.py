# peserta/views.py
import hashlib


from django.db.models import Q, Case, When, BooleanField
from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from datetime import timedelta
from django_ratelimit.decorators import ratelimit
from django.http import HttpResponse, FileResponse
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from django.utils._os import safe_join
from django.core.exceptions import SuspiciousFileOperation
from django.core.cache import cache

import os
import uuid
import hashlib
from PIL import Image
import magic

from .models import (
    Peserta, Perbaikan, PengajuanAktivasi, DokumenAktivasi,
    NotifikasiInApp, LogLogin, LogContact, LogAdminAction, PengaturanSistem
)
from .forms import (
    PersetujuanForm, CariNamaForm, VerifikasiForm, PopupWAForm,
    PerbaikanForm, AktivasiForm
)


# ============ HELPER ============
def get_peserta(request):
    """Ambil peserta dari session."""
    pid = request.session.get('peserta_id')
    if not pid:
        return None
    return Peserta.objects.filter(id=pid).first()


def require_peserta(request):
    """Decorator-like: kembalikan peserta atau None."""
    return get_peserta(request)


# ============ LANDING & PERSETUJUAN ============
def landing(request):
    # Coba ambil dari cache selama 60 detik
    stats = cache.get('landing_stats')

    if not stats:
        setting = PengaturanSistem.get()
        pending = PengajuanAktivasi.objects.filter(status='pending').count()
        stats = {
            'batas': setting.batas_pending_aktivasi,
            'pending': pending,
            'aktivasi_terbuka': pending < setting.batas_pending_aktivasi,
        }
        # Simpan ke cache
        cache.set('landing_stats', stats, timeout=60)

    return render(request, 'peserta/landing.html', stats)


def persetujuan(request):
    if request.session.get('pdp_ok'):
        return redirect('peserta:cari')
    if request.method == 'POST':
        form = PersetujuanForm(request.POST)
        if form.is_valid():
            request.session['pdp_ok'] = True
            return redirect('peserta:cari')
    else:
        form = PersetujuanForm()
    return render(request, 'peserta/persetujuan.html', {'form': form})


@ratelimit(key='ip', rate='20/h', method='POST', block=True)
def cari(request):
    if not request.session.get('pdp_ok'):
        return redirect('peserta:persetujuan')

    hasil = []
    kata_kunci = ''
    kabupaten_filter = ''
    total_hasil = 0

    if request.method == 'POST':
        form = CariNamaForm(request.POST)
        if form.is_valid():
            kata_kunci = form.cleaned_data.get('kata_kunci', '').strip()
            kabupaten_filter = form.cleaned_data.get('kabupaten', '').strip()

            # Buat Cache Key unik
            raw_key = f"{kata_kunci.lower()}:{kabupaten_filter.lower()}"
            hash_key = hashlib.md5(raw_key.encode('utf-8')).hexdigest()
            cache_key = f"cari:{hash_key}"

            # Cek ke cache terlebih dahulu
            cached_data = cache.get(cache_key)

            if cached_data:
                # Jika ada di cache, langsung pakai (TIDAK ke database)
                hasil = cached_data['hasil']
                total_hasil = cached_data['total']
            else:
                # Jika tidak ada, query ke database
                qs = Peserta.objects.all()

                if kata_kunci:
                    kata_list = kata_kunci.lower().split()
                    for kata in kata_list:
                        qs = qs.filter(
                            Q(nama_normalized__icontains=kata) |
                            Q(nama__icontains=kata) |
                            Q(tempat_tugas__icontains=kata) |
                            Q(kabupaten__icontains=kata) |
                            Q(kecamatan__icontains=kata) |
                            Q(nip__icontains=kata) |
                            Q(nik__icontains=kata) |
                            Q(nuptk__icontains=kata)
                        )

                if kabupaten_filter:
                    qs = qs.filter(kabupaten__icontains=kabupaten_filter)

                qs = qs.order_by('nama_normalized').annotate(
                    has_identity=Case(
                        When(Q(nuptk__isnull=False) & ~Q(nuptk='') | Q(nik__isnull=False) & ~Q(nik='') | Q(nip__isnull=False) & ~Q(nip='') | Q(tanggal_lahir__isnull=False), then=True),
                        default=False, output_field=BooleanField(),
                    )
                ).values('id', 'nama', 'kabupaten', 'has_identity')
                total_hasil = qs.count()

                # WAJIB: Ubah QuerySet ke list() agar bisa masuk cache
                hasil = list(qs[:20])

                # Simpan ke cache selama 5 menit (300 detik)
                cache.set(cache_key, {'hasil': hasil,
                          'total': total_hasil}, timeout=300)
    else:
        form = CariNamaForm()

    return render(request, 'peserta/cari.html', {
        'form': form,
        'hasil': hasil,
        'kata_kunci': kata_kunci,
        'kabupaten_filter': kabupaten_filter,
        'total_hasil': total_hasil,
        'query': kata_kunci,
    })


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def verifikasi(request, peserta_id):
    if not request.session.get('pdp_ok'):
        return redirect('peserta:persetujuan')

    peserta = get_object_or_404(Peserta, id=peserta_id)

    # ===== CEK KETERSEDIAAN DATA IDENTITAS =====
    available_methods = []
    if peserta.nuptk:
        available_methods.append(('nuptk', 'NUPTK'))
    if peserta.nik:
        available_methods.append(('nik', 'NIK'))
    if peserta.nip:
        available_methods.append(('nip', 'NIP'))
    if peserta.tanggal_lahir:
        available_methods.append(('tanggal_lahir', 'Tanggal Lahir'))

    # Kalau TIDAK ADA data identitas sama sekali → arahkan ke aktivasi
    if not available_methods:
        messages.info(
            request,
            'Data identitas Anda (NIK / NIP / NUPTK / Tanggal Lahir) belum terdaftar di sistem. '
            'Silakan ajukan aktivasi dengan mengunggah dokumen bukti identitas (KTP/KK + SK/Kartu Pegawai).'
        )
        return redirect('peserta:aktivasi', peserta_id=peserta.id)

    # ===== PROSES VERIFIKASI =====
    error = None

    if request.method == 'POST':
        form = VerifikasiForm(
            request.POST, available_methods=available_methods)
        if form.is_valid():
            metode = form.cleaned_data['metode']
            nilai = form.cleaned_data['nilai'].strip()

            # Cek lock
            if peserta.locked_until and peserta.locked_until > timezone.now():
                error = f'Akun terkunci sampai {peserta.locked_until.strftime("%H:%M")}.'
            else:
                cocok = False
                if metode == 'nuptk':
                    cocok = bool(peserta.nuptk) and nilai == peserta.nuptk
                elif metode == 'nik':
                    cocok = bool(peserta.nik) and nilai == peserta.nik
                elif metode == 'nip':
                    cocok = bool(peserta.nip) and nilai == peserta.nip
                elif metode == 'tanggal_lahir':
                    from datetime import datetime
                    try:
                        d = datetime.strptime(nilai, '%Y-%m-%d').date()
                        cocok = peserta.tanggal_lahir == d
                    except ValueError:
                        error = 'Format tanggal lahir salah. Gunakan format YYYY-MM-DD.'
                        cocok = False

                if cocok:
                    request.session.cycle_key()
                    request.session['peserta_id'] = peserta.id
                    peserta.failed_login_count = 0
                    peserta.locked_until = None
                    peserta.save(update_fields=[
                        'failed_login_count', 'locked_until'])
                    LogLogin.objects.create(
                        peserta=peserta,
                        ip_address=get_client_ip(request),
                        user_agent=request.META.get(
                            'HTTP_USER_AGENT', '')[:500],
                        success=True,
                    )
                    return redirect('peserta:dashboard')
                else:
                    if not error:
                        peserta.failed_login_count += 1
                        if peserta.failed_login_count >= 5:
                            peserta.locked_until = timezone.now() + timedelta(minutes=15)
                            peserta.failed_login_count = 0
                            error = 'Terlalu banyak percobaan. Coba lagi 15 menit lagi.'
                        else:
                            error = 'Data tidak cocok. Silakan coba lagi.'
                    peserta.save()
                    LogLogin.objects.create(
                        peserta=peserta,
                        ip_address=get_client_ip(request),
                        user_agent=request.META.get(
                            'HTTP_USER_AGENT', '')[:500],
                        success=False,
                        notes=f'metode={metode}',
                    )
    else:
        form = VerifikasiForm(available_methods=available_methods)

    return render(request, 'peserta/verifikasi.html', {
        'form': form,
        'peserta': peserta,
        'error': error,
        'available_methods': available_methods,
    })


def popup_wa(request):
    peserta = require_peserta(request)
    if not peserta:
        return redirect('peserta:cari')
    if peserta.nomor_wa:
        return redirect('peserta:dashboard')

    if request.method == 'POST':
        # Hapus fitur "lewati" — WAJIB isi
        form = PopupWAForm(request.POST)
        if form.is_valid():
            nomor_wa = form.cleaned_data['nomor_wa']

            # Nomor WhatsApp bersifat opsional; cek duplikat hanya jika diisi.
            existing = None
            if nomor_wa:
                existing = Peserta.objects.filter(
                    nomor_wa=nomor_wa
                ).exclude(id=peserta.id).exclude(nomor_wa='').first()

            if existing:
                form.add_error(
                    'nomor_wa',
                    f'Nomor WA ini sudah terdaftar untuk akun lain. '
                    f'Jika ini kesalahan, hubungi admin.'
                )
            else:
                peserta.nomor_wa = nomor_wa or ''
                peserta.save(update_fields=['nomor_wa'])
                return redirect('peserta:dashboard')
    else:
        form = PopupWAForm()

    return render(request, 'peserta/popup_wa.html', {
        'form': form,
        'peserta': peserta,
    })


@require_POST
def logout_view(request):
    request.session.flush()
    return redirect('peserta:landing')


# ============ DASHBOARD ============
def dashboard(request):
    peserta = require_peserta(request)
    if not peserta:
        return redirect('peserta:cari')

    notifikasi_terbaru = peserta.notifikasi.all()[:5]
    perbaikan_pending = peserta.perbaikan.filter(status='pending')
    pending_fields = set(perbaikan_pending.values_list('field_name', flat=True))

    field_data = [
        ('Nama', 'nama'), ('NUPTK', 'nuptk'), ('NIP', 'nip'),
        ('NIK', 'nik'), ('Tempat Tugas', 'tempat_tugas'),
        ('NPSN', 'npsn'), ('Kecamatan', 'kecamatan'),
        ('Kabupaten', 'kabupaten'), ('Provinsi', 'provinsi'),
        ('Jenjang', 'jenjang'), ('Jabatan', 'jabatan'),
    ]
    tidak_dimiliki = set(peserta.data_tidak_dimiliki or [])
    fields = [
        (label,
         'Tidak memiliki' if key in tidak_dimiliki else (getattr(peserta, key) or '-'),
         key)
        for label, key in field_data
    ]
    required_profile_fields = [
        ('NUPTK', 'nuptk'), ('NIP', 'nip'), ('NIK', 'nik'),
        ('Tempat Tugas', 'tempat_tugas'), ('NPSN', 'npsn'),
        ('Kecamatan', 'kecamatan'), ('Kabupaten', 'kabupaten'),
        ('Provinsi', 'provinsi'), ('Jenjang', 'jenjang'),
        ('Jabatan', 'jabatan'),
    ]
    data_belum_lengkap = [
        label for label, key in required_profile_fields
        if not getattr(peserta, key) and key not in tidak_dimiliki
    ]
    data_profil_lengkap = not data_belum_lengkap

    return render(request, 'peserta/dashboard.html', {
        'peserta': peserta,
        'fields': fields,
        'data_kepegawaian_fields': {
            'nuptk', 'nip', 'nik', 'tempat_tugas', 'npsn', 'kecamatan',
            'kabupaten', 'provinsi', 'jenjang', 'jabatan'
        },
        'notifikasi_terbaru': notifikasi_terbaru,
        'perbaikan_pending': perbaikan_pending,
        'pending_fields': pending_fields,
        'show_wa_banner': not bool(peserta.nomor_wa),
        'data_belum_lengkap': data_belum_lengkap,
        'data_profil_lengkap': data_profil_lengkap,
    })


def perbaikan_form(request, field_name):
    peserta = require_peserta(request)
    if not peserta:
        return redirect('peserta:cari')

    valid_fields = [f[0] for f in Perbaikan.FIELD_CHOICES]
    if field_name not in valid_fields:
        raise Http404

    # Cek jika sudah ada pengajuan pending untuk field ini
    if Perbaikan.objects.filter(peserta=peserta, field_name=field_name, status='pending').exists():
        messages.warning(
            request,
            f'Anda sudah mengajukan perbaikan {dict(Perbaikan.FIELD_CHOICES)[field_name]} dan sedang menunggu verifikasi admin. Tidak dapat mengajukan dua kali.'
        )
        return redirect('peserta:dashboard')

    nilai_lama = getattr(peserta, field_name, '') or ''
    field_label = dict(Perbaikan.FIELD_CHOICES)[field_name]

    if request.method == 'POST':
        form = PerbaikanForm(request.POST, field_name=field_name,
                             current_value=nilai_lama)
        if form.is_valid():
            Perbaikan.objects.create(
                peserta=peserta,
                field_name=field_name,
                nilai_lama=str(nilai_lama),
                nilai_baru=form.cleaned_data['nilai_baru'],
                tidak_dimiliki=form.cleaned_data.get('tidak_dimiliki', False),
                alasan=form.cleaned_data.get('alasan', ''),
                ip_address=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
            )
            messages.success(
                request, f'Perbaikan {field_label} berhasil diajukan. Menunggu verifikasi admin.')
            return redirect('peserta:dashboard')
    else:
        form = PerbaikanForm(field_name=field_name, current_value=nilai_lama)

    return render(request, 'peserta/perbaikan.html', {
        'form': form,
        'peserta': peserta,
        'field_name': field_name,
        'field_label': field_label,
        'nilai_lama': nilai_lama,
        'field_required': field_name in {
            'nama', 'nomor_rekening', 'bank', 'atas_nama_rekening'
        },
    })


@require_POST
def ajukan_tidak_dimiliki(request, field_name):
    peserta = require_peserta(request)
    if not peserta:
        return redirect('peserta:cari')

    allowed_fields = {
        'nuptk', 'nip', 'nik', 'tempat_tugas', 'npsn', 'kecamatan',
        'kabupaten', 'provinsi', 'jenjang', 'jabatan'
    }
    if field_name not in allowed_fields:
        raise Http404
    if request.POST.get('tidak_dimiliki') != '1':
        messages.error(request, 'Centang pernyataan bahwa Anda memang tidak memiliki data ini.')
        return redirect('peserta:dashboard')
    if getattr(peserta, field_name):
        messages.error(request, 'Data masih memiliki nilai. Ajukan perubahan data terlebih dahulu.')
        return redirect('peserta:dashboard')
    if field_name in (peserta.data_tidak_dimiliki or []):
        messages.info(request, 'Data ini sudah dinyatakan tidak dimiliki.')
        return redirect('peserta:dashboard')
    if peserta.perbaikan.filter(field_name=field_name, status='pending').exists():
        messages.warning(request, 'Pengajuan untuk data ini sedang menunggu verifikasi admin.')
        return redirect('peserta:dashboard')

    Perbaikan.objects.create(
        peserta=peserta,
        field_name=field_name,
        nilai_lama='',
        nilai_baru='',
        tidak_dimiliki=True,
        alasan='Peserta menyatakan tidak memiliki data ini melalui dashboard.',
        ip_address=get_client_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
    )
    messages.success(request, 'Pernyataan berhasil diajukan dan menunggu verifikasi admin.')
    return redirect('peserta:dashboard')


def notifikasi_list(request):
    peserta = require_peserta(request)
    if not peserta:
        return redirect('peserta:cari')
    notif = peserta.notifikasi.all()
    return render(request, 'peserta/notifikasi.html', {'notifikasi': notif})


@require_POST
def notifikasi_baca(request, notif_id):
    peserta = require_peserta(request)
    if not peserta:
        return redirect('peserta:cari')
    if request.method != 'POST':
        return redirect('peserta:notifikasi')
    n = get_object_or_404(NotifikasiInApp, id=notif_id, peserta=peserta)
    n.sudah_dibaca = True
    n.read_at = timezone.now()
    n.save()
    if n.link_terkait:
        return redirect(n.link_terkait)
    return redirect('peserta:notifikasi')


@require_POST
def notifikasi_hapus(request, notif_id):
    peserta = require_peserta(request)
    if not peserta:
        return redirect('peserta:cari')
    notif = get_object_or_404(NotifikasiInApp, id=notif_id, peserta=peserta)
    notif.delete()
    messages.success(request, 'Notifikasi berhasil dihapus.')
    return redirect('peserta:notifikasi')


@require_POST
def notifikasi_hapus_semua(request):
    peserta = require_peserta(request)
    if not peserta:
        return redirect('peserta:cari')
    jumlah, _ = peserta.notifikasi.all().delete()
    messages.success(request, f'{jumlah} notifikasi berhasil dihapus.')
    return redirect('peserta:notifikasi')


@ratelimit(key='ip', rate='3/d', method='POST', block=True)
def aktivasi(request, peserta_id):
    if not request.session.get('pdp_ok'):
        return redirect('peserta:persetujuan')

    peserta = get_object_or_404(Peserta, id=peserta_id)
    setting = PengaturanSistem.get()

    # Cek apakah benar-benar tidak punya identitas
    if peserta.nuptk or peserta.nik or peserta.nip:
        return redirect('peserta:verifikasi', peserta_id=peserta.id)

    # ===== TAMBAHAN: Cek pengajuan yang sudah ada =====
    existing = PengajuanAktivasi.objects.filter(
        peserta=peserta, status='pending'
    ).first()
    if existing:
        messages.info(
            request,
            f'Anda sudah memiliki pengajuan aktif: {existing.nomor_referensi}. '
            f'Status: menunggu verifikasi admin.'
        )
        return redirect('peserta:aktivasi_status', ref=existing.nomor_referensi)

    # Kalau pengajuan sebelumnya di-reject, boleh submit ulang
    # Tapi tetap perlu dicek dulu apakah sudah di-approve (status_akun='aktif')
    # if peserta.status_akun == 'aktif':
    #     return redirect('peserta:verifikasi', peserta_id=peserta.id)

    pending_count = PengajuanAktivasi.objects.filter(status='pending').count()
    if pending_count >= setting.batas_pending_aktivasi:
        return render(request, 'peserta/aktivasi.html', {
            'penuh': True, 'peserta': peserta, 'setting': setting,
        })

    if request.method == 'POST':
        form = AktivasiForm(request.POST)
        if form.is_valid():
            uploaded_paths = []
            try:
                with transaction.atomic():
                    # Cek ulang dalam transaksi
                    pend = PengajuanAktivasi.objects.filter(status='pending').count()
                    if pend >= setting.batas_pending_aktivasi:
                        messages.error(request, 'Sedang banyak pengajuan, coba lagi besok.')
                        return redirect('peserta:aktivasi', peserta_id=peserta.id)

                    # ===== TAMBAHKAN PENGECEKAN DUPLIKAT WA DI SINI =====
                    nomor_wa = form.cleaned_data['nomor_wa']
                    wa_existing = Peserta.objects.filter(
                        nomor_wa=nomor_wa
                    ).exclude(id=peserta.id).first()
                    
                    if wa_existing:
                        messages.error(request, 'Nomor WhatsApp ini sudah digunakan oleh akun lain. Silakan gunakan nomor Anda sendiri.')
                        return redirect('peserta:aktivasi', peserta_id=peserta.id)
                    # ====================================================

                    from .utils import generate_nomor_referensi
                    ref = generate_nomor_referensi()
                    pengajuan = PengajuanAktivasi.objects.create(
                        peserta=peserta,
                        nik_diisi=form.cleaned_data.get('nik_diisi', ''),
                        nip_diisi=form.cleaned_data.get('nip_diisi', ''),
                        nuptk_diisi=form.cleaned_data.get('nuptk_diisi', ''),
                        alasan_kosong=form.cleaned_data.get('alasan_kosong', ''),
                        nomor_referensi=ref,
                        status='pending',
                        ip_address=get_client_ip(request),
                        user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                    )
                    peserta.nomor_wa = nomor_wa  # Gunakan variabel yang sudah divalidasi
                    peserta.status_akun = 'pending_aktivasi'
                    peserta.save()
                    request.session.cycle_key()
                    request.session['peserta_id'] = peserta.id

                    #  Proses upload dokumen
                    # HANYA proses field yang diizinkan
                    allowed_fields = ['ktp', 'sk', 'kk', 'kartu_pegawai', 'surat_keterangan']
                    for field_name in allowed_fields:
                        if field_name in request.FILES:
                            f = request.FILES[field_name]
                            if f:
                                uploaded_paths.append(simpan_dokumen(pengajuan, field_name, f, setting))

                    messages.success(
                        request,
                        f'Pengajuan diterima. No. Referensi: {ref}'
                    )
                    return redirect('peserta:aktivasi_status', ref=ref)

            except ValueError as e:
                request.session.pop('peserta_id', None)
                for stored_path in uploaded_paths:
                    try:
                        os.remove(stored_path)
                    except OSError:
                        pass
                # Tangkap error dari simpan_dokumen (file tidak valid/terlalu besar)
                messages.error(
                    request,
                    f'Upload gagal: {e}. Silakan periksa file Anda dan coba lagi.'
                )
                # Tidak redirect — biarkan form tampil lagi dengan error message
                # File sudah terhapus otomatis karena transaction rollback

            except Exception as e:
                request.session.pop('peserta_id', None)
                for stored_path in uploaded_paths:
                    try:
                        os.remove(stored_path)
                    except OSError:
                        pass
                # Error tak terduga — log dan tampil pesan generic
                import logging
                logging.getLogger(__name__).exception(f'Error di aktivasi: {e}')
                messages.error(
                    request,
                    'Terjadi kesalahan saat menyimpan pengajuan. Silakan coba lagi.'
                )
    else:
        form = AktivasiForm()

    return render(request, 'peserta/aktivasi.html', {
        'form': form, 'peserta': peserta, 'setting': setting, 'penuh': False,
    })


def aktivasi_status(request, ref):
    peserta = require_peserta(request)
    if not peserta:
        return redirect('peserta:cari')
    pengajuan = get_object_or_404(PengajuanAktivasi, nomor_referensi=ref, peserta=peserta)
    return render(request, 'peserta/aktivasi_status.html', {'pengajuan': pengajuan})


@never_cache
@require_GET
def dokumen_aktivasi(request, pk):
    if not request.user.is_authenticated or not request.user.is_active or not request.user.is_staff:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    dokumen = get_object_or_404(DokumenAktivasi, pk=pk, deleted_at__isnull=True)
    from django.conf import settings
    try:
        path = safe_join(str(settings.PRIVATE_MEDIA_ROOT), dokumen.file_path)
    except (SuspiciousFileOperation, ValueError):
        raise Http404
    if not os.path.isfile(path):
        raise Http404
    from .models import LogAksesDokumen
    LogAksesDokumen.objects.create(dokumen=dokumen, admin=request.user, ip_address=get_client_ip(request))
    response = FileResponse(open(path, 'rb'), content_type=dokumen.file_mime or 'application/octet-stream')
    response['Content-Disposition'] = f'inline; filename="document-{dokumen.pk}"'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response


# ============ UTIL FILE ============
def get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def simpan_dokumen(pengajuan, field_name, file, setting):
    """Simpan file dokumen + kompres + validasi."""
    mime = magic.from_buffer(file.read(1024), mime=True)
    file.seek(0)

    allowed = ['image/jpeg', 'image/png', 'application/pdf']
    if mime not in allowed:
        jenis_nama = {
            'ktp': 'KTP', 'kk': 'Kartu Keluarga', 'sk': 'SK Pengangkatan',
            'kartu_pegawai': 'Kartu Pegawai', 'surat_keterangan': 'Surat Keterangan',
        }.get(field_name, field_name)
        raise ValueError(
            f'{jenis_nama}: format tidak didukung ({mime}). '
            f'Gunakan JPG, PNG, atau PDF.'
        )

    max_bytes = setting.ukuran_file_maksimal_mb * 1024 * 1024
    if file.size > max_bytes:
        if mime.startswith('image/'):
            file = compress_image(file, max_bytes)
            mime = 'image/jpeg'
        else:
            jenis_nama = {
                'ktp': 'KTP', 'kk': 'Kartu Keluarga', 'sk': 'SK Pengangkatan',
                'kartu_pegawai': 'Kartu Pegawai', 'surat_keterangan': 'Surat Keterangan',
            }.get(field_name, field_name)
            raise ValueError(
                f'{jenis_nama}: ukuran file melebihi {setting.ukuran_file_maksimal_mb} MB. '
                f'PDF tidak bisa dikompres otomatis. Silakan perkecil file dulu.'
            )

    # Folder: media/dokumen/{ref}/
    folder = os.path.join('dokumen', pengajuan.nomor_referensi)
    from django.conf import settings as django_settings
    abs_folder = os.path.join(django_settings.PRIVATE_MEDIA_ROOT, folder)
    os.makedirs(abs_folder, exist_ok=True)

    ext = file.name.split('.')[-1].lower() or 'bin'
    filename = f'{uuid.uuid4().hex}.{ext}'
    rel_path = os.path.join(folder, filename).replace('\\', '/')
    abs_path = os.path.join(abs_folder, filename)

    # Hash dulu
    sha = hashlib.sha256()
    for chunk in file.chunks():
        sha.update(chunk)
    file.seek(0)

    try:
        with open(abs_path, 'xb') as dest:
            for chunk in file.chunks():
                dest.write(chunk)
    except Exception:
        if os.path.exists(abs_path):
            os.remove(abs_path)
        raise

    jenis_map = {'ktp': 'ktp', 'kk': 'kk', 'sk': 'sk',
                 'kartu_pegawai': 'kartu_pegawai', 'surat_keterangan': 'surat_keterangan'}
    jenis = jenis_map.get(field_name, field_name)

    try:
        DokumenAktivasi.objects.create(
            pengajuan=pengajuan,
            jenis_dokumen=jenis,
            file_path=rel_path,
            file_name_original=file.name,
            file_mime=mime,
            file_size=os.path.getsize(abs_path),
            file_hash_sha256=sha.hexdigest(),
        )
    except Exception:
        if os.path.exists(abs_path):
            os.remove(abs_path)
        raise
    return abs_path


def compress_image(file, max_bytes, max_dim=1600):
    from io import BytesIO
    from django.core.files.uploadedfile import InMemoryUploadedFile
    try:
        img = Image.open(file)
        img.verify()
        file.seek(0)
        img = Image.open(file)
    except Exception as exc:
        raise ValueError('File gambar rusak atau tidak valid.') from exc
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGB')
    img.thumbnail((max_dim, max_dim))
    out = BytesIO()
    quality = 85
    while quality >= 25:
        out.seek(0)
        out.truncate()
        img.save(out, format='JPEG', quality=quality, optimize=True)
        if out.tell() <= max_bytes:
            break
        quality -= 10
    if out.tell() > max_bytes:
        raise ValueError('Gambar tidak dapat dikompres hingga batas ukuran yang diizinkan.')
    out.seek(0)
    return InMemoryUploadedFile(out, 'file', file.name.rsplit('.', 1)[0] + '.jpg',
                                'image/jpeg', out.tell(), None)


def ratelimited_view(request, exception):
    return HttpResponse(
        '<h1>Terlalu Banyak Permintaan</h1>'
        '<p>Anda telah melakukan terlalu banyak percobaan. Silakan coba lagi nanti.</p>'
        '<a href="/">Kembali ke Beranda</a>',
        status=429, content_type='text/html'
    )

# Tambahkan fungsi ini di bagian bawah file


@require_POST
def tandai_data_sesuai(request):
    peserta = require_peserta(request)
    if not peserta:
        return redirect('peserta:cari')

    # ===== TAMBAHKAN VALIDASI BACKEND =====
    is_lengkap = bool(
        peserta.nomor_rekening and peserta.bank and peserta.atas_nama_rekening
    ) and not [
        field for field in (
            'nuptk', 'nip', 'nik', 'tempat_tugas', 'npsn', 'kecamatan',
            'kabupaten', 'provinsi', 'jenjang', 'jabatan'
        )
        if not getattr(peserta, field) and field not in (peserta.data_tidak_dimiliki or [])
    ]
    
    if not is_lengkap:
        messages.error(
            request, 
            'Konfirmasi belum dapat dilakukan. Lengkapi nomor rekening, nama bank, '
            'dan nama pemilik rekening terlebih dahulu. Untuk data pelengkap yang kosong, '
            'isi nilainya atau nyatakan bahwa Anda memang tidak memilikinya.'
        )
        return redirect('peserta:dashboard')

    peserta.is_data_verified = True
    peserta.verified_at = timezone.now()
    peserta.save(update_fields=['is_data_verified', 'verified_at'])

    messages.success(
        request, 'Terima kasih! Anda telah mengonfirmasi bahwa data Anda sudah sesuai.')
    return redirect('peserta:dashboard')
