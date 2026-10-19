from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0020_registration_in_the_admin'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='identity',
            options={'verbose_name': 'user (federated identity)', 'verbose_name_plural': 'Users and their quotas'},
        ),
        migrations.AlterModelOptions(
            name='invitecode',
            options={'ordering': ['-created_at'], 'verbose_name': 'invitation code', 'verbose_name_plural': 'Invitation codes'},
        ),
        migrations.AlterModelOptions(
            name='quotatier',
            options={'verbose_name': 'group quota', 'verbose_name_plural': 'Quotas by group'},
        ),
        migrations.AlterModelOptions(
            name='registrationpolicy',
            options={'verbose_name': 'sign-up policy', 'verbose_name_plural': 'Sign-up policy'},
        ),
        migrations.AlterModelOptions(
            name='servernotice',
            options={'ordering': ['-created_at'], 'verbose_name': 'MOTD / server notice', 'verbose_name_plural': 'MOTD and notices'},
        ),
    ]
