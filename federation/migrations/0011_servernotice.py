from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("federation", "0010_quotas"),
    ]

    operations = [
        migrations.CreateModel(
            name="ServerNotice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("kind", models.CharField(
                    choices=[("motd", "MOTD (broadcast)"), ("quota", "Quota"),
                             ("system", "System"), ("alert", "Alert")],
                    db_index=True, default="motd", max_length=16)),
                ("level", models.CharField(
                    choices=[("info", "Info"), ("warn", "Warning"),
                             ("critical", "Critical")],
                    default="info", max_length=10)),
                ("text", models.TextField(
                    blank=True, default="",
                    help_text="Free operator text (MOTD). For system notices use code+params.")),
                ("code", models.CharField(
                    blank=True, default="",
                    help_text="Machine code the client localizes via t() (e.g. quota_exceeded).",
                    max_length=64)),
                ("params", models.JSONField(
                    blank=True, default=dict,
                    help_text="Params for the code's translation, e.g. {resource,used,limit}.")),
                ("active", models.BooleanField(db_index=True, default=True)),
                ("delivered", models.BooleanField(db_index=True, default=False)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(
                    blank=True, null=True,
                    help_text="Optional: after this instant the notice is no longer served.")),
                ("recipient", models.ForeignKey(
                    blank=True,
                    help_text="Target user. Leave EMPTY to broadcast to everyone (MOTD).",
                    null=True, on_delete=django.db.models.deletion.CASCADE,
                    related_name="notices", to="federation.identity")),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
