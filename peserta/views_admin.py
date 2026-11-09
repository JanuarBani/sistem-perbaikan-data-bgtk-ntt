# peserta/views_admin.py
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, PatternFill, Alignment
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.utils import timezone
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit
from django.core.cache import cache
from django.core.exceptions import SuspiciousFileOperation
from django.core.paginator import Paginator
from django.db.models.functions import Length
from django.urls import reverse
from datetime import timedelta
import os

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from .models import (
    Peserta, Perbaikan, PengajuanAktivasi, DokumenAktivasi,
    NotifikasiInApp, LogLogin, LogContact, LogAdminAction,
    PengaturanSistem
)
from .decorators import admin_required

# ================= LOGIN / LOGOUT =================


@require_http_methods(['GET', 'POST'])
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def admin_login(request):
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('admin_panel:dashboard')

    error = None
    next_url = request.GET.get('next', '')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        next_url = request.POST.get('next', '')

        if not username or not password:
            error = 'Username dan password wajib diisi.'
        else:
            user = authenticate(request, username=username, password=password)
            if user is None:
                error = 'Username atau password salah.'
            elif not user.is_staff:
                error = 'Akun ini tidak memiliki akses admin.'
            elif not user.is_active:
                error = 'Akun ini dinonaktifkan.'
            else:
                login(request, user)
                LogAdminAction.objects.create(
                    admin=user, action='admin_login',
                    target_type='Admin', target_id=user.id,
                    ip_address=get_client_ip(request),
                )
                if next_url and next_url.startswith('/panel/'):
                    return redirect(next_url)
                return redirect('admin_panel:dashboard')

    return render(request, 'peserta/admin/login.html', {
        'error': error, 'next': next_url,
    })


@require_http_methods(['POST'])
def admin_logout(request):
    if request.user.is_authenticated:
        LogAdminAction.objects.create(
            admin=request.user, action='admin_logout',
            target_type='Admin', target_id=request.user.id,
            ip_address=get_client_ip(request),
        )
        logout(request)
    return redirect('admin_panel:login')


# ================= DASHBOARD =================
@admin_required
def dashboard(request):
    setting = PengaturanSistem.get()
    now = timezone.now()
    today = now.date()
    week_ago = now - timedelta(days=7)

    stats = cache.get('admin_dashboard_stats')
    if not stats:
        stats = {
            'total_peserta': Peserta.objects.count(),
            'peserta_aktif': Peserta.objects.filter(status_akun='aktif').count(),
            'peserta_belum_identitas': Peserta.objects.filter(status_akun='belum_identitas').count(),
            'peserta_pending_aktivasi': Peserta.objects.filter(status_akun='pending_aktivasi').count(),
            'perbaikan_count': Perbaikan.objects.filter(status='pending').count(),
            'perbaikan_minggu': Perbaikan.objects.filter(created_at__gte=week_ago).count(),
            'aktivasi_pending': PengajuanAktivasi.objects.filter(status='pending').count(),
            'aktivasi_draft': PengajuanAktivasi.objects.filter(
                status='draft',
                created_at__gte=now -
                timedelta(minutes=setting.durasi_draft_menit)
            ).count(),
            'login_hari_ini': LogLogin.objects.filter(login_at__date=today, success=True).count(),
            'login_gagal_hari_ini': LogLogin.objects.filter(login_at__date=today, success=False).count(),
        }
        cache.set('admin_dashboard_stats', stats, timeout=120)

    slot_terpakai = stats['aktivasi_pending'] + stats['aktivasi_draft']
    slot_tersedia = max(0, setting.batas_pending_aktivasi - slot_terpakai)
    persen_slot = (slot_terpakai / setting.batas_pending_aktivasi *
                   100) if setting.batas_pending_aktivasi else 0

    if persen_slot < 80:
        slot_status, slot_warna = 'aman', 'green'
    elif persen_slot < 100:
        slot_status, slot_warna = 'waspada', 'yellow'
    else:
        slot_status, slot_warna = 'penuh', 'red'

    perbaikan_pending = Perbaikan.objects.filter(status='pending')
    batas_hari = now - timedelta(days=setting.auto_reject_pending_hari)
    aktivasi_terlambat = PengajuanAktivasi.objects.filter(
        status='pending', created_at__lt=batas_hari
    ).count()

    return render(request, 'peserta/admin/dashboard.html', {
        **stats,
        'slot_terpakai': slot_terpakai,
        'slot_tersedia': slot_tersedia,
        'slot_batas': setting.batas_pending_aktivasi,
        'persen_slot': round(persen_slot, 1),
        'slot_status': slot_status,
        'slot_warna': slot_warna,
        'aktivasi_terlambat': aktivasi_terlambat,
        'perbaikan_terbaru': perbaikan_pending.select_related('peserta')[:10],
        'aktivasi_terbaru': PengajuanAktivasi.objects.filter(
            status='pending').select_related('peserta')[:10],
        'setting': setting,
    })


# ================= PERBAIKAN =================
@admin_required
def perbaikan_list(request):
    status_filter = request.GET.get('status', 'pending')
    field_filter = request.GET.get('field', '')
    q = request.GET.get('q', '').strip()

    qs = Perbaikan.objects.select_related('peserta')
    if status_filter and status_filter != 'all':
        qs = qs.filter(status=status_filter)
    if field_filter:
        qs = qs.filter(field_name=field_filter)
    if q:
        qs = qs.filter(Q(peserta__nama__icontains=q) |
                       Q(peserta__nik__icontains=q) |
                       Q(peserta__nip__icontains=q))
    qs = qs.order_by('-created_at')

    paginator = Paginator(qs, 30)
    page = paginator.get_page(request.GET.get('page'))

    counts = Perbaikan.objects.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status='pending')),
        approved=Count('id', filter=Q(status='approved')),
        rejected=Count('id', filter=Q(status='rejected')),
    )

    return render(request, 'peserta/admin/daftar_perbaikan.html', {
        'page': page, 'perbaikan_list': page,
        'status_filter': status_filter,
        'field_filter': field_filter, 'q': q,
        'counts': counts, 'field_choices': Perbaikan.FIELD_CHOICES,
    })


