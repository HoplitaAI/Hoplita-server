from django.core.management.base import BaseCommand

from federation import services


class Command(BaseCommand):
    help = "Sweep undelivered mail older than the TTL (default: 30 days)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None,
                            help="TTL in days (0 = never sweep).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Count what WOULD go, delete nothing.")

    def handle(self, *args, **opts):
        days = opts.get("days")
        if opts.get("dry_run"):
            from datetime import timedelta
            from django.db.models import Sum
            from django.utils import timezone
            from federation.models import Envelope
            d = services.MAIL_TTL_DAYS if days is None else days
            if d <= 0:
                self.stdout.write("TTL is 0: nothing would be swept.")
                return
            cut = timezone.now() - timedelta(days=d)
            qs = Envelope.objects.filter(delivered=False, created_at__lt=cut)
            self.stdout.write(
                f"{qs.count()} envelope(s), "
                f"{(qs.aggregate(s=Sum('size_hint'))['s'] or 0) // 1024} KB "
                f"older than {d}d would be swept.")
            return
        r = services.sweep_stale_mail(days)
        self.stdout.write(
            f"swept {r['swept']} envelope(s), {r['freed_bytes'] // 1024} KB "
            f"freed (TTL {r['ttl_days']}d)")
