from django.core.management.base import BaseCommand

from federation.models import InviteCode, RegistrationPolicy


class Command(BaseCommand):
    help = ("Show or set who may sign up (closed, invite, open) and mint "
            "invitation codes.")

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=[m for m, _ in
                                               RegistrationPolicy.MODE_CHOICES])
        parser.add_argument("--new", action="store_true",
                            help="Mint an invitation code and print it.")
        parser.add_argument("--ensure", action="store_true",
                            help="Print a usable code, minting one only if "
                                 "none exists; a closed server moves to invite.")
        parser.add_argument("--note", default="")
        parser.add_argument("--uses", type=int, default=1,
                            help="Accounts the new code can create (0 = unlimited).")

    def handle(self, *args, **o):
        policy = RegistrationPolicy.current()
        if o["mode"]:
            policy.mode = o["mode"]
            policy.save()
        code = None
        if o["ensure"]:
            if policy.mode == RegistrationPolicy.CLOSED:
                policy.mode = RegistrationPolicy.INVITE
                policy.save()
            have = InviteCode.usable().order_by("created_at").first()
            code = have or InviteCode.objects.create(
                note=o["note"], max_uses=max(0, o["uses"]))
        elif o["new"]:
            code = InviteCode.objects.create(note=o["note"],
                                             max_uses=max(0, o["uses"]))

        self.stdout.write(f"sign-up: {policy}")
        for c in InviteCode.objects.order_by("created_at"):
            self.stdout.write(f"  {c.code:<24} {c.state:<10} "
                              f"{c.uses}/{c.max_uses or '∞'}  {c.note}")
        if code is not None:
            self.stdout.write(code.code)
