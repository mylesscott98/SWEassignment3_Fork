from pydantic import BaseModel

from swe.domain.job.execution import AttemptOutcome, DeliveryAttempt, DeliveryIssue, IssueReview
from swe.domain.job.execution_state import CommandReceipt, CustodyTransfer, ExecutionChanged, ExecutionProgress
from swe.repo.schema import Codec, ItemSchema, Number, Optional, String, Timestamp, Window
from swe.repo.schemas.shared import HISTORY_EVENT


class ModelJson(Codec):
    def __init__(self, model: type[BaseModel]):
        self.model = model

    def encode(self, value: BaseModel) -> dict:
        return {"S": value.model_dump_json()}

    def decode(self, attribute: dict) -> BaseModel:
        return self.model.model_validate_json(attribute["S"])


class Boolean(Codec):
    def encode(self, value: bool) -> dict:
        return {"BOOL": value}

    def decode(self, attribute: dict) -> bool:
        return attribute["BOOL"]


ATTEMPT = ItemSchema(
    DeliveryAttempt,
    pk="JOB#{job_id}",
    sk="ATTEMPT#{id}",
    attributes={
        "id": String,
        "job_id": String,
        "delivery_staff_id": String,
        "allocation_id": String,
        "recorded_at": Timestamp,
        "outcome": ModelJson(AttemptOutcome),
    },
)
ISSUE = ItemSchema(
    DeliveryIssue,
    pk="JOB#{job_id}",
    sk="ISSUE#{id}",
    attributes={"id": String, "job_id": String, "attempt_id": String, "raised_at": Timestamp, "reason": String},
)
REVIEW = ItemSchema(
    IssueReview,
    pk="JOB#{job_id}",
    sk="REVIEW#{id}",
    attributes={
        "id": String,
        "job_id": String,
        "issue_id": String,
        "reviewed_by": String,
        "reviewed_at": Timestamp,
        "notes": String,
        "issue_addressed": Boolean(),
        "parcels_with_driver": Boolean(),
        "collection_window": Optional(Window),
    },
)
PROGRESS = ItemSchema(
    ExecutionProgress,
    pk="JOB#{job_id}",
    sk="EXECUTION",
    attributes={
        "job_id": String,
        "revision": Number,
        "open_issue_id": Optional(String),
        "custodian_id": Optional(String),
        "last_occurred_at": Optional(Timestamp),
        "updated_at": Optional(Timestamp),
        "collection_window": Optional(Window),
    },
)
RECEIPT = ItemSchema(
    CommandReceipt,
    pk="JOB#{job_id}",
    sk="COMMAND#{id}",
    attributes={"id": String, "job_id": String, "actor_id": String, "action": String, "fingerprint": String},
)
TRANSFER = ItemSchema(
    CustodyTransfer,
    pk="JOB#{job_id}",
    sk="TRANSFER#{id}",
    attributes={
        "id": String,
        "job_id": String,
        "recorded_by": String,
        "recorded_at": Timestamp,
        "from_driver_id": String,
        "to_driver_id": String,
        "occurred_at": Timestamp,
        "notes": String,
    },
)
CHANGED = HISTORY_EVENT.extend(
    ExecutionChanged,
    {
        "event_type": String,
        "job_id": String,
        "record_id": String,
        "action": String,
        "previous_state": String,
        "resulting_state": String,
    },
)
