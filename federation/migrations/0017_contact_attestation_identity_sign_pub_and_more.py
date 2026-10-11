
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0016_alter_identity_options_alter_quotatier_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='contact',
            name='attestation',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='identity',
            name='sign_pub',
            field=models.TextField(blank=True, default='', help_text='Ed25519 signing public key, base64'),
        ),
        migrations.AddField(
            model_name='publicfolder',
            name='attestation',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='sharedfolder',
            name='grant',
            field=models.TextField(blank=True, default=''),
        ),
    ]
