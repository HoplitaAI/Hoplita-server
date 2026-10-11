
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='contact',
            name='permissions',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
