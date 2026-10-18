from django.http import JsonResponse

from . import protocol


class ProtocolMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        refusal = None
        if (request.path.startswith("/api/fed/")
                and request.path.rstrip("/") != "/api/fed/protocol"):
            refusal = protocol.too_old(protocol.client_proto(
                request.META.get("HTTP_X_HOPLITA_PROTO")))
        if refusal is not None:
            response = JsonResponse(refusal, status=426)
        else:
            response = self.get_response(request)
        response["X-Hoplita-Proto"] = str(protocol.PROTO)
        return response
