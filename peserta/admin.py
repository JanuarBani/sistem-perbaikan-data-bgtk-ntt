# peserta/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    Admin, Peserta, Perbaikan, PengajuanAktivasi, DokumenAktivasi,
    NotifikasiInApp, LogContact, LogLogin, LogAdminAction, PengaturanSistem
)


@admin.register(Admin)
class CustomAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('Info Tambahan', {'fields': ('nama_lengkap', 'is_superadmin')}),
    )


@admin.register(Peserta)
class PesertaAdmin(admin.ModelAdmin):
    list_display = ('nama', 'nip', 'nik', 'nuptk', 'kabupaten', 'status_akun')
    search_fields = ('nama', 'nip', 'nik', 'nuptk')
    list_filter = ('status_akun', 'kabupaten', 'jenjang')
    readonly_fields = ('rekening_masked',)


@admin.register(Perbaikan)
class PerbaikanAdmin(admin.ModelAdmin):
    list_display = ('peserta', 'field_name', 'status', 'created_at')
    list_filter = ('status', 'field_name')
    search_fields = ('peserta__nama',)


@admin.register(PengajuanAktivasi)
class PengajuanAktivasiAdmin(admin.ModelAdmin):
    list_display = ('nomor_referensi', 'peserta', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('nomor_referensi', 'peserta__nama')


@admin.register(NotifikasiInApp)
class NotifikasiAdmin(admin.ModelAdmin):
    list_display = ('peserta', 'tipe', 'judul', 'sudah_dibaca', 'created_at')
    list_filter = ('tipe', 'sudah_dibaca')


@admin.register(PengaturanSistem)
class PengaturanAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not PengaturanSistem.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# Register sisanya sebagai read-only
admin.site.register(DokumenAktivasi)
admin.site.register(LogContact)
admin.site.register(LogLogin)
admin.site.register(LogAdminAction)

admin.site.site_header = 'Admin Perbaikan Data Peserta'
admin.site.site_title = 'Admin Panel'
admin.site.index_title = 'Manajemen Data'
