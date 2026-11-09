# peserta/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser
from encrypted_fields.fields import EncryptedCharField
from django.core.cache import cache


class Admin(AbstractUser):
    """Custom user model — pakai ini sebagai AUTH_USER_MODEL."""
    nama_lengkap = models.CharField(max_length=200, blank=True)
    is_superadmin = models.BooleanField(default=False)

    def __str__(self):
        return self.nama_lengkap or self.username


class Peserta(models.Model):
    STATUS_CHOICES = [
        ('aktif', 'Aktif'),
        ('belum_identitas', 'Belum Punya Identitas'),
        ('pending_aktivasi', 'Menunggu Aktivasi'),
        ('rejected', 'Aktivasi Ditolak'),
    ]

    nip_nik_raw = models.CharField(max_length=50, blank=True,
                                   help_text='Data mentah dari Excel')
    nip = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    nik = models.CharField(max_length=20, null=True, blank=True, db_index=True)
    nuptk = models.CharField(max_length=20, null=True,
                             blank=True, db_index=True)

    nama = models.CharField(max_length=255)
    nama_normalized = models.CharField(max_length=255, db_index=True)
    nama_gelar = models.CharField(max_length=255, blank=True)
    tanggal_lahir = models.DateField(null=True, blank=True)

    tempat_tugas = models.CharField(max_length=255, blank=True)
    npsn = models.CharField(max_length=20, blank=True)
    kecamatan = models.CharField(max_length=100, blank=True)
    kabupaten = models.CharField(max_length=100, blank=True)
    provinsi = models.CharField(max_length=100, blank=True)
    jenjang = models.CharField(max_length=20, blank=True)
    jabatan = models.CharField(max_length=100, blank=True)
    data_tidak_dimiliki = models.JSONField(
        default=list, blank=True,
        help_text='Daftar field yang dinyatakan peserta memang tidak dimiliki.'
    )

    # Field terenkripsi
    nomor_rekening = EncryptedCharField(max_length=50, null=True, blank=True)
    bank = EncryptedCharField(max_length=50, null=True, blank=True)
    atas_nama_rekening = EncryptedCharField(
        max_length=255, null=True, blank=True)

    nomor_wa = models.CharField(max_length=20, blank=True)
    nomor_wa_confirmed = models.BooleanField(default=False)
    status_akun = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='aktif')

    # TAMBAHKAN 2 BARIS INI
    is_data_verified = models.BooleanField(default=False, help_text='Peserta sudah menandai datanya sesuai')
    verified_at = models.DateTimeField(null=True, blank=True)

    failed_login_count = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Peserta'
        ordering = ['nama_normalized']

    def __str__(self):
        return f'{self.nama} ({self.kabupaten})'

    @property
    def rekening_masked(self):
        """Tampilkan 4 digit terakhir saja."""
        if not self.nomor_rekening:
            return '-'
        rek = str(self.nomor_rekening)
        if len(rek) <= 4:
            return rek
        return '**** **** ' + rek[-4:]


class Perbaikan(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Menunggu'),
        ('approved', 'Disetujui'),
        ('rejected', 'Ditolak'),
    ]
    FIELD_CHOICES = [
        ('nuptk', 'NUPTK'),
        ('nip', 'NIP'),
        ('nik', 'NIK'),
        ('nama', 'Nama'),
        ('tempat_tugas', 'Tempat Tugas'),
        ('npsn', 'NPSN'),
        ('kecamatan', 'Kecamatan'),
        ('kabupaten', 'Kabupaten'),
        ('provinsi', 'Provinsi'),
        ('jenjang', 'Jenjang'),
        ('jabatan', 'Jabatan'),
        ('nomor_rekening', 'Nomor Rekening'),
        ('bank', 'Bank'),
        ('atas_nama_rekening', 'Atas Nama Rekening'),
    ]

    peserta = models.ForeignKey(
        Peserta, on_delete=models.CASCADE, related_name='perbaikan')
    field_name = models.CharField(max_length=30, choices=FIELD_CHOICES)
    nilai_lama = models.TextField(blank=True)
    nilai_baru = models.TextField()
    tidak_dimiliki = models.BooleanField(default=False)
    alasan = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='pending')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    catatan_admin = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        Admin, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.peserta.nama} — {self.get_field_name_display()}'


class PengajuanAktivasi(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('pending', 'Menunggu Verifikasi'),
        ('approved', 'Disetujui'),
        ('rejected', 'Ditolak'),
    ]

    peserta = models.ForeignKey(
        Peserta, on_delete=models.CASCADE, related_name='pengajuan_aktivasi')
    nik_diisi = models.CharField(max_length=20, blank=True)
    nip_diisi = models.CharField(max_length=20, blank=True)
    nuptk_diisi = models.CharField(max_length=20, blank=True)
    alasan_kosong = models.TextField(blank=True)
    nomor_referensi = models.CharField(max_length=30, unique=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='draft', db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    catatan_admin = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        Admin, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.nomor_referensi} — {self.peserta.nama}'


