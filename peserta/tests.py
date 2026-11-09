"""Regression and workflow tests for the participant and admin site."""

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import (
    AktivasiForm,
    CariNamaForm,
    PerbaikanForm,
    PersetujuanForm,
    PopupWAForm,
    VerifikasiForm,
)
from .models import (
    DokumenAktivasi,
    LogAksesDokumen,
    LogLogin,
    NotifikasiInApp,
    PengajuanAktivasi,
    PengaturanSistem,
    Peserta,
    Perbaikan,
)
from .utils import derive_tanggal_lahir, normalisasi_wa, parse_nip_nik
from .views import simpan_dokumen


User = get_user_model()


def peserta_baru(**values):
    data = {
        'nama': 'Siti Aminah',
        'nama_normalized': 'siti aminah',
        'kabupaten': 'Kupang',
    }
    data.update(values)
    return Peserta.objects.create(**data)


def beri_sesi_peserta(client, peserta, *, pdp_ok=True):
    session = client.session
    session['peserta_id'] = peserta.pk
    if pdp_ok:
        session['pdp_ok'] = True
    session.save()


def buat_admin(**values):
    data = {'username': 'admin-test', 'is_staff': True, 'is_active': True}
    data.update(values)
    return User.objects.create_user(password='Test-password-123!', **data)


class FormValidationTests(TestCase):
    def test_persetujuan_requires_explicit_consent(self):
        self.assertFalse(PersetujuanForm({}).is_valid())
        self.assertTrue(PersetujuanForm({'setuju': 'on'}).is_valid())

    def test_search_form_accepts_optional_filters_and_rejects_oversize(self):
        self.assertTrue(CariNamaForm({'kata_kunci': '', 'kabupaten': ''}).is_valid())
        self.assertFalse(CariNamaForm({'kata_kunci': 'x' * 256}).is_valid())

    def test_verification_form_only_accepts_available_methods(self):
        form = VerifikasiForm(
            {'metode': 'nik', 'nilai': '1234567890123456'},
            available_methods=[('nik', 'NIK')],
        )
        self.assertTrue(form.is_valid())
        unavailable = VerifikasiForm(
            {'metode': 'nip', 'nilai': '123456789012345678'},
            available_methods=[('nik', 'NIK')],
        )
        self.assertFalse(unavailable.is_valid())

    def test_whatsapp_is_optional_but_validated_when_present(self):
        empty = PopupWAForm({})
        self.assertTrue(empty.is_valid())
        self.assertEqual(empty.cleaned_data['nomor_wa'], '')
        valid = PopupWAForm({'nomor_wa': '081234567890'})
        self.assertTrue(valid.is_valid())
        self.assertEqual(valid.cleaned_data['nomor_wa'], '6281234567890')
        self.assertFalse(PopupWAForm({'nomor_wa': 'abc'}).is_valid())

    def test_profile_repair_requires_value_or_no_data_declaration(self):
        self.assertFalse(PerbaikanForm(
            {'nilai_baru': ''}, field_name='nip'
        ).is_valid())
        declaration = PerbaikanForm(
            {'nilai_baru': '', 'tidak_dimiliki': 'on'}, field_name='nip'
        )
        self.assertTrue(declaration.is_valid(), declaration.errors)
        both = PerbaikanForm(
            {'nilai_baru': '123456789012345678', 'tidak_dimiliki': 'on'},
            field_name='nip',
        )
        self.assertFalse(both.is_valid())

    def test_account_repair_cannot_use_no_data_declaration(self):
        form = PerbaikanForm(
            {'nilai_baru': '', 'tidak_dimiliki': 'on'},
            field_name='nomor_rekening',
        )
        self.assertFalse(form.is_valid())
        self.assertNotIn('tidak_dimiliki', form.fields)
        self.assertFalse(PerbaikanForm(
            {'nilai_baru': ''}, field_name='bank'
        ).is_valid())

    def test_bank_custom_choice_requires_a_name(self):
        form = PerbaikanForm(
            {'nilai_baru': '__lainnya__', 'bank_lainnya': ''},
            field_name='bank',
        )
        self.assertFalse(form.is_valid())
        custom = PerbaikanForm(
            {'nilai_baru': '__lainnya__', 'bank_lainnya': 'Bank Contoh'},
            field_name='bank',
        )
        self.assertTrue(custom.is_valid(), custom.errors)
        self.assertEqual(custom.cleaned_data['nilai_baru'], 'Bank Contoh')

    def test_activation_form_requires_valid_identity_and_whatsapp(self):
        valid = AktivasiForm({
            'nik_diisi': '1234567890123456',
            'nip_diisi': '',
            'nuptk_diisi': '',
            'nomor_wa': '081234567890',
            'alasan_kosong': 'Data identitas diperbarui',
        })
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.cleaned_data['nomor_wa'], '6281234567890')
        self.assertFalse(AktivasiForm({
            'nik_diisi': '', 'nip_diisi': '', 'nuptk_diisi': '',
            'nomor_wa': '081234567890', 'alasan_kosong': 'Tidak ada',
        }).is_valid())


