import logging
import time

logger = logging.getLogger('request_logger')


class RequestLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start_time = time.time()

        response = self.get_response(request)

        duration = time.time() - start_time
        logger.info(
            f"Request: {request.method} {request.path} "
            f"Status: {response.status_code} "
            f"Duration: {duration:.2f}s"
        )

        return response