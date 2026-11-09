# peserta/utils_admin.py
import os
from urllib.parse import quote
from django.conf import settings
from django.utils import timezone
from django.core.exceptions import SuspiciousFileOperation


WA_TEMPLATES = {
    'aktivasi_approved': (
        'Aktivasi Disetujui',
        'Yth. {nama},\n\n'
        'Pengajuan aktivasi akun Anda telah DISETUJUI.\n'
        'No. Referensi: {ref}\n\n'
        'Silakan login di sistem dengan NIK/NIP/NUPTK yang Anda daftarkan.\n\n'
        'Terima kasih.'
    ),
    'aktivasi_rejected': (
        'Aktivasi Ditolak',
        'Yth. {nama},\n\n'
        'Pengajuan aktivasi akun Anda DITOLAK.\n'
        'No. Referensi: {ref}\n'
        'Alasan: {alasan}\n\n'
        'Silakan ajukan ulang dengan dokumen yang lebih jelas.\n\n'
        'Terima kasih.'
    ),
    'rekening_berubah': (
        'Rekening Berubah',
        'Yth. {nama},\n\n'
        'Rekening Anda di sistem kami telah berubah pada {waktu}.\n'
        'Bank: {bank}\n'
        'No. Rekening: {rekening}\n\n'
        'Jika ini BUKAN Anda, segera hubungi admin.\n\n'
        'Terima kasih.'
    ),
    'minta_upload_ulang': (
        'Minta Upload Ulang',
        'Yth. {nama},\n\n'
        'Data Anda perlu diperbaiki: {field}.\n'
        'Alasan: {alasan}\n\n'
        'Silakan login dan kirim ulang data yang benar.\n\n'
        'Terima kasih.'
    ),
    'perbaikan_approved': (
        'Perbaikan Disetujui',
        'Yth. {nama},\n\n'
        'Perbaikan data {field} Anda telah DISETUJUI dan diterapkan.\n\n'
        'Terima kasih.'
    ),
    'perbaikan_rejected': (
        'Perbaikan Ditolak',
        'Yth. {nama},\n\n'
        'Perbaikan data {field} Anda DITOLAK.\n'
        'Alasan: {alasan}\n\n'
        'Silakan ajukan ulang jika perlu.\n\n'
        'Terima kasih.'
    ),
}


def generate_wa_link(nomor_wa, template_key, **kwargs):
    """
    Return (link_wa, isi_pesan).
    nomor_wa harus format 628xxx.
    """
    if not nomor_wa:
        return None, None
    nomor_wa = str(nomor_wa).strip()
    if not nomor_wa.startswith('62'):
        return None, None

    tpl = WA_TEMPLATES.get(template_key)
    if not tpl:
        return None, None
    _, body = tpl

    try:
        pesan = body.format(**kwargs)
    except KeyError:
        pesan = body

    link = f'https://wa.me/{nomor_wa}?text={quote(pesan)}'
    return link, pesan


def hapus_file_dokumen(dokumen, admin=None, request_ip=None):
    """Hapus file fisik + set deleted_at + log."""
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

    if admin:
        from .models import LogAdminAction
        LogAdminAction.objects.create(
            admin=admin,
            action='hapus_dokumen',
            target_type='DokumenAktivasi',
            target_id=dokumen.id,
            details=f'File {dokumen.file_name_original} dihapus',
            ip_address=request_ip,
        )


def hapus_semua_dokumen_pengajuan(pengajuan, admin=None, request_ip=None):
    """Hapus semua dokumen milik pengajuan (setelah approve/reject)."""
    for d in pengajuan.dokumen.all():
        if d.deleted_at is None:
            hapus_file_dokumen(d, admin=admin, request_ip=request_ip)


def get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')
