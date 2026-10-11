
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0014_alter_identity_options_alter_quotatier_options'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='servernotice',
            options={'ordering': ['-created_at'], 'verbose_name': 'MOTD / server notice', 'verbose_name_plural': '📢 MOTD and notices'},
        ),
    ]
