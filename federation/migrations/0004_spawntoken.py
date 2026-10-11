
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0003_sharedfolder'),
    ]

    operations = [
        migrations.CreateModel(
            name='SpawnToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token_hash', models.CharField(db_index=True, max_length=64, unique=True)),
                ('label', models.CharField(blank=True, max_length=120)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('redeemed', models.BooleanField(default=False)),
                ('redeemed_at', models.DateTimeField(blank=True, null=True)),
                ('child', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='spawned_from', to='federation.identity')),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='spawn_tokens', to='federation.identity')),
            ],
        ),
    ]
