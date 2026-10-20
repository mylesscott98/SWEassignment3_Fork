# Single source of truth for EventBridge rules and Lambda targets.
from swe.events import handlers
from swe.events.execution import notify_execution

ROUTES = {
    "create-delivery-job": handlers.create_delivery_job,
    "notify-office-staff": handlers.notify_office_staff,
    "notify-customer": handlers.notify_customer,
    "notify-driver": handlers.notify_driver,
    "notify-execution": notify_execution,
    "audit": handlers.audit,
}


def function_name(route: str) -> str:
    return f"yowd-{route}"


def handler_path(route: str) -> str:
    handler = ROUTES[route]
    return f"{handler.__module__}.{handler.__name__}"
