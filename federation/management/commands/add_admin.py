import getpass

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = ("Create/refresh an admin (staff+superuser) with a TOTP device "
            "and print its otpauth:// URL. No QR: paste the URL into your "
            "authenticator.")

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--email", default="")
        parser.add_argument("--password", default=None,
                            help="For scripts. Omit to be prompted (hidden); "
                                 "on an existing user, omitting changes "
                                 "nothing.")
        parser.add_argument("--rotate", action="store_true",
                            help="Replace the TOTP device: new secret, new "
                                 "URL, old tokens stop working.")

    def handle(self, username, email="", password=None, rotate=False,
               **opts):
        user = User.objects.filter(username=username).first()
        created = user is None
        if created:
            pwd = password
            if pwd is None:
                pwd = getpass.getpass(f"Password for {username}: ")
                if not pwd or pwd != getpass.getpass("Repeat: "):
                    raise CommandError("empty or mismatched password")
            user = User.objects.create_user(username=username, email=email,
                                            password=pwd)
        elif password is not None:
            user.set_password(password)
        if email:
            user.email = email
        user.is_staff = True
        user.is_superuser = True
        user.save()

        device = TOTPDevice.objects.filter(user=user, confirmed=True).first()
        if device is not None and rotate:
            device.delete()
            device = None
        if device is None:
            device = TOTPDevice.objects.create(user=user, name="default",
                                               confirmed=True)
            what = "rotated" if rotate else "created"
        else:
            what = "existing (use --rotate for a new secret)"

        self.stdout.write(self.style.SUCCESS(
            f"admin '{username}' {'created' if created else 'updated'} · "
            f"TOTP device {what}"))
        self.stdout.write(device.config_url)