class DokumenAktivasi(models.Model):
    JENIS_CHOICES = [
        ('ktp', 'KTP'),
        ('kk', 'Kartu Keluarga'),
        ('sk', 'SK Pembagian Tugas Mengajar'),
        ('kartu_pegawai', 'Kartu Pegawai'),
        ('surat_keterangan', 'Surat Keterangan'),
    ]

    pengajuan = models.ForeignKey(
        PengajuanAktivasi, on_delete=models.CASCADE, related_name='dokumen')
    jenis_dokumen = models.CharField(max_length=20, choices=JENIS_CHOICES)
    file_path = models.CharField(max_length=500)
    file_name_original = models.CharField(max_length=255)
    file_mime = models.CharField(max_length=100)
    file_size = models.IntegerField()
    file_hash_sha256 = models.CharField(max_length=64, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.get_jenis_dokumen_display()} — {self.pengajuan.nomor_referensi}'


class NotifikasiInApp(models.Model):
    TIPE_CHOICES = [
        ('aktivasi_diterima', 'Aktivasi Diterima'),
        ('aktivasi_approved', 'Aktivasi Disetujui'),
        ('aktivasi_rejected', 'Aktivasi Ditolak'),
        ('perbaikan_approved', 'Perbaikan Disetujui'),
        ('perbaikan_rejected', 'Perbaikan Ditolak'),
        ('rekening_berubah', 'Rekening Berubah'),
        ('minta_upload_ulang', 'Minta Upload Ulang'),
        ('info_umum', 'Info Umum'),
    ]

    peserta = models.ForeignKey(
        Peserta, on_delete=models.CASCADE, related_name='notifikasi')
    tipe = models.CharField(max_length=30, choices=TIPE_CHOICES)
    judul = models.CharField(max_length=200)
    isi = models.TextField()
    link_terkait = models.CharField(max_length=500, blank=True)
    sudah_dibaca = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class LogContact(models.Model):
    JENIS_CHOICES = [
        ('wa_manual', 'WhatsApp Manual'),
        ('telepon', 'Telepon'),
        ('email', 'Email'),
    ]
    STATUS_CHOICES = [
        ('terkirim', 'Terkirim'),
        ('gagal', 'Gagal'),
        ('no_wa', 'Tidak Ada Nomor WA'),
    ]

    peserta = models.ForeignKey(Peserta, on_delete=models.CASCADE)
    admin = models.ForeignKey(Admin, on_delete=models.SET_NULL, null=True)
    jenis_kontak = models.CharField(max_length=20, choices=JENIS_CHOICES)
    nomor_tujuan = models.CharField(max_length=20, blank=True)
    template_dipakai = models.CharField(max_length=100, blank=True)
    isi_pesan = models.TextField(blank=True)
    status_kirim = models.CharField(max_length=20, choices=STATUS_CHOICES)
    catatan = models.TextField(blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)


class LogLogin(models.Model):
    peserta = models.ForeignKey(
        Peserta, null=True, blank=True, on_delete=models.SET_NULL)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    login_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-login_at']


class LogAdminAction(models.Model):
    admin = models.ForeignKey(Admin, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.IntegerField(null=True, blank=True)
    details = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class LogAksesDokumen(models.Model):
    """Audit setiap kali admin membuka dokumen aktivasi."""
    dokumen = models.ForeignKey(
        DokumenAktivasi, on_delete=models.CASCADE, related_name='log_akses')
    admin = models.ForeignKey(Admin, on_delete=models.SET_NULL, null=True)
    accessed_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-accessed_at']
        verbose_name_plural = 'Log Akses Dokumen'

    def __str__(self):
        return f'{self.admin} akses {self.dokumen}'


class PengaturanSistem(models.Model):
    """Singleton — hanya 1 baris."""
    batas_pending_aktivasi = models.IntegerField(default=50)
    batas_draft_aktivasi = models.IntegerField(default=10)
    durasi_draft_menit = models.IntegerField(default=30)
    auto_reject_pending_hari = models.IntegerField(default=7)
    ukuran_file_maksimal_mb = models.IntegerField(default=2)

    class Meta:
        verbose_name = 'Pengaturan Sistem'
        verbose_name_plural = 'Pengaturan Sistem'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        # Hapus cache setiap kali pengaturan disimpan/diubah
        cache.delete('pengaturan_sistem') 

    @classmethod
    def get(cls):
        # Coba ambil dari cache
        setting = cache.get('pengaturan_sistem')
        if not setting:
            # Jika tidak ada di cache, ambil dari DB
            setting, _ = cls.objects.get_or_create(pk=1)
            # Simpan ke cache selama 1 jam (3600 detik)
            cache.set('pengaturan_sistem', setting, timeout=3600)
        return setting
