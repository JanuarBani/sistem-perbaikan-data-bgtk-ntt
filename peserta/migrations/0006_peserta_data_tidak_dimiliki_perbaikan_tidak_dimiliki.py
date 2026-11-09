from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('peserta', '0005_alter_dokumenaktivasi_jenis_dokumen'),
    ]

    operations = [
        migrations.AddField(
            model_name='peserta',
            name='data_tidak_dimiliki',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Daftar field yang dinyatakan peserta memang tidak dimiliki.',
            ),
        ),
        migrations.AddField(
            model_name='perbaikan',
            name='tidak_dimiliki',
            field=models.BooleanField(default=False),
        ),
    ]
