from django.core.management.base import BaseCommand

from federation import services


class Command(BaseCommand):
    help = "Boot chores: clear ghost presence and sweep stale mail."

    def handle(self, *args, **opts):
        try:
            n = services.clear_stale_presence()
        except Exception as e:
            self.stderr.write(f"presence: {e}")
            n = None
        if n:
            self.stdout.write(f"presence: {n} ghost flag(s) cleared")
        try:
            r = services.sweep_stale_mail()
        except Exception as e:
            self.stderr.write(f"mail: {e}")
            return
        if r.get("swept"):
            self.stdout.write(
                f"mail: swept {r['swept']} envelope(s), "
                f"{r['freed_bytes'] // 1024} KB freed (TTL {r['ttl_days']}d)")
