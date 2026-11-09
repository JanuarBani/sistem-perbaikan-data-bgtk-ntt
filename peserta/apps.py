# peserta/apps.py
from django.apps import AppConfig
from django.db.backends.signals import connection_created
from django.dispatch import receiver


class PesertaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'peserta'
    verbose_name = 'Data Peserta'

    def ready(self):
        """
        Aktifkan PRAGMA SQLite untuk performa & konkurensi.
        Dipanggil sekali per connection.
        """
        @receiver(connection_created)
        def set_sqlite_pragma(sender, connection, **kwargs):
            if connection.vendor == 'sqlite':
                cursor = connection.cursor()
                # WAL: writer tidak blokir reader
                cursor.execute('PRAGMA journal_mode=WAL;')
                # Balance speed vs durability
                cursor.execute('PRAGMA synchronous=NORMAL;')
                # 20 detik tunggu lock
                cursor.execute('PRAGMA busy_timeout=20000;')
                cursor.execute('PRAGMA cache_size=-64000;')      # 64MB cache
                # enforce foreign keys
                cursor.execute('PRAGMA foreign_keys=ON;')
                cursor.close()
