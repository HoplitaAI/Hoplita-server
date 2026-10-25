import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0021_admin_names_without_emoji'),
    ]

    operations = [
        migrations.AlterField(
            model_name='syncsnapshot',
            name='size',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='syncsnapshot',
            name='parts',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='syncsnapshot',
            name='upload_id',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
        migrations.AddField(
            model_name='syncsnapshot',
            name='sha256',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
        migrations.CreateModel(
            name='SyncUpload',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('upload_id', models.CharField(max_length=32, unique=True)),
                ('base_version', models.IntegerField(default=0)),
                ('parts', models.IntegerField()),
                ('size', models.BigIntegerField()),
                ('sha256', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sync_uploads', to='federation.identity')),
            ],
        ),
        migrations.CreateModel(
            name='SyncPart',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('upload_id', models.CharField(db_index=True, max_length=32)),
                ('idx', models.IntegerField()),
                ('data', models.TextField()),
                ('size', models.IntegerField(default=0)),
                ('owner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sync_parts', to='federation.identity')),
            ],
            options={
                'unique_together': {('upload_id', 'idx')},
            },
        ),
    ]
