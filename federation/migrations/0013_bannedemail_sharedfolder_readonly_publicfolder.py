
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0012_drop_motd_and_sync_help_text'),
    ]

    operations = [
        migrations.CreateModel(
            name='BannedEmail',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(db_index=True, max_length=254, unique=True)),
                ('reason', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddField(
            model_name='sharedfolder',
            name='readonly',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='PublicFolder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('folder_id', models.CharField(db_index=True, max_length=64)),
                ('folder_name', models.CharField(max_length=200)),
                ('kind', models.CharField(choices=[('folder', 'folder'), ('project', 'project')], default='folder', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='public_folders', to='federation.identity')),
            ],
            options={
                'ordering': ['created_at'],
                'unique_together': {('owner', 'folder_id')},
            },
        ),
    ]
