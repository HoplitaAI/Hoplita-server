
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0013_bannedemail_sharedfolder_readonly_publicfolder'),
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
    ]