@admin_required
def perbaikan_detail(request, pk):
    perbaikan = get_object_or_404(
        Perbaikan.objects.select_related('peserta'), pk=pk)

    if request.method == 'POST' and perbaikan.status == 'pending':
        action = request.POST.get('action')
        catatan = request.POST.get('catatan_admin', '').strip()
        if action not in {'approve', 'reject'} or (action == 'reject' and not catatan):
            messages.error(request, 'Aksi tidak valid atau alasan penolakan wajib diisi.')
            return redirect('admin_panel:perbaikan_detail', pk=pk)

        if action == 'approve':
            with transaction.atomic():
                perbaikan.status = 'approved'
                perbaikan.catatan_admin = catatan
                perbaikan.reviewed_by = request.user
                perbaikan.reviewed_at = timezone.now()
                perbaikan.save()

                peserta = perbaikan.peserta
                if hasattr(peserta, perbaikan.field_name):
                    setattr(peserta, perbaikan.field_name,
                            perbaikan.nilai_baru)
                    profile_fields = {
                        'nuptk', 'nip', 'nik', 'tempat_tugas', 'npsn',
                        'kecamatan', 'kabupaten', 'provinsi', 'jenjang', 'jabatan'
                    }
                    if perbaikan.field_name in profile_fields:
                        fields_without_data = set(peserta.data_tidak_dimiliki or [])
                        if perbaikan.tidak_dimiliki:
                            fields_without_data.add(perbaikan.field_name)
                        else:
                            fields_without_data.discard(perbaikan.field_name)
                        peserta.data_tidak_dimiliki = sorted(fields_without_data)
                    if perbaikan.field_name in profile_fields | {'nama', 'nomor_rekening', 'bank', 'atas_nama_rekening'}:
                        peserta.is_data_verified = False
                        peserta.verified_at = None
                    if perbaikan.field_name == 'nama':
                        from .utils import normalisasi_nama
                        norm, gelar = normalisasi_nama(perbaikan.nilai_baru)
                        peserta.nama_normalized = norm
                        peserta.nama_gelar = gelar
                    peserta.save()

                NotifikasiInApp.objects.create(
                    peserta=peserta, tipe='perbaikan_approved',
                    judul=(
                        f'Pernyataan {perbaikan.get_field_name_display()} disetujui'
                        if perbaikan.tidak_dimiliki else
                        f'Perbaikan {perbaikan.get_field_name_display()} disetujui'
                    ),
                    isi=(
                        f'Pernyataan bahwa Anda tidak memiliki {perbaikan.get_field_name_display()} telah disetujui.'
                        if perbaikan.tidak_dimiliki else
                        f'Perbaikan {perbaikan.get_field_name_display()} Anda telah disetujui.'
                    )
                    + (f'\n\nCatatan: {catatan}' if catatan else ''),
                )
                LogAdminAction.objects.create(
                    admin=request.user, action='approve_perbaikan',
                    target_type='Perbaikan', target_id=perbaikan.id,
                    ip_address=get_client_ip(request),
                )
            # TAMBAHKAN BARIS INI
            cache.delete('admin_dashboard_stats')
            
            messages.success(request, 'Perbaikan disetujui & diterapkan.')
            return redirect('admin_panel:perbaikan_list')

        elif action == 'reject':
            perbaikan.status = 'rejected'
            perbaikan.catatan_admin = catatan or 'Ditolak oleh admin.'
            perbaikan.reviewed_by = request.user
            perbaikan.reviewed_at = timezone.now()
            perbaikan.save()
            NotifikasiInApp.objects.create(
                peserta=perbaikan.peserta, tipe='perbaikan_rejected',
                judul=f'Perbaikan {perbaikan.get_field_name_display()} ditolak',
                isi=f'Perbaikan ditolak.\n\nAlasan: {perbaikan.catatan_admin}',
            )
            cache.delete('admin_dashboard_stats')
            LogAdminAction.objects.create(
                admin=request.user, action='reject_perbaikan',
                target_type='Perbaikan', target_id=perbaikan.id,
                ip_address=get_client_ip(request),
            )
            messages.warning(request, 'Perbaikan ditolak.')
            return redirect('admin_panel:perbaikan_list')

    return render(request, 'peserta/admin/detail_perbaikan.html', {'perbaikan': perbaikan})


