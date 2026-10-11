
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0007_sharedfolder_kind'),
    ]

    operations = [
        migrations.AddField(
            model_name='identity',
            name='ws_online',
            field=models.BooleanField(default=False),
        ),
    ]
