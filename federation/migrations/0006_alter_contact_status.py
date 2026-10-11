
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0005_loginchallenge'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contact',
            name='status',
            field=models.CharField(choices=[('pending', 'Pending (sent)'), ('pending_incoming', 'Pending (received, yours to accept)'), ('accepted', 'Accepted'), ('blocked', 'Blocked')], default='pending', max_length=20),
        ),
    ]