# ================= BULK ACTION PERBAIKAN =================
@admin_required
@require_POST
def perbaikan_bulk_action(request):
    action = request.POST.get('action')
    selected_ids = request.POST.getlist('selected_ids')

    if action not in {'approve', 'reject'}:
        messages.error(request, 'Aksi bulk tidak valid.')
        return redirect('admin_panel:perbaikan_list')

    if not selected_ids:
        messages.warning(request, 'Tidak ada data yang dipilih.')
        return redirect('admin_panel:perbaikan_list')

    perbaikan_list = Perbaikan.objects.filter(
        id__in=selected_ids, status='pending')
    count = perbaikan_list.count()

    for perbaikan in perbaikan_list:
        if action == 'approve':
            with transaction.atomic():
                perbaikan.status = 'approved'
                perbaikan.reviewed_by = request.user
                perbaikan.reviewed_at = timezone.now()
                perbaikan.catatan_admin = 'Bulk approve oleh admin'
                perbaikan.save()

                peserta = perbaikan.peserta
                if hasattr(peserta, perbaikan.field_name):
                    setattr(peserta, perbaikan.field_name,
                            perbaikan.nilai_baru)
                    profile_fields = {
                        'nuptk', 'nip', 'nik', 'tempat_tugas', 'npsn',
                        'kecamatan', 'kabupaten', 'provinsi', 'jenjang', 'jabatan'
                    }
                    if perbaikan.field_name in profile_fields:
                        fields_without_data = set(peserta.data_tidak_dimiliki or [])
                        if perbaikan.tidak_dimiliki:
                            fields_without_data.add(perbaikan.field_name)
                        else:
                            fields_without_data.discard(perbaikan.field_name)
                        peserta.data_tidak_dimiliki = sorted(fields_without_data)
                    if perbaikan.field_name in profile_fields | {'nama', 'nomor_rekening', 'bank', 'atas_nama_rekening'}:
                        peserta.is_data_verified = False
                        peserta.verified_at = None
                    if perbaikan.field_name == 'nama':
                        from .utils import normalisasi_nama
                        norm, gelar = normalisasi_nama(perbaikan.nilai_baru)
                        peserta.nama_normalized = norm
                        peserta.nama_gelar = gelar
                    peserta.save()

                NotifikasiInApp.objects.create(
                    peserta=peserta, tipe='perbaikan_approved',
                    judul=(
                        f'Pernyataan {perbaikan.get_field_name_display()} disetujui'
                        if perbaikan.tidak_dimiliki else
                        f'Perbaikan {perbaikan.get_field_name_display()} disetujui'
                    ),
                    isi=(
                        f'Pernyataan bahwa Anda tidak memiliki {perbaikan.get_field_name_display()} telah disetujui.'
                        if perbaikan.tidak_dimiliki else
                        f'Perbaikan {perbaikan.get_field_name_display()} Anda telah disetujui.'
                    ),
                )
        elif action == 'reject':
            perbaikan.status = 'rejected'
            perbaikan.catatan_admin = 'Bulk reject oleh admin'
            perbaikan.reviewed_by = request.user
            perbaikan.reviewed_at = timezone.now()
            perbaikan.save()

            NotifikasiInApp.objects.create(
                peserta=perbaikan.peserta, tipe='perbaikan_rejected',
                judul=f'Perbaikan {perbaikan.get_field_name_display()} ditolak',
                isi='Perbaikan ditolak oleh admin.',
            )

    cache.delete('admin_dashboard_stats')
    messages.success(request, f'{count} data perbaikan berhasil di-{action}.')
    return redirect('admin_panel:perbaikan_list')


# ================= AKTIVASI =================
@admin_required
def aktivasi_list(request):
    status_filter = request.GET.get('status', 'pending')
    q = request.GET.get('q', '').strip()

    qs = PengajuanAktivasi.objects.select_related(
        'peserta').prefetch_related('dokumen')
    if status_filter and status_filter != 'all':
        qs = qs.filter(status=status_filter)
    if q:
        qs = qs.filter(Q(nomor_referensi__icontains=q) |
                       Q(peserta__nama__icontains=q) |
                       Q(nik_diisi__icontains=q) |
                       Q(nip_diisi__icontains=q))
    qs = qs.order_by('-created_at')

    paginator = Paginator(qs, 30)
    page = paginator.get_page(request.GET.get('page'))

    counts = PengajuanAktivasi.objects.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(status='pending')),
        approved=Count('id', filter=Q(status='approved')),
        rejected=Count('id', filter=Q(status='rejected')),
    )

    return render(request, 'peserta/admin/daftar_aktivasi.html', {
        'page': page, 'aktivasi_list': page,
        'status_filter': status_filter,
        'q': q, 'counts': counts,
    })


