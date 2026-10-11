from django.db import migrations


def up(apps, schema_editor):
    apps.get_model("federation", "QuotaTier").objects.filter(
        name="nobody", max_requests_per_min=60).update(max_requests_per_min=300)


def down(apps, schema_editor):
    apps.get_model("federation", "QuotaTier").objects.filter(
        name="nobody", max_requests_per_min=300).update(max_requests_per_min=60)


class Migration(migrations.Migration):

    dependencies = [("federation", "0017_contact_attestation_identity_sign_pub_and_more")]

    operations = [migrations.RunPython(up, down)]
