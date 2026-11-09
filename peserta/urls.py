# peserta/urls.py
from django.urls import path
from . import views

app_name = 'peserta'

urlpatterns = [
    path('', views.landing, name='landing'),
    path('persetujuan/', views.persetujuan, name='persetujuan'),
    path('cari/', views.cari, name='cari'),
    path('verifikasi/<int:peserta_id>/', views.verifikasi, name='verifikasi'),
    path('aktivasi/<int:peserta_id>/', views.aktivasi, name='aktivasi'),
    path('aktivasi/status/<str:ref>/',
         views.aktivasi_status, name='aktivasi_status'),
    path('dokumen/<int:pk>/', views.dokumen_aktivasi, name='dokumen_aktivasi'),
    path('login-wa/', views.popup_wa, name='popup_wa'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('perbaikan/<str:field_name>/', views.perbaikan_form, name='perbaikan'),
    path('perbaikan/<str:field_name>/tidak-dimiliki/',
         views.ajukan_tidak_dimiliki, name='ajukan_tidak_dimiliki'),
    path('tandai-sesuai/', views.tandai_data_sesuai, name='tandai_data_sesuai'),
    path('notifikasi/', views.notifikasi_list, name='notifikasi'),
    path('notifikasi/<int:notif_id>/baca/',
         views.notifikasi_baca, name='notifikasi_baca'),
    path('notifikasi/<int:notif_id>/hapus/',
         views.notifikasi_hapus, name='notifikasi_hapus'),
    path('notifikasi/hapus-semua/',
         views.notifikasi_hapus_semua, name='notifikasi_hapus_semua'),
    path('logout/', views.logout_view, name='logout'),
]
