from django.db import migrations, models
import django.db.models.deletion


def seed_tiers(apps, schema_editor):
    QuotaTier = apps.get_model("federation", "QuotaTier")
    tiers = [
        ("nobody",  5_000_000,      200,     10_000_000,     20,      2,      300,  True),
        ("power",  100_000_000,    5000,    200_000_000,    500,     50,      600,  False),
        ("vip",             0,        0,             0,       0,      0,        0,  False),
    ]
    for (name, sb, ie, ib, co, ch, rpm, dflt) in tiers:
        QuotaTier.objects.get_or_create(
            name=name,
            defaults=dict(max_sync_bytes=sb, max_inbox_envelopes=ie,
                          max_inbox_bytes=ib, max_contacts=co, max_children=ch,
                          max_requests_per_min=rpm, is_default=dflt))


def unseed(apps, schema_editor):
    apps.get_model("federation", "QuotaTier").objects.filter(
        name__in=["nobody", "power", "vip"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("federation", "0009_syncsnapshot_motd"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuotaTier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("name", models.CharField(db_index=True, max_length=40, unique=True)),
                ("max_sync_bytes", models.BigIntegerField(default=0)),
                ("max_inbox_envelopes", models.IntegerField(default=0)),
                ("max_inbox_bytes", models.BigIntegerField(default=0)),
                ("max_contacts", models.IntegerField(default=0)),
                ("max_children", models.IntegerField(default=0)),
                ("max_requests_per_min", models.IntegerField(default=0)),
                ("is_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddField(
            model_name="identity",
            name="quota_overrides",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="identity",
            name="tier",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="members", to="federation.quotatier"),
        ),
        migrations.AddField(
            model_name="envelope",
            name="size_hint",
            field=models.IntegerField(default=0),
        ),
        migrations.RunPython(seed_tiers, unseed),
    ]