@admin_required
def aktivasi_detail(request, pk):
    pengajuan = get_object_or_404(
        PengajuanAktivasi.objects.select_related('peserta').prefetch_related('dokumen'), pk=pk
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        catatan = request.POST.get('catatan_admin', '').strip()
        if pengajuan.status != 'pending' or action not in {'approve', 'reject'} or (action == 'reject' and not catatan):
            messages.error(request, 'Pengajuan sudah diproses atau aksi/alasannya tidak valid.')
            return redirect('admin_panel:aktivasi_detail', pk=pk)
        if action == 'approve':
            with transaction.atomic():
                peserta = pengajuan.peserta
                if pengajuan.nik_diisi:
                    peserta.nik = pengajuan.nik_diisi
                if pengajuan.nip_diisi:
                    peserta.nip = pengajuan.nip_diisi
                if pengajuan.nuptk_diisi:
                    peserta.nuptk = pengajuan.nuptk_diisi
                from .utils import derive_tanggal_lahir
                tgl = derive_tanggal_lahir(peserta.nip, peserta.nik)
                if tgl:
                    peserta.tanggal_lahir = tgl
                peserta.status_akun = 'aktif'
                peserta.save()

                pengajuan.status = 'approved'
                pengajuan.catatan_admin = catatan
                pengajuan.reviewed_by = request.user
                pengajuan.reviewed_at = timezone.now()
                pengajuan.save()

                for d in pengajuan.dokumen.all():
                    hapus_file_dokumen(d)

                NotifikasiInApp.objects.create(
                    peserta=peserta, tipe='aktivasi_approved',
                    judul='Aktivasi akun disetujui',
                    isi='Akun Anda aktif. Silakan login kembali.',
                )
                LogAdminAction.objects.create(
                    admin=request.user, action='approve_aktivasi',
                    target_type='PengajuanAktivasi', target_id=pengajuan.id,
                    ip_address=get_client_ip(request),
                )
            # ===== TAMBAHKAN INI =====
            cache.delete('admin_dashboard_stats')
            cache.delete('landing_stats')
            # =========================
            
            messages.success(
                request, f'Aktivasi {pengajuan.nomor_referensi} disetujui.')
            return redirect('admin_panel:aktivasi_list')

        elif action == 'reject':
            with transaction.atomic():
                pengajuan.status = 'rejected'
                pengajuan.catatan_admin = catatan or 'Ditolak.'
                pengajuan.reviewed_by = request.user
                pengajuan.reviewed_at = timezone.now()
                pengajuan.save()
                pengajuan.peserta.status_akun = 'rejected'
                pengajuan.peserta.save(update_fields=['status_akun'])
                for d in pengajuan.dokumen.all():
                    hapus_file_dokumen(d)
                NotifikasiInApp.objects.create(
                    peserta=pengajuan.peserta, tipe='aktivasi_rejected',
                    judul='Aktivasi ditolak',
                    isi=f'Alasan: {pengajuan.catatan_admin}',
                )
                cache.delete('admin_dashboard_stats')
                cache.delete('landing_stats')
            messages.warning(request, 'Aktivasi ditolak.')
            return redirect('admin_panel:aktivasi_list')

    return render(request, 'peserta/admin/detail_aktivasi.html', {'pengajuan': pengajuan})


# ================= PESERTA (DENGAN FILTER DUPLIKAT YANG BENAR) =================
@admin_required
def peserta_list(request):
    q = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '')
    kab_filter = request.GET.get('kabupaten', '')
    dup_filter = request.GET.get('duplikat', '')

    qs = Peserta.objects.all()
    if q:
        qs = qs.filter(Q(nama__icontains=q) | Q(nik__icontains=q) |
                       Q(nip__icontains=q) | Q(nuptk__icontains=q))
    if status_filter:
        qs = qs.filter(status_akun=status_filter)
    if kab_filter:
        qs = qs.filter(kabupaten=kab_filter)
        
    # ===== LOGIKA FILTER GANDA & VALIDASI PANJANG =====
    if dup_filter:
        if dup_filter == 'nik':
            dup_values = Peserta.objects.values('nik').annotate(cnt=Count('nik')).filter(cnt__gt=1).exclude(nik__isnull=True).exclude(nik='').values('nik')
            qs = qs.filter(nik__in=dup_values)
        elif dup_filter == 'nip':
            dup_values = Peserta.objects.values('nip').annotate(cnt=Count('nip')).filter(cnt__gt=1).exclude(nip__isnull=True).exclude(nip='').values('nip')
            qs = qs.filter(nip__in=dup_values)
        elif dup_filter == 'nuptk':
            dup_values = Peserta.objects.values('nuptk').annotate(cnt=Count('nuptk')).filter(cnt__gt=1).exclude(nuptk__isnull=True).exclude(nuptk='').values('nuptk')
            qs = qs.filter(nuptk__in=dup_values)
        elif dup_filter == 'nama':
            dup_values = Peserta.objects.values('nama_normalized').annotate(cnt=Count('nama_normalized')).filter(cnt__gt=1).values('nama_normalized')
            qs = qs.filter(nama_normalized__in=dup_values)
            
        # --- VALIDASI PANJANG DIGIT ---
        elif dup_filter == 'nik_salah':
            qs = qs.exclude(nik__isnull=True).exclude(nik='').annotate(nik_len=Length('nik')).exclude(nik_len=16)
        elif dup_filter == 'nip_salah':
            qs = qs.exclude(nip__isnull=True).exclude(nip='').annotate(nip_len=Length('nip')).exclude(nip_len=18)
        elif dup_filter == 'nuptk_salah':
            qs = qs.exclude(nuptk__isnull=True).exclude(nuptk='').annotate(nuptk_len=Length('nuptk')).exclude(nuptk_len=16)

    qs = qs.order_by('nama_normalized')
    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get('page'))

    kabupaten_list = cache.get('list_kabupaten')
    if not kabupaten_list:
        kabupaten_list = list(Peserta.objects.exclude(kabupaten='').values_list('kabupaten', flat=True).distinct().order_by('kabupaten'))
        cache.set('list_kabupaten', kabupaten_list, timeout=86400)

    return render(request, 'peserta/admin/peserta_list.html', {
        'page': page, 'total': qs.count(), 'q': q,
        'status_filter': status_filter, 'kab_filter': kab_filter,
        'dup_filter': dup_filter,
        'kabupaten_list': kabupaten_list,
        'status_choices': Peserta.STATUS_CHOICES,
    })



@admin_required
def peserta_detail(request, pk):
    peserta = get_object_or_404(Peserta, pk=pk)
    perbaikan = peserta.perbaikan.select_related(
        'reviewed_by').order_by('-created_at')[:20]
    aktivasi = peserta.pengajuan_aktivasi.order_by('-created_at')[:5]
    login_log = LogLogin.objects.filter(
        peserta=peserta).order_by('-login_at')[:10]
    notifikasi = peserta.notifikasi.order_by('-created_at')[:10]

    return render(request, 'peserta/admin/peserta_detail.html', {
        'peserta': peserta, 'perbaikan': perbaikan, 'aktivasi': aktivasi,
        'login_log': login_log, 'notifikasi': notifikasi,
    })


