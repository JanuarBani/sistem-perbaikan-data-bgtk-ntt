# project/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),                   # ← cadangan untuk reset password
    path('panel/', include('peserta.urls_admin')),    # ← admin panel custom
    path('', include('peserta.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


# === CUSTOM ERROR HANDLERS ===
# Handler hanya aktif saat DEBUG=False
handler400 = 'peserta.error_views.custom_400'
handler403 = 'peserta.error_views.custom_403'
handler404 = 'peserta.error_views.custom_404'
handler500 = 'peserta.error_views.custom_500'
