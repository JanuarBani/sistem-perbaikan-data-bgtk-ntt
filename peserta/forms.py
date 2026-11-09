# peserta/forms.py
from django import forms
from .utils import normalisasi_wa


class PersetujuanForm(forms.Form):
    setuju = forms.BooleanField(
        required=True,
        label='Saya menyetujui pemrosesan data pribadi saya sesuai kebijakan privasi.'
    )


class CariNamaForm(forms.Form):
    kata_kunci = forms.CharField(
        max_length=255,
        required=False,
        label='Cari Peserta',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Ketik nama, instansi, NPSN, atau kabupaten...',
            'autofocus': True,
            'autocomplete': 'off',
        })
    )
    # Optional: filter tambahan
    kabupaten = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Filter kabupaten (opsional)',
        })
    )


class VerifikasiForm(forms.Form):
    METODE_CHOICES = [
        ('nuptk', 'NUPTK'),
        ('nik', 'NIK'),
        ('nip', 'NIP'),
        ('tanggal_lahir', 'Tanggal Lahir'),
    ]
    metode = forms.ChoiceField(
        choices=METODE_CHOICES, widget=forms.RadioSelect
    )
    nilai = forms.CharField(
        max_length=100,
        widget=forms.TextInput(
            attrs={'class': 'form-input', 'placeholder': 'Masukkan nilai'}
        )
    )

    def __init__(self, *args, available_methods=None, **kwargs):
        super().__init__(*args, **kwargs)
        if available_methods:
            self.fields['metode'].choices = available_methods


class PopupWAForm(forms.Form):
    nomor_wa = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '08xxxxxxxxxx',
            'inputmode': 'tel',
            'autofocus': True,
        }),
        error_messages={
            'required': 'Nomor WhatsApp tidak valid.',
        }
    )

    def clean_nomor_wa(self):
        wa = self.cleaned_data.get('nomor_wa', '').strip()
        if not wa:
            return ''
        norm = normalisasi_wa(wa)
        if not norm.startswith('62') or len(norm) < 10:
            raise forms.ValidationError(
                'Format nomor WA tidak valid. Gunakan format 08xxxxxxxxxx.'
            )
        return norm


BANK_CHOICES = [
    ('Bank NTT', 'Bank NTT'),
    ('Bank Rakyat Indonesia (BRI)', 'Bank Rakyat Indonesia (BRI)'),
    ('Bank Negara Indonesia (BNI)', 'Bank Negara Indonesia (BNI)'),
    ('Bank Mandiri', 'Bank Mandiri'),
    ('Bank Tabungan Negara (BTN)', 'Bank Tabungan Negara (BTN)'),
    ('Bank Central Asia (BCA)', 'Bank Central Asia (BCA)'),
    ('Bank Syariah Indonesia (BSI)', 'Bank Syariah Indonesia (BSI)'),
    ('Bank Sinarmas', 'Bank Sinarmas'),
    ('Bank Capital Indonesia', 'Bank Capital Indonesia'),
    ('Bank Danamon', 'Bank Danamon'),
    ('CIMB Niaga', 'CIMB Niaga'),
    ('OCBC', 'OCBC'),
    ('Bank Panin', 'Bank Panin'),
    ('Bank Mega', 'Bank Mega'),
    ('BTPN', 'BTPN'),
    ('Bank Muamalat Indonesia', 'Bank Muamalat Indonesia'),
    ('Bank Nationalnobu (Nobu Bank)', 'Bank Nationalnobu (Nobu Bank)'),
    ('Bank Multiarta Sentosa (Bank MAS)', 'Bank Multiarta Sentosa (Bank MAS)'),
]