@admin_required
@require_http_methods(['GET', 'POST'])
def peserta_edit(request, pk):
    peserta = get_object_or_404(Peserta, pk=pk)

    if request.method == 'POST':
        editable = ['nama', 'nuptk', 'nip', 'nik', 'tempat_tugas', 'npsn',
                    'kecamatan', 'kabupaten', 'provinsi', 'jenjang', 'jabatan',
                    'nomor_wa', 'status_akun',
                    'nomor_rekening', 'bank', 'atas_nama_rekening']
        account_fields = {'nomor_rekening', 'bank', 'atas_nama_rekening'}
        old_account_values = {
            field: getattr(peserta, field) for field in account_fields
        }

        for field in editable:
            if field in request.POST:
                val = request.POST.get(field, '').strip()
                max_lengths = {'nama': 255, 'nuptk': 20, 'nip': 20, 'nik': 20,
                               'tempat_tugas': 255, 'npsn': 20, 'kecamatan': 100,
                               'kabupaten': 100, 'provinsi': 100, 'jenjang': 20,
                               'jabatan': 100, 'nomor_wa': 20, 'status_akun': 20,
                               'nomor_rekening': 50, 'bank': 50, 'atas_nama_rekening': 255}
                if len(val) > max_lengths[field]:
                    messages.error(request, f'Nilai {field} terlalu panjang.')
                    return redirect('admin_panel:peserta_edit', pk=pk)
                if field in {'nip', 'nik', 'nuptk'} and val and not val.isdigit():
                    messages.error(request, f'{field.upper()} harus berisi angka saja.')
                    return redirect('admin_panel:peserta_edit', pk=pk)
                if field == 'status_akun' and val not in dict(Peserta.STATUS_CHOICES):
                    messages.error(request, 'Status akun tidak valid.')
                    return redirect('admin_panel:peserta_edit', pk=pk)
                nullable_fields = ('nip', 'nik', 'nuptk', 'nomor_rekening', 'bank', 'atas_nama_rekening')
                
                if field in nullable_fields:
                    setattr(peserta, field, val if val else None)
                else:
                    setattr(peserta, field, val)

        profile_fields = {
            'nuptk', 'nip', 'nik', 'tempat_tugas', 'npsn',
            'kecamatan', 'kabupaten', 'provinsi', 'jenjang', 'jabatan'
        }
        fields_without_data = set(peserta.data_tidak_dimiliki or [])
        for field in profile_fields:
            if getattr(peserta, field):
                fields_without_data.discard(field)
        peserta.data_tidak_dimiliki = sorted(fields_without_data)

        from .utils import normalisasi_nama, derive_tanggal_lahir
        norm, gelar = normalisasi_nama(peserta.nama)
        peserta.nama_normalized = norm
        peserta.nama_gelar = gelar

        peserta.tanggal_lahir = derive_tanggal_lahir(peserta.nip, peserta.nik)
        if any(getattr(peserta, field) != old_value
               for field, old_value in old_account_values.items()) or any(
                   field in request.POST for field in profile_fields | {'nama'}
               ):
            peserta.is_data_verified = False
            peserta.verified_at = None

        peserta.save()

        LogAdminAction.objects.create(
            admin=request.user, action='edit_peserta',
            target_type='Peserta', target_id=peserta.id,
            details=f'Nama: {peserta.nama}',
            ip_address=get_client_ip(request),
        )

        messages.success(request, f'Data {peserta.nama} berhasil diperbarui.')
        
        # Cek apakah ada parameter filter yang dikirim
        return_url = request.POST.get('return_url', '')
        if return_url:
            # Kembali ke list dengan filter yang sama
            return redirect(f"{reverse('admin_panel:peserta_list')}?{return_url}")
            
        # Jika tidak ada filter (misal edit dari halaman detail), kembali ke detail
        return redirect('admin_panel:peserta_detail', pk=peserta.id)

    return render(request, 'peserta/admin/peserta_edit.html', {'peserta': peserta})


# ================= NOTIFIKASI =================
@admin_required
def notifikasi_list(request):
    q = request.GET.get('q', '').strip()
    tipe_filter = request.GET.get('tipe', '')

    qs = NotifikasiInApp.objects.select_related('peserta')
    if q:
        qs = qs.filter(Q(peserta__nama__icontains=q) | Q(judul__icontains=q))
    if tipe_filter:
        qs = qs.filter(tipe=tipe_filter)
    qs = qs.order_by('-created_at')

    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'peserta/admin/notifikasi_list.html', {
        'page': page, 'total': qs.count(), 'q': q,
        'tipe_filter': tipe_filter,
        'tipe_choices': NotifikasiInApp.TIPE_CHOICES,
    })


@admin_required
@require_http_methods(['GET', 'POST'])
def notifikasi_send(request):
    if request.method == 'POST':
        peserta_id = request.POST.get('peserta_id', '').strip()
        tipe = request.POST.get('tipe', 'info_umum')
        judul = request.POST.get('judul', '').strip()
        isi = request.POST.get('isi', '').strip()

        if not peserta_id or not judul or not isi:
            messages.error(request, 'Peserta, judul, dan isi wajib diisi.')
        else:
            try:
                peserta = Peserta.objects.get(id=peserta_id)
                NotifikasiInApp.objects.create(
                    peserta=peserta, tipe=tipe, judul=judul, isi=isi,
                )
                LogAdminAction.objects.create(
                    admin=request.user, action='kirim_notifikasi',
                    target_type='Peserta', target_id=peserta.id,
                    details=f'Judul: {judul}',
                    ip_address=get_client_ip(request),
                )
                messages.success(
                    request, f'Notifikasi terkirim ke {peserta.nama}.')
                return redirect('admin_panel:notifikasi_list')
            except Peserta.DoesNotExist:
                messages.error(request, 'Peserta tidak ditemukan.')

    if request.GET.get('q'):
        peserta_qs = Peserta.objects.filter(
            Q(nama__icontains=request.GET['q']) |
            Q(nik__icontains=request.GET['q']) |
            Q(nip__icontains=request.GET['q'])
        )[:20]
    else:
        peserta_qs = Peserta.objects.all()[:20]

    return render(request, 'peserta/admin/notifikasi_send.html', {
        'peserta_qs': peserta_qs,
        'tipe_choices': NotifikasiInApp.TIPE_CHOICES,
    })


