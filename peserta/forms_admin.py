# peserta/forms_admin.py
from django import forms


class ReviewPerbaikanForm(forms.Form):
    action = forms.ChoiceField(
        choices=[('approve', 'Setujui'), ('reject', 'Tolak')])
    catatan_admin = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 3,
                                     'placeholder': 'Wajib diisi jika menolak'})
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('action') == 'reject' and not cleaned.get('catatan_admin', '').strip():
            raise forms.ValidationError('Alasan penolakan wajib diisi.')
        return cleaned


class ReviewAktivasiForm(forms.Form):
    action = forms.ChoiceField(
        choices=[('approve', 'Setujui & Aktifkan'), ('reject', 'Tolak')])
    catatan_admin = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 3,
                                     'placeholder': 'Wajib diisi jika menolak'})
    )
    # Checklist verifikasi
    cek_ktp_cocok = forms.BooleanField(
        required=False, label='NIK di KTP cocok dengan NIK yang diisi')
    cek_nama_cocok = forms.BooleanField(
        required=False, label='Nama di KTP cocok dengan nama di data')
    cek_sk_cocok = forms.BooleanField(
        required=False, label='NIP di SK cocok dengan NIP yang diisi')

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('action') == 'reject' and not cleaned.get('catatan_admin', '').strip():
            raise forms.ValidationError('Alasan penolakan wajib diisi.')
        if cleaned.get('action') == 'approve':
            if not (cleaned.get('cek_ktp_cocok') and cleaned.get('cek_nama_cocok')):
                raise forms.ValidationError(
                    'Centang minimal "NIK cocok" dan "Nama cocok" sebelum menyetujui.'
                )
        return cleaned


class SimpanLogWAForm(forms.Form):
    jenis_kontak = forms.ChoiceField(
        choices=[('wa_manual', 'WhatsApp'), ('telepon', 'Telepon'), ('email', 'Email')])
    status_kirim = forms.ChoiceField(choices=[(
        'terkirim', 'Terkirim'), ('gagal', 'Gagal'), ('no_wa', 'Tidak Ada Nomor WA')])
    isi_pesan = forms.CharField(required=False, widget=forms.Textarea(
        attrs={'class': 'form-input', 'rows': 4}))
    catatan = forms.CharField(required=False, widget=forms.Textarea(
        attrs={'class': 'form-input', 'rows': 2}))
