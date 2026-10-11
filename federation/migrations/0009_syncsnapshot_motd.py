from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("federation", "0008_identity_ws_online"),
    ]

    operations = [
        migrations.CreateModel(
            name="Motd",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("text", models.TextField()),
                ("level", models.CharField(
                    choices=[("info", "Info"), ("warn", "Warning"),
                             ("critical", "Critical")],
                    default="info", max_length=10)),
                ("active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="SyncSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("version", models.IntegerField(default=0)),
                ("ciphertext", models.TextField()),
                ("size", models.IntegerField(default=0)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("owner", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="sync_snapshot", to="federation.identity")),
            ],
        ),
    ]