@admin_required
def notifikasi_detail(request, pk):
    notif = get_object_or_404(
        NotifikasiInApp.objects.select_related('peserta'), pk=pk)
    return render(request, 'peserta/admin/notifikasi_detail.html', {'notif': notif})


@admin_required
@require_POST
def notifikasi_toggle_read(request, pk):
    notif = get_object_or_404(NotifikasiInApp, pk=pk)
    notif.sudah_dibaca = not notif.sudah_dibaca
    notif.read_at = timezone.now() if notif.sudah_dibaca else None
    notif.save(update_fields=['sudah_dibaca', 'read_at'])

    status = "dibaca" if notif.sudah_dibaca else "belum dibaca"
    messages.success(request, f'Notifikasi ditandai sebagai {status}.')
    return redirect('admin_panel:notifikasi_list')


@admin_required
@require_POST
def notifikasi_delete(request, pk):
    notif = get_object_or_404(NotifikasiInApp, pk=pk)
    notif.delete()
    messages.success(request, 'Notifikasi berhasil dihapus.')
    return redirect('admin_panel:notifikasi_list')


@admin_required
@require_POST
def notifikasi_delete_all(request):
    count, _ = NotifikasiInApp.objects.all().delete()
    messages.success(
        request, f'Semua notifikasi berhasil dihapus ({count} data).')
    return redirect('admin_panel:notifikasi_list')


# ================= LOG =================
@admin_required
def log_list(request):
    tab = request.GET.get('tab', 'login')
    q = request.GET.get('q', '').strip()

    if tab == 'login':
        qs = LogLogin.objects.select_related('peserta')
        if q:
            qs = qs.filter(Q(peserta__nama__icontains=q)
                           | Q(ip_address__icontains=q))
        qs = qs.order_by('-login_at')
    elif tab == 'admin':
        qs = LogAdminAction.objects.select_related('admin')
        if q:
            qs = qs.filter(Q(admin__username__icontains=q)
                           | Q(action__icontains=q))
        qs = qs.order_by('-created_at')
    else:  # contact
        qs = LogContact.objects.select_related('peserta', 'admin')
        if q:
            qs = qs.filter(Q(peserta__nama__icontains=q)
                           | Q(nomor_tujuan__icontains=q))
        qs = qs.order_by('-sent_at')

    paginator = Paginator(qs, 100)
    page = paginator.get_page(request.GET.get('page'))

    return render(request, 'peserta/admin/log_list.html', {
        'page': page, 'tab': tab, 'q': q, 'total': qs.count(),
    })


# ================= PENGATURAN =================
@admin_required
@require_http_methods(['GET', 'POST'])
def pengaturan(request):
    setting = PengaturanSistem.get()

    if request.method == 'POST':
        try:
            values = {
                'batas_pending_aktivasi': int(request.POST.get('batas_pending_aktivasi', 50)),
                'batas_draft_aktivasi': int(request.POST.get('batas_draft_aktivasi', 10)),
                'durasi_draft_menit': int(request.POST.get('durasi_draft_menit', 30)),
                'auto_reject_pending_hari': int(request.POST.get('auto_reject_pending_hari', 7)),
                'ukuran_file_maksimal_mb': int(request.POST.get('ukuran_file_maksimal_mb', 2)),
            }
            limits = {
                'batas_pending_aktivasi': (1, 100000),
                'batas_draft_aktivasi': (1, 100000),
                'durasi_draft_menit': (1, 525600),
                'auto_reject_pending_hari': (1, 3650),
                'ukuran_file_maksimal_mb': (1, 20),
            }
            for name, value in values.items():
                minimum, maximum = limits[name]
                if not minimum <= value <= maximum:
                    raise ValueError(f'{name} harus antara {minimum} dan {maximum}.')
            for name, value in values.items():
                setattr(setting, name, value)
            setting.save()
            cache.delete('pengaturan_sistem')
            cache.delete('landing_stats')
            LogAdminAction.objects.create(
                admin=request.user, action='edit_pengaturan',
                target_type='PengaturanSistem', target_id=setting.id,
                ip_address=get_client_ip(request),
            )
            messages.success(request, 'Pengaturan tersimpan.')
        except (ValueError, TypeError) as e:
            messages.error(request, f'Input tidak valid: {e}')
        return redirect('admin_panel:pengaturan')

    return render(request, 'peserta/admin/pengaturan.html', {'setting': setting})


# ================= EXPORT EXCEL (DENGAN FORMAT TEKS) =================
# ================= EXPORT EXCEL =================
@admin_required
def export_excel(request):
    """Halaman form filter export."""
    kabupaten_list = cache.get('list_kabupaten')
    if not kabupaten_list:
        kabupaten_list = list(
            Peserta.objects.exclude(kabupaten='')
            .values_list('kabupaten', flat=True)
            .distinct().order_by('kabupaten')
        )
        cache.set('list_kabupaten', kabupaten_list, timeout=86400)

    jenjang_list = list(
        Peserta.objects.exclude(jenjang='')
        .values_list('jenjang', flat=True)
        .distinct().order_by('jenjang')
    )

    # Preview statistik
    total_all = Peserta.objects.count()
    total_verified = Peserta.objects.filter(is_data_verified=True).count()
    total_with_rek = Peserta.objects.exclude(
        nomor_rekening__isnull=True).exclude(nomor_rekening='').count()
    total_belum_identitas = Peserta.objects.filter(
        status_akun='belum_identitas').count()

    return render(request, 'peserta/admin/export.html', {
        'kabupaten_list': kabupaten_list,
        'jenjang_list': jenjang_list,
        'status_choices': Peserta.STATUS_CHOICES,
        'total_all': total_all,
        'total_verified': total_verified,
        'total_with_rek': total_with_rek,
        'total_belum_identitas': total_belum_identitas,
    })