class UtilityTests(TestCase):
    def test_parse_identity_lengths_and_invalid_values(self):
        self.assertEqual(parse_nip_nik('123456789012345678'), ('123456789012345678', None))
        self.assertEqual(parse_nip_nik('1234567890123456'), (None, '1234567890123456'))
        self.assertEqual(parse_nip_nik('not-an-id'), (None, None))

    def test_birth_date_derivation_handles_nik_and_invalid_values(self):
        # A female NIK encodes the day as 40 plus the actual day.
        self.assertEqual(derive_tanggal_lahir(None, '3201014101010001').isoformat(), '2001-01-01')
        self.assertIsNone(derive_tanggal_lahir(None, 'invalid'))

    def test_whatsapp_normalization(self):
        self.assertEqual(normalisasi_wa('+62 812-3456-7890'), '6281234567890')
        self.assertEqual(normalisasi_wa('081234567890'), '6281234567890')


class ParticipantWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.peserta = peserta_baru(
            nik='1234567890123456',
            nomor_rekening='1234567890',
            bank='Bank NTT',
            atas_nama_rekening='Siti Aminah',
        )
        beri_sesi_peserta(self.client, self.peserta)

    def test_dashboard_requires_participant_session(self):
        anonymous = Client()
        response = anonymous.get(reverse('peserta:dashboard'))
        self.assertRedirects(
            response, reverse('peserta:cari'), fetch_redirect_response=False
        )

    def test_dashboard_identifies_unresolved_profile_fields(self):
        response = self.client.get(reverse('peserta:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('NUPTK', response.context['data_belum_lengkap'])
        self.assertFalse(response.context['data_profil_lengkap'])
        self.assertContains(response, 'Data kosong tanpa pernyataan tetap dianggap belum lengkap')

    def test_search_requires_privacy_consent(self):
        anonymous = Client()
        response = anonymous.get(reverse('peserta:cari'))
        self.assertRedirects(
            response, reverse('peserta:persetujuan'), fetch_redirect_response=False
        )

    def test_consent_submission_persists_in_session(self):
        client = Client()
        response = client.post(reverse('peserta:persetujuan'), {'setuju': 'on'})
        self.assertRedirects(response, reverse('peserta:cari'), fetch_redirect_response=False)
        self.assertTrue(client.session['pdp_ok'])

    def test_search_returns_minimal_participant_data(self):
        cache.clear()
        response = self.client.post(
            reverse('peserta:cari'), {'kata_kunci': 'Siti', 'kabupaten': ''}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_hasil'], 1)
        result = response.context['hasil'][0]
        self.assertEqual(set(result), {'id', 'nama', 'kabupaten', 'has_identity'})
        self.assertNotContains(response, self.peserta.nik)
        self.assertNotContains(response, self.peserta.nomor_rekening)

    def test_identity_verification_rotates_session_and_logs_success(self):
        old_session_key = self.client.session.session_key
        response = self.client.post(reverse(
            'peserta:verifikasi', kwargs={'peserta_id': self.peserta.pk}
        ), {'metode': 'nik', 'nilai': self.peserta.nik})
        self.assertRedirects(response, reverse('peserta:dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.client.session['peserta_id'], self.peserta.pk)
        self.assertNotEqual(self.client.session.session_key, old_session_key)
        self.assertTrue(LogLogin.objects.filter(peserta=self.peserta, success=True).exists())

    def test_failed_verification_is_logged_and_counted(self):
        response = self.client.post(reverse(
            'peserta:verifikasi', kwargs={'peserta_id': self.peserta.pk}
        ), {'metode': 'nik', 'nilai': '0000000000000000'})
        self.assertEqual(response.status_code, 200)
        self.peserta.refresh_from_db()
        self.assertEqual(self.peserta.failed_login_count, 1)
        self.assertTrue(LogLogin.objects.filter(peserta=self.peserta, success=False).exists())

    def test_verification_locks_after_five_failed_attempts(self):
        for _ in range(5):
            self.client.post(reverse(
                'peserta:verifikasi', kwargs={'peserta_id': self.peserta.pk}
            ), {'metode': 'nik', 'nilai': '0000000000000000'})
        self.peserta.refresh_from_db()
        self.assertEqual(self.peserta.failed_login_count, 0)
        self.assertIsNotNone(self.peserta.locked_until)
        self.assertGreater(self.peserta.locked_until, timezone.now())

    def test_missing_whatsapp_can_be_skipped(self):
        response = self.client.post(reverse('peserta:popup_wa'), {'nomor_wa': ''})
        self.assertRedirects(response, reverse('peserta:dashboard'), fetch_redirect_response=False)
        self.peserta.refresh_from_db()
        self.assertEqual(self.peserta.nomor_wa, '')

    def test_duplicate_whatsapp_is_rejected(self):
        peserta_baru(nama='Peserta Lain', nama_normalized='peserta lain', nomor_wa='6281234567890')
        response = self.client.post(reverse('peserta:popup_wa'), {'nomor_wa': '081234567890'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'sudah terdaftar')
        self.peserta.refresh_from_db()
        self.assertEqual(self.peserta.nomor_wa, '')

    def test_profile_repair_submission_requires_value_or_declaration(self):
        url = reverse('peserta:perbaikan', kwargs={'field_name': 'nip'})
        invalid = self.client.post(url, {'nilai_baru': ''})
        self.assertEqual(invalid.status_code, 200)
        self.assertFalse(Perbaikan.objects.filter(peserta=self.peserta, field_name='nip').exists())
        valid = self.client.post(url, {'nilai_baru': '', 'tidak_dimiliki': 'on'})
        self.assertRedirects(valid, reverse('peserta:dashboard'), fetch_redirect_response=False)
        repair = Perbaikan.objects.get(peserta=self.peserta, field_name='nip')
        self.assertTrue(repair.tidak_dimiliki)
        self.assertEqual(repair.status, 'pending')

    def test_inline_no_data_declaration_is_pending_and_excludes_account_fields(self):
        url = reverse('peserta:ajukan_tidak_dimiliki', kwargs={'field_name': 'nip'})
        response = self.client.post(url, {'tidak_dimiliki': '1'})
        self.assertRedirects(response, reverse('peserta:dashboard'), fetch_redirect_response=False)
        repair = Perbaikan.objects.get(peserta=self.peserta, field_name='nip')
        self.assertTrue(repair.tidak_dimiliki)
        self.assertNotIn('nip', self.peserta.data_tidak_dimiliki)

        account_url = reverse('peserta:ajukan_tidak_dimiliki', kwargs={'field_name': 'bank'})
        rejected = self.client.post(account_url, {'tidak_dimiliki': '1'})
        self.assertEqual(rejected.status_code, 404)

    def test_inline_no_data_declaration_refuses_nonempty_data_and_duplicates(self):
        self.peserta.nip = '123456789012345678'
        self.peserta.save(update_fields=['nip'])
        url = reverse('peserta:ajukan_tidak_dimiliki', kwargs={'field_name': 'nip'})
        response = self.client.post(url, {'tidak_dimiliki': '1'})
        self.assertRedirects(response, reverse('peserta:dashboard'), fetch_redirect_response=False)
        self.assertFalse(Perbaikan.objects.filter(peserta=self.peserta).exists())

        self.peserta.nip = ''
        self.peserta.save(update_fields=['nip'])
        Perbaikan.objects.create(
            peserta=self.peserta, field_name='nip', nilai_lama='', nilai_baru='',
            tidak_dimiliki=True, status='pending',
        )
        response = self.client.post(url, {'tidak_dimiliki': '1'})
        self.assertRedirects(response, reverse('peserta:dashboard'), fetch_redirect_response=False)
        self.assertEqual(Perbaikan.objects.filter(peserta=self.peserta, field_name='nip').count(), 1)

    def test_account_data_cannot_be_confirmed_when_profile_unresolved(self):
        response = self.client.post(reverse('peserta:tandai_data_sesuai'))
        self.assertRedirects(response, reverse('peserta:dashboard'), fetch_redirect_response=False)
        self.peserta.refresh_from_db()
        self.assertFalse(self.peserta.is_data_verified)

    def test_account_and_resolved_profile_data_can_be_confirmed(self):
        self.peserta.data_tidak_dimiliki = [
            'nuptk', 'nip', 'tempat_tugas', 'npsn', 'kecamatan',
            'kabupaten', 'provinsi', 'jenjang', 'jabatan',
        ]
        self.peserta.save(update_fields=['data_tidak_dimiliki'])
        response = self.client.post(reverse('peserta:tandai_data_sesuai'))
        self.assertRedirects(response, reverse('peserta:dashboard'), fetch_redirect_response=False)
        self.peserta.refresh_from_db()
        self.assertTrue(self.peserta.is_data_verified)
        self.assertIsNotNone(self.peserta.verified_at)

    def test_notification_delete_is_post_only_and_owner_scoped(self):
        own = NotifikasiInApp.objects.create(
            peserta=self.peserta, tipe='info_umum', judul='Milik saya', isi='Isi'
        )
        other = peserta_baru(nama='Peserta Lain', nama_normalized='peserta lain')
        theirs = NotifikasiInApp.objects.create(
            peserta=other, tipe='info_umum', judul='Milik orang lain', isi='Isi'
        )
        get_response = self.client.get(reverse(
            'peserta:notifikasi_hapus', kwargs={'notif_id': own.pk}
        ))
        self.assertEqual(get_response.status_code, 405)
        forbidden = self.client.post(reverse(
            'peserta:notifikasi_hapus', kwargs={'notif_id': theirs.pk}
        ))
        self.assertEqual(forbidden.status_code, 404)
        self.assertTrue(NotifikasiInApp.objects.filter(pk=theirs.pk).exists())
        self.client.post(reverse('peserta:notifikasi_hapus', kwargs={'notif_id': own.pk}))
        self.assertFalse(NotifikasiInApp.objects.filter(pk=own.pk).exists())

    def test_delete_all_notifications_only_deletes_current_participant_items(self):
        NotifikasiInApp.objects.create(
            peserta=self.peserta, tipe='info_umum', judul='Satu', isi='Isi'
        )
        other = peserta_baru(nama='Peserta Lain', nama_normalized='peserta lain')
        theirs = NotifikasiInApp.objects.create(
            peserta=other, tipe='info_umum', judul='Tetap ada', isi='Isi'
        )
        response = self.client.post(reverse('peserta:notifikasi_hapus_semua'))
        self.assertRedirects(response, reverse('peserta:notifikasi'), fetch_redirect_response=False)
        self.assertFalse(self.peserta.notifikasi.exists())
        self.assertTrue(NotifikasiInApp.objects.filter(pk=theirs.pk).exists())

    def test_csrf_protects_inline_declaration_mutation(self):
        csrf_client = Client(enforce_csrf_checks=True)
        beri_sesi_peserta(csrf_client, self.peserta)
        response = csrf_client.post(reverse(
            'peserta:ajukan_tidak_dimiliki', kwargs={'field_name': 'nip'}
        ), {'tidak_dimiliki': '1'})
        self.assertEqual(response.status_code, 403)

    def test_activation_submission_creates_owned_pending_request(self):
        fresh = peserta_baru(nama='Guru Baru', nama_normalized='guru baru')
        beri_sesi_peserta(self.client, fresh)
        response = self.client.post(reverse(
            'peserta:aktivasi', kwargs={'peserta_id': fresh.pk}
        ), {
            'nik_diisi': '1234567890123456', 'nip_diisi': '', 'nuptk_diisi': '',
            'nomor_wa': '081234567890', 'alasan_kosong': 'Identitas diperbarui',
        })
        self.assertEqual(response.status_code, 302)
        application = PengajuanAktivasi.objects.get(peserta=fresh)
        self.assertEqual(application.status, 'pending')
        fresh.refresh_from_db()
        self.assertEqual(fresh.status_akun, 'pending_aktivasi')

    def test_activation_status_is_private_to_owning_participant(self):
        application = PengajuanAktivasi.objects.create(
            peserta=self.peserta, nomor_referensi='AKT-TEST-000001', status='pending'
        )
        other = peserta_baru(nama='Peserta Lain', nama_normalized='peserta lain')
        beri_sesi_peserta(self.client, other)
        response = self.client.get(reverse(
            'peserta:aktivasi_status', kwargs={'ref': application.nomor_referensi}
        ))
        self.assertEqual(response.status_code, 404)

    def test_activation_upload_saves_private_file_and_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            upload = SimpleUploadedFile('ktp.pdf', b'%PDF-test-file', content_type='application/pdf')
            with override_settings(PRIVATE_MEDIA_ROOT=temp_dir):
                with patch('peserta.views.magic.from_buffer', return_value='application/pdf'):
                    saved_path = simpan_dokumen(
                        PengajuanAktivasi.objects.create(
                            peserta=self.peserta,
                            nomor_referensi='AKT-UPLOAD-000001',
                            status='pending',
                        ),
                        'ktp', upload, PengaturanSistem(ukuran_file_maksimal_mb=2),
                    )
                document = DokumenAktivasi.objects.get(
                    pengajuan__nomor_referensi='AKT-UPLOAD-000001'
                )
                self.assertTrue(Path(saved_path).is_file())
                self.assertTrue(document.file_path.startswith('dokumen/AKT-UPLOAD-000001/'))
                self.assertEqual(document.file_hash_sha256, hashlib.sha256(b'%PDF-test-file').hexdigest())

    def test_activation_upload_rejects_unsupported_mime(self):
        upload = SimpleUploadedFile('ktp.txt', b'not a document', content_type='text/plain')
        application = PengajuanAktivasi.objects.create(
            peserta=self.peserta,
            nomor_referensi='AKT-UPLOAD-INVALID-000001',
            status='pending',
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with override_settings(PRIVATE_MEDIA_ROOT=temp_dir):
                with patch('peserta.views.magic.from_buffer', return_value='text/plain'):
                    with self.assertRaisesMessage(ValueError, 'format tidak didukung'):
                        simpan_dokumen(
                            application, 'ktp', upload,
                            PengaturanSistem(ukuran_file_maksimal_mb=2),
                        )
        self.assertFalse(application.dokumen.exists())


class AdminWorkflowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = buat_admin()
        self.client = Client()
        self.client.force_login(self.admin)
        self.peserta = peserta_baru(nip=None, is_data_verified=True, verified_at=timezone.now())

    def buat_perbaikan(self, **values):
        data = {
            'peserta': self.peserta,
            'field_name': 'nip',
            'nilai_lama': '',
            'nilai_baru': '',
            'tidak_dimiliki': True,
            'status': 'pending',
        }
        data.update(values)
        return Perbaikan.objects.create(**data)

    def test_admin_views_require_staff_login(self):
        anonymous = Client()
        response = anonymous.get(reverse('admin_panel:perbaikan_list'))
        self.assertRedirects(
            response,
            f"{reverse('admin_panel:login')}?next={reverse('admin_panel:perbaikan_list')}",
            fetch_redirect_response=False,
        )

    def test_admin_login_accepts_staff_and_rejects_nonstaff(self):
        anonymous = Client()
        response = anonymous.post(reverse('admin_panel:login'), {
            'username': self.admin.username, 'password': 'Test-password-123!',
        })
        self.assertRedirects(response, reverse('admin_panel:dashboard'), fetch_redirect_response=False)

        regular_user = buat_admin(username='nonstaff', is_staff=False)
        response = Client().post(reverse('admin_panel:login'), {
            'username': regular_user.username, 'password': 'Test-password-123!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'tidak memiliki akses admin')

    def test_single_approval_applies_no_data_declaration(self):
        repair = self.buat_perbaikan()
        response = self.client.post(reverse(
            'admin_panel:perbaikan_detail', kwargs={'pk': repair.pk}
        ), {'action': 'approve', 'catatan_admin': ''})
        self.assertRedirects(response, reverse('admin_panel:perbaikan_list'), fetch_redirect_response=False)
        self.peserta.refresh_from_db()
        repair.refresh_from_db()
        self.assertIn('nip', self.peserta.data_tidak_dimiliki)
        self.assertEqual(self.peserta.nip, '')
        self.assertFalse(self.peserta.is_data_verified)
        self.assertEqual(repair.status, 'approved')
        self.assertTrue(self.peserta.notifikasi.filter(judul__contains='Pernyataan').exists())

    def test_single_rejection_does_not_mark_field_as_absent(self):
        repair = self.buat_perbaikan()
        response = self.client.post(reverse(
            'admin_panel:perbaikan_detail', kwargs={'pk': repair.pk}
        ), {'action': 'reject', 'catatan_admin': 'Data perlu dikonfirmasi'})
        self.assertRedirects(response, reverse('admin_panel:perbaikan_list'), fetch_redirect_response=False)
        self.peserta.refresh_from_db()
        repair.refresh_from_db()
        self.assertNotIn('nip', self.peserta.data_tidak_dimiliki)
        self.assertEqual(repair.status, 'rejected')

    def test_bulk_approval_applies_no_data_declaration(self):
        repair = self.buat_perbaikan()
        response = self.client.post(reverse('admin_panel:perbaikan_bulk_action'), {
            'action': 'approve', 'selected_ids': [str(repair.pk)],
        })
        self.assertRedirects(response, reverse('admin_panel:perbaikan_list'), fetch_redirect_response=False)
        self.peserta.refresh_from_db()
        repair.refresh_from_db()
        self.assertIn('nip', self.peserta.data_tidak_dimiliki)
        self.assertEqual(repair.status, 'approved')

    def test_bulk_rejection_does_not_apply_declaration(self):
        repair = self.buat_perbaikan()
        response = self.client.post(reverse('admin_panel:perbaikan_bulk_action'), {
            'action': 'reject', 'selected_ids': [str(repair.pk)],
        })
        self.assertRedirects(response, reverse('admin_panel:perbaikan_list'), fetch_redirect_response=False)
        self.peserta.refresh_from_db()
        repair.refresh_from_db()
        self.assertNotIn('nip', self.peserta.data_tidak_dimiliki)
        self.assertEqual(repair.status, 'rejected')

    def test_bulk_mutation_requires_admin(self):
        repair = self.buat_perbaikan()
        anonymous = Client()
        response = anonymous.post(reverse('admin_panel:perbaikan_bulk_action'), {
            'action': 'approve', 'selected_ids': [str(repair.pk)],
        })
        self.assertEqual(response.status_code, 302)
        repair.refresh_from_db()
        self.peserta.refresh_from_db()
        self.assertEqual(repair.status, 'pending')
        self.assertNotIn('nip', self.peserta.data_tidak_dimiliki)

    def test_review_page_explains_no_data_declaration(self):
        repair = self.buat_perbaikan()
        response = self.client.get(reverse(
            'admin_panel:perbaikan_detail', kwargs={'pk': repair.pk}
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Peserta menyatakan tidak memiliki data NIP')

    def test_admin_edit_rejects_invalid_identity_and_does_not_save_it(self):
        response = self.client.post(reverse(
            'admin_panel:peserta_edit', kwargs={'pk': self.peserta.pk}
        ), {'nip': 'abc123'})
        self.assertRedirects(response, reverse(
            'admin_panel:peserta_edit', kwargs={'pk': self.peserta.pk}
        ), fetch_redirect_response=False)
        self.peserta.refresh_from_db()
        self.assertIsNone(self.peserta.nip)

    def test_admin_editing_participant_profile_resets_confirmation(self):
        response = self.client.post(reverse(
            'admin_panel:peserta_edit', kwargs={'pk': self.peserta.pk}
        ), {'provinsi': 'Nusa Tenggara Timur'})
        self.assertEqual(response.status_code, 302)
        self.peserta.refresh_from_db()
        self.assertEqual(self.peserta.provinsi, 'Nusa Tenggara Timur')
        self.assertFalse(self.peserta.is_data_verified)
        self.assertIsNone(self.peserta.verified_at)


class PrivateDocumentAccessTests(TestCase):
    def setUp(self):
        self.participant = peserta_baru()
        self.application = PengajuanAktivasi.objects.create(
            peserta=self.participant, nomor_referensi='AKT-TEST-000002', status='pending'
        )
        self.document = DokumenAktivasi.objects.create(
            pengajuan=self.application,
            jenis_dokumen='ktp',
            file_path='dokumen/AKT-TEST-000002/ktp.pdf',
            file_name_original='ktp.pdf',
            file_mime='application/pdf',
            file_size=12,
        )

    def test_private_document_denies_anonymous_and_participant(self):
        url = reverse('peserta:dokumen_aktivasi', kwargs={'pk': self.document.pk})
        self.assertEqual(Client().get(url).status_code, 403)
        participant_client = Client()
        beri_sesi_peserta(participant_client, self.participant)
        self.assertEqual(participant_client.get(url).status_code, 403)

    def test_private_document_is_served_only_to_staff_and_audited(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            absolute_path = Path(temp_dir) / self.document.file_path
            absolute_path.parent.mkdir(parents=True)
            absolute_path.write_bytes(b'%PDF-test-data')
            admin = buat_admin(username='document-admin')
            client = Client()
            client.force_login(admin)
            with override_settings(PRIVATE_MEDIA_ROOT=temp_dir):
                response = client.get(reverse(
                    'peserta:dokumen_aktivasi', kwargs={'pk': self.document.pk}
                ))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
                self.assertIn('private', response['Cache-Control'])
                self.assertEqual(LogAksesDokumen.objects.filter(
                    dokumen=self.document, admin=admin
                ).count(), 1)
                response.close()

    def test_deleted_or_missing_private_document_returns_404(self):
        client = Client()
        admin = buat_admin(username='document-admin-2')
        client.force_login(admin)
        with tempfile.TemporaryDirectory() as temp_dir:
            with override_settings(PRIVATE_MEDIA_ROOT=temp_dir):
                response = client.get(reverse(
                    'peserta:dokumen_aktivasi', kwargs={'pk': self.document.pk}
                ))
        self.assertEqual(response.status_code, 404)
        self.document.deleted_at = timezone.now()
        self.document.save(update_fields=['deleted_at'])
        self.assertEqual(client.get(reverse(
            'peserta:dokumen_aktivasi', kwargs={'pk': self.document.pk}
        )).status_code, 404)

    def test_private_document_path_traversal_is_rejected(self):
        admin = buat_admin(username='document-admin-3')
        client = Client()
        client.force_login(admin)
        self.document.file_path = '../outside.pdf'
        self.document.save(update_fields=['file_path'])
        with tempfile.TemporaryDirectory() as temp_dir:
            with override_settings(PRIVATE_MEDIA_ROOT=temp_dir):
                response = client.get(reverse(
                    'peserta:dokumen_aktivasi', kwargs={'pk': self.document.pk}
                ))
        self.assertEqual(response.status_code, 404)

