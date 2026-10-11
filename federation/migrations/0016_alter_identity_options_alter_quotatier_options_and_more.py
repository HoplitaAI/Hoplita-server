from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0015_alter_servernotice_options'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='identity',
            options={'verbose_name': 'user (federated identity)', 'verbose_name_plural': '👤 Users and their quotas'},
        ),
        migrations.AlterModelOptions(
            name='quotatier',
            options={'verbose_name': 'group quota', 'verbose_name_plural': '📦 Quotas by group'},
        ),
        migrations.AlterModelOptions(
            name='servernotice',
            options={'ordering': ['-created_at'], 'verbose_name': 'MOTD / server notice', 'verbose_name_plural': '📢 MOTD and notices'},
        ),
        migrations.AlterField(
            model_name='contact',
            name='status',
            field=models.CharField(choices=[('pending', 'Pending (sent)'), ('pending_incoming', 'Pending (received, yours to accept)'), ('accepted', 'Accepted'), ('blocked', 'Blocked')], default='pending', max_length=20),
        ),
        migrations.AlterField(
            model_name='contact',
            name='trust_level',
            field=models.CharField(choices=[('open', 'Free rein'), ('accept', 'With acceptance'), ('analyze', 'Acceptance + analysis')], default='accept', max_length=10),
        ),
        migrations.AlterField(
            model_name='identity',
            name='public_key',
            field=models.TextField(help_text='Curve25519 public key, base64'),
        ),
        migrations.AlterField(
            model_name='identity',
            name='quota_overrides',
            field=models.JSONField(blank=True, default=dict, help_text='e.g. {"max_sync_bytes": 5000000}'),
        ),
        migrations.AlterField(
            model_name='quotatier',
            name='is_default',
            field=models.BooleanField(default=False, help_text='Tier applied to anyone without one'),
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
            name='max_requests_per_min',
            field=models.IntegerField(default=0, help_text='API requests per minute (0=unlimited)'),
        ),
    ]
