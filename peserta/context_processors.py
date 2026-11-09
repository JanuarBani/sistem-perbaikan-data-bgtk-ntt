# peserta/context_processors.py
def notifikasi_count(request):
    """Context processor untuk notifikasi peserta + badge admin."""
    context = {'notif_unread_count': 0, 'pending_perbaikan_count': 0, 'pending_aktivasi_count': 0}

    peserta_id = request.session.get('peserta_id')
    if peserta_id:
        from .models import NotifikasiInApp
        context['notif_unread_count'] = NotifikasiInApp.objects.filter(
            peserta_id=peserta_id, sudah_dibaca=False
        ).count()

    # Badge untuk admin (hanya jika staff login)
    if request.user.is_authenticated and request.user.is_staff:
        from .models import Perbaikan, PengajuanAktivasi
        context['pending_perbaikan_count'] = Perbaikan.objects.filter(status='pending').count()
        context['pending_aktivasi_count'] = PengajuanAktivasi.objects.filter(status='pending').count()

    return context