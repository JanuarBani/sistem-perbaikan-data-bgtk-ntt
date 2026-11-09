# peserta/urls_admin.py
from django.urls import path
from . import views_admin

app_name = 'admin_panel'

urlpatterns = [
    # Auth
    path('login/', views_admin.admin_login, name='login'),
    path('logout/', views_admin.admin_logout, name='logout'),

    # Dashboard
    path('', views_admin.dashboard, name='dashboard'),

    # Perbaikan
    path('perbaikan/', views_admin.perbaikan_list, name='perbaikan_list'),
    path('perbaikan/<int:pk>/', views_admin.perbaikan_detail,
         name='perbaikan_detail'),

    # Aktivasi
    path('aktivasi/', views_admin.aktivasi_list, name='aktivasi_list'),
    path('aktivasi/<int:pk>/', views_admin.aktivasi_detail, name='aktivasi_detail'),

    # Peserta
    path('peserta/', views_admin.peserta_list, name='peserta_list'),
    path('peserta/<int:pk>/', views_admin.peserta_detail, name='peserta_detail'),
    path('peserta/<int:pk>/edit/', views_admin.peserta_edit, name='peserta_edit'),

    path('peserta-sesuai/', views_admin.peserta_sesuai_list,
         name='peserta_sesuai_list'),
    path('peserta-sesuai/<int:pk>/batal/', views_admin.peserta_batal_sesuai, name='peserta_batal_sesuai'), # <-- TAMBAHKAN INI


    path('perbaikan/bulk/', views_admin.perbaikan_bulk_action,
         name='perbaikan_bulk_action'),

    # Notifikasi
    path('notifikasi/', views_admin.notifikasi_list, name='notifikasi_list'),
    path('notifikasi/kirim/', views_admin.notifikasi_send, name='notifikasi_send'),
    path('notifikasi/<int:pk>/', views_admin.notifikasi_detail,
         name='notifikasi_detail'),  # BARU
    path('notifikasi/<int:pk>/toggle/', views_admin.notifikasi_toggle_read,
         name='notifikasi_toggle_read'),  # BARU
    path('notifikasi/<int:pk>/delete/', views_admin.notifikasi_delete,
         name='notifikasi_delete'),  # BARU
    path('notifikasi/delete-all/', views_admin.notifikasi_delete_all,
         name='notifikasi_delete_all'),  # BARU

    # Log
    path('log/', views_admin.log_list, name='log_list'),

    # Pengaturan
    path('pengaturan/', views_admin.pengaturan, name='pengaturan'),

    # Halaman form filter export
    path('export/', views_admin.export_excel, name='export_excel'),

    # Endpoint generate file (dipanggil dari form)
    path('export/download/', views_admin.export_excel_download, name='export_excel_download'),
]