class PerbaikanForm(forms.Form):
    nilai_baru = forms.CharField(max_length=1000, widget=forms.Textarea(attrs={
        'class': 'form-input',
        'rows': 2,
    }))
    alasan = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'form-input',
            'rows': 2,
            'placeholder': 'Alasan perubahan (opsional)',
        })
    )
    tidak_dimiliki = forms.BooleanField(
        required=False,
        label='Saya memang tidak memiliki data ini',
        widget=forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
    )

    bank_lainnya = forms.CharField(
        max_length=50,
        required=False,
        label='Nama bank lainnya',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Tuliskan nama bank Anda',
            'autocomplete': 'organization',
        }),
    )

    def __init__(self, *args, field_name=None, current_value='', **kwargs):
        super().__init__(*args, **kwargs)
        self.is_bank_field = field_name == 'bank'
        always_required = field_name in {
            'nama', 'nomor_rekening', 'bank', 'atas_nama_rekening'
        }
        self.fields['nilai_baru'].required = always_required
        if always_required:
            self.fields.pop('tidak_dimiliki')
        if not self.is_bank_field:
            self.fields.pop('bank_lainnya')
            return

        self.fields['nilai_baru'] = forms.ChoiceField(
            required=True,
            choices=[('', 'Pilih bank'), *BANK_CHOICES,
                     ('__lainnya__', 'Tidak ada di daftar — tulis nama bank')],
            widget=forms.Select(attrs={
                'class': 'form-input',
                'id': 'id_nilai_baru',
            }),
            label='Pilih Bank',
        )
        if not self.is_bound and current_value:
            known_banks = {value for value, _label in BANK_CHOICES}
            if current_value in known_banks:
                self.initial['nilai_baru'] = current_value
            else:
                self.initial['nilai_baru'] = '__lainnya__'
                self.initial['bank_lainnya'] = current_value

    def clean(self):
        cleaned = super().clean()
        if not self.is_bank_field and 'tidak_dimiliki' in self.fields:
            if cleaned.get('tidak_dimiliki'):
                if cleaned.get('nilai_baru', '').strip():
                    self.add_error(
                        'nilai_baru',
                        'Kosongkan nilai jika Anda menyatakan tidak memiliki data ini.'
                    )
                else:
                    cleaned['nilai_baru'] = ''
            elif not cleaned.get('nilai_baru', '').strip():
                self.add_error(
                    'nilai_baru',
                    'Isi nilai data, atau centang bahwa Anda memang tidak memilikinya.'
                )
        if self.is_bank_field and cleaned.get('nilai_baru') == '__lainnya__':
            bank_lainnya = cleaned.get('bank_lainnya', '').strip()
            if not bank_lainnya:
                self.add_error('bank_lainnya', 'Tuliskan nama bank Anda.')
            else:
                cleaned['nilai_baru'] = bank_lainnya
        return cleaned


class AktivasiForm(forms.Form):
    nik_diisi = forms.CharField(max_length=16, required=False,
                                widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '16 digit'}))
    nip_diisi = forms.CharField(max_length=18, required=False,
                                widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '18 digit'}))
    nuptk_diisi = forms.CharField(max_length=16, required=False,
                                  widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '16 digit'}))
    nomor_wa = forms.CharField(max_length=20,
                               widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '08xxxxxxxxxx'}))
    alasan_kosong = forms.CharField(widget=forms.Textarea(
        attrs={'class': 'form-input', 'rows': 3}))

    def clean(self):
        cleaned = super().clean()
        if not any([cleaned.get('nik_diisi'), cleaned.get('nip_diisi'), cleaned.get('nuptk_diisi')]):
            raise forms.ValidationError(
                'Isi minimal salah satu: NIK, NIP, atau NUPTK.')
        wa = cleaned.get('nomor_wa', '')
        if wa:
            cleaned['nomor_wa'] = normalisasi_wa(wa)
            if not cleaned['nomor_wa'].startswith('62') or len(cleaned['nomor_wa']) < 10:
                self.add_error('nomor_wa', 'Format WhatsApp tidak valid.')
        for field, expected_length in (('nik_diisi', 16), ('nip_diisi', 18), ('nuptk_diisi', 16)):
            value = cleaned.get(field, '')
            if value and (not value.isdigit() or len(value) != expected_length):
                self.add_error(field, f'Harus berisi {expected_length} digit angka.')
        return cleaned
