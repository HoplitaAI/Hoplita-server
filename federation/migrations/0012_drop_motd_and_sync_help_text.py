
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0011_servernotice'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Motd',
        ),
        migrations.AlterField(
            model_name='envelope',
            name='size_hint',
            field=models.IntegerField(default=0, help_text='len(ciphertext), for mailbox quota accounting'),
        ),
        migrations.AlterField(
            model_name='identity',
            name='quota_overrides',
            field=models.JSONField(blank=True, default=dict, help_text='e.g. {"max_sync_bytes": 5000000}'),
        ),
        migrations.AlterField(
            model_name='quotatier',
            name='is_default',
            field=models.BooleanField(default=False, help_text='Tier asignado a quien no tiene uno'),
        ),
        migrations.AlterField(
            model_name='quotatier',
            name='max_children',
            field=models.IntegerField(default=0, help_text='Enrolled children (0=unlimited)'),
        ),
        migrations.AlterField(
            model_name='quotatier',
            name='max_contacts',
            field=models.IntegerField(default=0, help_text='Contacts (0=unlimited)'),
        ),
        migrations.AlterField(
            model_name='quotatier',
            name='max_inbox_bytes',
            field=models.BigIntegerField(default=0, help_text='Undelivered bytes in the mailbox (0=unlimited)'),
        ),
        migrations.AlterField(
            model_name='quotatier',
            name='max_inbox_envelopes',
            field=models.IntegerField(default=0, help_text='Undelivered envelopes in the mailbox (0=unlimited)'),
        ),
        migrations.AlterField(
            model_name='quotatier',
            name='max_requests_per_min',
            field=models.IntegerField(default=0, help_text='API requests per minute (0=unlimited)'),
        ),
        migrations.AlterField(
            model_name='quotatier',
            name='max_sync_bytes',
            field=models.BigIntegerField(default=0, help_text='Max size of the sync snapshot (0=unlimited)'),
        ),
    ]
