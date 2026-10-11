
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0004_spawntoken'),
    ]

    operations = [
        migrations.CreateModel(
            name='LoginChallenge',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nonce', models.CharField(db_index=True, max_length=128)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('used', models.BooleanField(default=False)),
                ('identity', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='login_challenges', to='federation.identity')),
            ],
        ),
    ]
