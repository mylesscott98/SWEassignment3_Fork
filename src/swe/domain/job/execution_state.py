from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictBool, StringConstraints

from swe.domain.job.execution import AttemptOutcome, ExecutionRecord, Text
from swe.domain.shared import HistoryEvent

SubmissionId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{8,80}$")]


class RecordAttempt(ExecutionRecord):
    submission_id: SubmissionId
    outcome: AttemptOutcome


class ReviewIssue(ExecutionRecord):
    submission_id: SubmissionId
    issue_id: Text
    notes: Text
    issue_addressed: StrictBool
    parcels_with_driver: StrictBool
    collection_window: tuple[AwareDatetime, AwareDatetime] | None = None


class TransferCustody(ExecutionRecord):
    submission_id: SubmissionId
    from_driver_id: Text
    occurred_at: AwareDatetime
    notes: Text


class CustodyTransfer(ExecutionRecord):
    id: Text
    job_id: Text
    recorded_by: Text
    recorded_at: AwareDatetime
    from_driver_id: Text
    to_driver_id: Text
    occurred_at: AwareDatetime
    notes: Text


class ExecutionProgress(ExecutionRecord):
    job_id: Text
    revision: int = Field(default=0, ge=0)
    open_issue_id: str | None = None
    custodian_id: str | None = None
    last_occurred_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    collection_window: tuple[AwareDatetime, AwareDatetime] | None = None


class CommandReceipt(ExecutionRecord):
    id: Text
    job_id: Text
    actor_id: Text
    action: Literal["attempt", "review", "transfer"]
    fingerprint: Text


class ExecutionChanged(HistoryEvent):
    event_type: Literal["execution_changed"] = "execution_changed"
    job_id: str
    record_id: str
    action: str
    previous_state: str
    resulting_state: str
