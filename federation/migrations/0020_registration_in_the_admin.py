import os

import django.db.models.deletion
import federation.models
from django.db import migrations, models


def bring_over_the_environment(apps, schema_editor):
    Policy = apps.get_model("federation", "RegistrationPolicy")
    Invite = apps.get_model("federation", "InviteCode")
    opened = (os.environ.get("HOPLITA_OPEN_REGISTRATION") or "").strip().lower() \
        in ("1", "true", "yes", "on", "si", "sí")
    codes = [c.strip() for c in
             (os.environ.get("HOPLITA_INVITE_CODES") or "").split(",") if c.strip()]
    for c in dict.fromkeys(codes):
        Invite.objects.get_or_create(code=c[:64], defaults={
            "note": "brought over from the environment", "max_uses": 0})
    mode = "open" if opened else ("invite" if codes else "closed")
    Policy.objects.update_or_create(pk=1, defaults={"mode": mode})


class Migration(migrations.Migration):

    dependencies = [
        ('federation', '0019_identity_ws_conns'),
    ]

    operations = [
        migrations.CreateModel(
            name='InviteCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(default=federation.models._new_invite_code, help_text='What the person types. A random one is proposed; any text works.', max_length=64, unique=True)),
                ('note', models.CharField(blank=True, help_text='Who it is for, so you recognise it later.', max_length=200)),
                ('max_uses', models.PositiveIntegerField(default=1, help_text='How many accounts it can create. 0 = unlimited.')),
                ('uses', models.PositiveIntegerField(default=0)),
                ('expires_at', models.DateTimeField(blank=True, help_text='Optional: after this instant it no longer works.', null=True)),
                ('active', models.BooleanField(default=True, help_text='Untick to withdraw it. Accounts already created stay.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'invitation code',
                'verbose_name_plural': '🎟 Invitation codes',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='RegistrationPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mode', models.CharField(choices=[('closed', 'Closed — nobody can sign up'), ('invite', 'Invitation — only with a usable code'), ('open', 'Open — anyone can sign up')], default='invite', help_text='Open on a public domain means anyone who finds it creates identities and spends quota. It also needs working mail: the verification PIN goes by email.', max_length=10)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'sign-up policy',
                'verbose_name_plural': '🚪 Sign-up policy',
            },
        ),
        migrations.AddField(
            model_name='identity',
            name='invite',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='identities', to='federation.invitecode'),
        ),
        migrations.RunPython(bring_over_the_environment,
                             migrations.RunPython.noop),
    ]
