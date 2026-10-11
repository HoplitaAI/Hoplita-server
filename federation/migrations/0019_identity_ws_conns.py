
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0018_nobody_rpm_300'),
    ]

    operations = [
        migrations.AddField(
            model_name='identity',
            name='ws_conns',
            field=models.IntegerField(default=0),
        ),
    ]
