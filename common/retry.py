from datetime import timedelta

from temporalio.common import RetryPolicy

from common.names import Errors

DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=6,
    non_retryable_error_types=[Errors.CORRUPT_DOCUMENT, Errors.PERMISSION_DENIED, Errors.UNKNOWN_TOOL],
)

PATIENT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=0,
    non_retryable_error_types=[Errors.CORRUPT_DOCUMENT, Errors.PERMISSION_DENIED, Errors.UNKNOWN_TOOL],
)
PATIENT_DEADLINE = timedelta(minutes=30)
