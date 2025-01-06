from enum import Enum


class NotificationStatus(Enum):
    STAGED = "STAGED"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
