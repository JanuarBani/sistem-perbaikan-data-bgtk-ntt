# peserta/management/commands/cleanup.py
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import os
from peserta.models import PengajuanAktivasi, DokumenAktivasi, LogLogin, NotifikasiInApp, PengaturanSistem


class Command(BaseCommand):
    help = 'Cleanup file & log lama'

    def handle(self, *args, **kwargs):
        setting = PengaturanSistem.get()
        now = timezone.now()

        # Auto-reject pending, synchronize participant status, and notify.
        batas = now - timedelta(days=setting.auto_reject_pending_hari)
        lama = PengajuanAktivasi.objects.filter(
            status='pending', created_at__lt=batas)
        count_reject = 0
        for p in lama:
            for d in p.dokumen.all():
                hapus_file_dokumen(d)
            p.status = 'rejected'
            p.catatan_admin = 'Auto-reject: waktu verifikasi habis.'
            p.reviewed_at = now
            p.save(update_fields=['status', 'catatan_admin', 'reviewed_at'])
            p.peserta.status_akun = 'rejected'
            p.peserta.save(update_fields=['status_akun'])
            NotifikasiInApp.objects.create(
                peserta=p.peserta,
                tipe='aktivasi_rejected',
                judul='Aktivasi ditolak otomatis',
                isi='Pengajuan melewati batas waktu verifikasi. Silakan ajukan ulang.',
                link_terkait='/cari/',
            )
            count_reject += 1
        self.stdout.write(f'Auto-reject: {count_reject} pengajuan')

        # 2. Hapus draft > durasi draft
        draft_batas = now - timedelta(minutes=setting.durasi_draft_menit)
        draft_lama = PengajuanAktivasi.objects.filter(
            status='draft', created_at__lt=draft_batas)
        for p in draft_lama:
            for d in p.dokumen.all():
                hapus_file_dokumen(d)
            p.delete()

        # 3. Hapus log login > 90 hari
        log_batas = now - timedelta(days=90)
        log_deleted, _ = LogLogin.objects.filter(
            login_at__lt=log_batas).delete()
        self.stdout.write(f'Log login dihapus: {log_deleted}')

        # 4. Hapus notifikasi > 6 bulan
        notif_batas = now - timedelta(days=180)
        notif_deleted, _ = NotifikasiInApp.objects.filter(
            created_at__lt=notif_batas).delete()
        self.stdout.write(f'Notifikasi dihapus: {notif_deleted}')


def hapus_file_dokumen(dokumen):
    from django.conf import settings
    from django.utils._os import safe_join
    from django.core.exceptions import SuspiciousFileOperation
    try:
        abs_path = safe_join(str(settings.PRIVATE_MEDIA_ROOT), dokumen.file_path)
    except (ValueError, SuspiciousFileOperation):
        abs_path = None
    if abs_path and os.path.exists(abs_path):
        try:
            os.remove(abs_path)
        except OSError:
            pass
    dokumen.deleted_at = timezone.now()
    dokumen.save(update_fields=['deleted_at'])