@admin_required
def export_excel_download(request):
    try:
        return _export_excel_download_inner(request)
    except Exception as e:
        import traceback
        traceback.print_exc()
        messages.error(
            request,
            f'Export gagal: {type(e).__name__}: {e}'
        )
        return redirect('admin_panel:export_excel')


def _export_excel_download_inner(request):
    """Isi asli fungsi — nama baru, dipanggil dari wrapper try/except."""
    q = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    kab_filter = request.GET.get('kabupaten', '').strip()
    jenjang_filter = request.GET.get('jenjang', '').strip()
    has_rekening = request.GET.get('has_rekening', '').strip()
    verified = request.GET.get('verified', '').strip()
    scope = request.GET.get('scope', 'filter')  # 'filter' atau 'full'

    qs = Peserta.objects.all()

    # Kalau scope=full → abaikan semua filter
    if scope != 'full':
        if q:
            qs = qs.filter(
                Q(nama__icontains=q) | Q(nik__icontains=q)
                | Q(nip__icontains=q) | Q(nuptk__icontains=q)
            )
        if status_filter:
            qs = qs.filter(status_akun=status_filter)
        if kab_filter:
            qs = qs.filter(kabupaten=kab_filter)
        if jenjang_filter:
            qs = qs.filter(jenjang=jenjang_filter)
        # EncryptedCharField: bandingkan '' tidak berguna karena ciphertext
        # non-deterministic. Cukup pakai isnull (NULL = belum diisi).
        if has_rekening == 'yes':
            qs = qs.exclude(nomor_rekening__isnull=True)
        elif has_rekening == 'no':
            qs = qs.filter(nomor_rekening__isnull=True)
        if verified == 'yes':
            qs = qs.filter(is_data_verified=True)
        elif verified == 'no':
            qs = qs.filter(is_data_verified=False)

    qs = qs.order_by('nama_normalized')
    total = qs.count()

    # === Build filename ===
    suffix_parts = []
    if scope == 'full':
        suffix_parts.append('full')
    else:
        if status_filter:
            suffix_parts.append(status_filter)
        if kab_filter:
            suffix_parts.append(kab_filter[:20])
        if jenjang_filter:
            suffix_parts.append(jenjang_filter)
        if has_rekening == 'yes':
            suffix_parts.append('dengan-rekening')
        elif has_rekening == 'no':
            suffix_parts.append('tanpa-rekening')
        if verified == 'yes':
            suffix_parts.append('verified')
        elif verified == 'no':
            suffix_parts.append('unverified')
        if q:
            suffix_parts.append(f'q-{q[:15]}')
        if not suffix_parts:
            suffix_parts.append('filter')
    suffix = '-'.join(suffix_parts).replace(' ', '_').replace('/', '-')[:80]
    stamp = timezone.localtime(timezone.now()).strftime('%Y%m%d-%H%M%S')
    filename = f'data-peserta-{suffix}-{stamp}.xlsx'

    # === Build workbook ===
    wb = Workbook(write_only=True)
    ws = wb.create_sheet('Data Peserta')

    header_font = Font(bold=True, color='FFFFFF', size=11)
    header_fill = PatternFill('solid', fgColor='2563EB')
    header_align = Alignment(
        horizontal='center', vertical='center', wrap_text=True)

    title_font = Font(bold=True, size=14, color='2563EB')
    meta_font = Font(bold=True, size=11, color='1E40AF')
    meta_fill = PatternFill('solid', fgColor='DBEAFE')

    TEXT_FORMAT = '@'  # Excel text format (cegah scientific notation)

    col_widths = [5, 18, 20, 20, 30, 30, 12, 18,
                  20, 20, 10, 20, 15, 15, 22, 12, 25]
    for i, width in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width

    # === METADATA ===
    now_wib = timezone.localtime(timezone.now())
    meta_rows = [
        ['LAPORAN EXPORT DATA PESERTA'],
        [f'Dibuat: {now_wib.strftime("%d %B %Y, %H:%M:%S")} WIB'],
        [f'Scope: {"SEMUA DATA (FULL)" if scope == "full" else "TERFILTER"}'],
        [f'Total baris: {total}'],
    ]

    filter_info = []
    if scope != 'full':
        if q:
            filter_info.append(f'Pencarian: {q}')
        if status_filter:
            filter_info.append(
                f'Status: {dict(Peserta.STATUS_CHOICES).get(status_filter, status_filter)}')
        if kab_filter:
            filter_info.append(f'Kabupaten: {kab_filter}')
        if jenjang_filter:
            filter_info.append(f'Jenjang: {jenjang_filter}')
        if has_rekening:
            filter_info.append(
                f'Rekening: {"Ada" if has_rekening == "yes" else "Kosong"}')
        if verified:
            filter_info.append(
                f'Verified: {"Ya" if verified == "yes" else "Tidak"}')

    if scope == 'full':
        meta_rows.append(['Filter: (FULL EXPORT — tidak ada filter)'])
    elif filter_info:
        meta_rows.append(['Filter: ' + ' | '.join(filter_info)])
    else:
        meta_rows.append(['Filter: (tidak ada filter — semua data)'])
    meta_rows.append([])  # spacer

    for i, row_text in enumerate(meta_rows, start=1):
        if not row_text:
            ws.append([])
            continue
        cell = WriteOnlyCell(ws, value=row_text[0])
        if i == 1:
            cell.font = title_font
            cell.alignment = Alignment(horizontal='center')
        elif i <= 5:
            cell.font = meta_font
            cell.fill = meta_fill
        ws.append([cell])

    ws.append([])  # spacer sebelum header

    # === HEADER ===
    headers = ['No', 'NUPTK', 'NIP', 'NIK', 'Nama', 'Tempat Tugas',
               'NPSN', 'Kecamatan', 'Kabupaten', 'Provinsi', 'Jenjang',
               'Jabatan', 'Status Akun', 'Nomor WA', 'Nomor Rekening',
               'Bank', 'Atas Nama Rekening']

    header_row = []
    for text in headers:
        cell = WriteOnlyCell(ws, value=text)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        header_row.append(cell)
    ws.append(header_row)

    # === DATA ===
    TEXT_COLS = {1, 2, 3, 14}  # NUPTK, NIP, NIK, Nomor Rekening

    SAFE_EMPTY = '[DECRYPT ERROR]'
    baris_rusak = 0

    for i, p in enumerate(qs.iterator(chunk_size=2000), start=1):
        # Akses field terenkripsi lewat helper aman
        try:
            rek = p.nomor_rekening or ''
        except Exception:
            rek = SAFE_EMPTY
            baris_rusak += 1
        try:
            bank = p.bank or ''
        except Exception:
            bank = SAFE_EMPTY
        try:
            atas_nama = p.atas_nama_rekening or ''
        except Exception:
            atas_nama = SAFE_EMPTY

        row_data = [
            i,
            p.nuptk or '',
            p.nip or '',
            p.nik or '',
            p.nama or '',
            p.tempat_tugas or '',
            p.npsn or '',
            p.kecamatan or '',
            p.kabupaten or '',
            p.provinsi or '',
            p.jenjang or '',
            p.jabatan or '',
            p.get_status_akun_display(),
            p.nomor_wa or '',
            rek,
            bank,
            atas_nama,
        ]
        formatted_row = []
        for idx, val in enumerate(row_data):
            if idx in TEXT_COLS and val != '':
                cell = WriteOnlyCell(ws, value=str(val))
                cell.number_format = TEXT_FORMAT
                formatted_row.append(cell)
            else:
                formatted_row.append(val)
        ws.append(formatted_row)

    # === RESPONSE ===
    # Log warning sekali saja (bukan per iterasi)
    if baris_rusak > 0:
        try:
            LogAdminAction.objects.create(
                admin=request.user,
                action='export_excel_partial',
                target_type='Peserta',
                details=(
                    f'{baris_rusak} baris punya rekening terenkripsi rusak. '
                    f'File: {filename}'
                ),
                ip_address=get_client_ip(request),
            )
        except Exception:
            pass
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)

    try:
        LogAdminAction.objects.create(
            admin=request.user, action='export_excel',
            target_type='Peserta',
            details=f'Export {total} baris — scope={scope}, file={filename}',
            ip_address=get_client_ip(request),
        )
    except Exception:
        pass

    return response


