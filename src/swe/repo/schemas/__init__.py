# Stored types selected by model class, event type, or account kind.
from swe.domain.shared import HistoryEvent
from swe.repo.schema import ItemSchema
from swe.repo.schemas import execution, job, request, user

SCHEMAS: dict[type, ItemSchema] = {
    schema.model: schema
    for schema in [
        request.DELIVERY_REQUEST,
        request.QUOTE,
        request.REQUEST_LOGGED,
        request.REQUEST_CANCELLED,
        request.QUOTE_ISSUED,
        request.QUOTE_ACCEPTED,
        request.QUOTE_REJECTED,
        job.DELIVERY_JOB,
        job.JOB_CREATED,
        job.ALLOCATION,
        job.JOB_ALLOCATED,
        user.CUSTOMER,
        user.STAFF_MEMBER,
        user.DELIVERY_STAFF,
        user.AVAILABILITY_SLOT,
        execution.ATTEMPT,
        execution.ISSUE,
        execution.REVIEW,
        execution.PROGRESS,
        execution.RECEIPT,
        execution.TRANSFER,
        execution.CHANGED,
    ]
}
EVENT_SCHEMAS: dict[str, ItemSchema] = {
    cls.TYPE: SCHEMAS[cls] for cls in HistoryEvent.registry.values() if cls in SCHEMAS
}
USER_SCHEMAS: dict[str, ItemSchema] = {
    schema.model.model_fields["kind"].default: schema
    for schema in [user.CUSTOMER, user.STAFF_MEMBER, user.DELIVERY_STAFF]
}


def schema_for(model: type | object) -> ItemSchema:
    cls = model if isinstance(model, type) else type(model)
    if cls not in SCHEMAS:
        raise LookupError(f"no schema for {cls.__name__}")
    return SCHEMAS[cls]


def event_schema_for(item: dict) -> ItemSchema:
    event_type = item.get("event_type", {}).get("S")
    if event_type not in EVENT_SCHEMAS:
        raise ValueError(f"no schema for event_type {event_type!r}")
    return EVENT_SCHEMAS[event_type]


def user_schema_for(item: dict) -> ItemSchema:
    kind = item.get("kind", {}).get("S")
    if kind not in USER_SCHEMAS:
        raise ValueError(f"no schema for user kind {kind!r}")
    return USER_SCHEMAS[kind]
