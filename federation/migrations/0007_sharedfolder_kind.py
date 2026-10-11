
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0006_alter_contact_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='sharedfolder',
            name='kind',
            field=models.CharField(choices=[('folder', 'folder'), ('project', 'project')], default='folder', max_length=10),
        ),
    ]
