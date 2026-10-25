from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0022_sync_in_parts'),
    ]

    operations = [
        migrations.AddField(
            model_name='spawntoken',
            name='kit',
            field=models.TextField(blank=True, default=''),
        ),
    ]
