# peserta/decorators.py
from functools import wraps
from django.shortcuts import redirect
from django.urls import reverse


def admin_required(view_func):
    """
    Decorator custom: wajib login sebagai admin (staff).
    Redirect ke /panel/login/ (BUKAN /admin/login/).
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if (not request.user.is_authenticated or not request.user.is_active
                or not request.user.is_staff):
            login_url = reverse('admin_panel:login')
            return redirect(f"{login_url}?next={request.path}")
        return view_func(request, *args, **kwargs)
    return _wrapped
