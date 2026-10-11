
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0002_contact_permissions'),
    ]

    operations = [
        migrations.CreateModel(
            name='SharedFolder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('folder_id', models.CharField(db_index=True, max_length=64)),
                ('folder_name', models.CharField(max_length=200)),
                ('status', models.CharField(choices=[('pending', 'pending'), ('accepted', 'accepted')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shared_folders', to='federation.identity')),
                ('peer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='folders_shared_with_me', to='federation.identity')),
            ],
            options={
                'ordering': ['created_at'],
                'unique_together': {('owner', 'peer', 'folder_id')},
            },
        ),
    ]
