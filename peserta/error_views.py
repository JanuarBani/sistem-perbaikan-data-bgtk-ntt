# peserta/error_views.py
from django.shortcuts import render


def _common_context(request):
    """Konteks yang dipakai bersama di semua error page."""
    is_admin = request.path.startswith('/panel/')
    return {
        'back_url': '/panel/' if is_admin else '/',
        'back_text': 'Dashboard Admin' if is_admin else 'Beranda',
        'is_admin_area': is_admin,
    }


def custom_404(request, exception=None):
    return render(
        request, 'peserta/404.html',
        {**_common_context(request), 'exception': exception},
        status=404,
    )


def custom_500(request):
    return render(
        request, 'peserta/500.html',
        _common_context(request),
        status=500,
    )


def custom_403(request, exception=None):
    return render(
        request, 'peserta/403.html',
        {**_common_context(request), 'exception': exception},
        status=403,
    )


def custom_400(request, exception=None):
    return render(
        request, 'peserta/400.html',
        {**_common_context(request), 'exception': exception},
        status=400,
    )