# ================= PESERTA SESUAI =================
@admin_required
def peserta_sesuai_list(request):
    q = request.GET.get('q', '').strip()
    kab_filter = request.GET.get('kabupaten', '')

    qs = Peserta.objects.filter(is_data_verified=True)

    if q:
        qs = qs.filter(Q(nama__icontains=q) | Q(
            nik__icontains=q) | Q(nip__icontains=q))
    if kab_filter:
        qs = qs.filter(kabupaten=kab_filter)

    qs = qs.order_by('-verified_at')

    paginator = Paginator(qs, 25)
    page = paginator.get_page(request.GET.get('page'))

    kabupaten_list = cache.get('list_kabupaten')
    if not kabupaten_list:
        kabupaten_list = list(Peserta.objects.exclude(kabupaten='').values_list(
            'kabupaten', flat=True).distinct().order_by('kabupaten'))
        cache.set('list_kabupaten', kabupaten_list, timeout=86400)

    return render(request, 'peserta/admin/peserta_sesuai.html', {
        'page': page, 'total': qs.count(), 'q': q,
        'kab_filter': kab_filter, 'kabupaten_list': kabupaten_list,
    })


@admin_required
@require_POST
def peserta_batal_sesuai(request, pk):
    peserta = get_object_or_404(Peserta, pk=pk)
    peserta.is_data_verified = False
    peserta.verified_at = None
    peserta.save(update_fields=['is_data_verified', 'verified_at'])

    LogAdminAction.objects.create(
        admin=request.user, action='batal_verifikasi_peserta',
        target_type='Peserta', target_id=peserta.id,
        details=f'Membatalkan status data sesuai untuk {peserta.nama}',
        ip_address=get_client_ip(request),
    )
    messages.warning(
        request, f'Status verifikasi untuk {peserta.nama} telah dibatalkan.')
    return redirect('admin_panel:peserta_sesuai_list')


# ================= HELPERS =================
def get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def hapus_file_dokumen(dokumen):
    from django.conf import settings
    from django.utils._os import safe_join
    from django.db import transaction
    try:
        abs_path = safe_join(str(settings.PRIVATE_MEDIA_ROOT), dokumen.file_path)
    except (ValueError, SuspiciousFileOperation):
        abs_path = None
    dokumen.deleted_at = timezone.now()
    dokumen.save(update_fields=['deleted_at'])
    if abs_path:
        def remove_after_commit():
            try:
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
            except OSError:
                pass
        transaction.on_commit(remove_after_commit)
