import logging
import time

from rest_framework.throttling import SimpleRateThrottle

logger = logging.getLogger(__name__)

_BREAKER_SECONDS = 30.0
_cache_down_until = 0.0


def cache_breaker_open(now=None):
    return (now if now is not None else time.time()) < _cache_down_until


def trip_cache_breaker(now=None, seconds=_BREAKER_SECONDS):
    global _cache_down_until
    _cache_down_until = (now if now is not None else time.time()) + seconds
    return _cache_down_until


class TierRateThrottle(SimpleRateThrottle):
    scope = "tier"

    def __init__(self):
        pass

    def get_cache_key(self, request, view):
        return f"throttle_tier_{request.user.id}"

    def allow_request(self, request, view):
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return True
        try:
            from .quotas import effective_quota
            rpm = int(effective_quota(user.identity).get("max_requests_per_min", 0) or 0)
        except Exception:
            return True
        if rpm <= 0:
            return True
        if cache_breaker_open():
            return True
        self.num_requests, self.duration = rpm, 60
        self.key = self.get_cache_key(request, view)
        try:
            self.history = self.cache.get(self.key, [])
            self.now = self.timer()
            while self.history and self.history[-1] <= self.now - self.duration:
                self.history.pop()
            if len(self.history) >= self.num_requests:
                return self.throttle_failure()
            return self.throttle_success()
        except Exception as e:
            until = trip_cache_breaker()
            logger.warning(
                "throttle cache unavailable (%s): failing OPEN, skipping the "
                "cache until %.0f", e, until)
            return True


def allow_identity(identity, user_id, now=None):
    from django.core.cache import cache
    from .quotas import effective_quota
    try:
        rpm = int(effective_quota(identity).get("max_requests_per_min", 0) or 0)
    except Exception:
        return True, 0
    if rpm <= 0:
        return True, 0
    now = time.time() if now is None else now
    if cache_breaker_open(now):
        return True, 0
    key = f"throttle_tier_{user_id}"
    try:
        history = [t for t in (cache.get(key) or []) if t > now - 60]
        if len(history) >= rpm:
            return False, max(1, int(60 - (now - history[-1])))
        history.insert(0, now)
        cache.set(key, history, 60)
        return True, 0
    except Exception as e:
        until = trip_cache_breaker(now)
        logger.warning("throttle cache unavailable (%s): failing OPEN on the "
                       "socket, skipping the cache until %.0f", e, until)
        return True, 0
