class LLMError(Exception):
    """Base exception for LLM-related errors"""
    status_code = 500

class LLMTimeoutError(LLMError):
    """Raised when LLM request times out"""
    status_code = 504

class LLMRateLimitError(LLMError):
    """Raised when LLM rate limit is exceeded"""
    status_code = 429

class LLMConnectionError(LLMError):
    """Raised when connection to LLM service fails"""
    status_code = 503

class InvalidResponseError(LLMError):
    """Raised when LLM returns invalid response format"""
    status_code = 502

class TokenLimitError(LLMError):
    """Raised when input exceeds token limit"""
    status_code = 400