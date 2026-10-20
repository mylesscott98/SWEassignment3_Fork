from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class AttemptKind(StrEnum):
    PICKUP = "pickup"
    DELIVERY = "delivery"


class ExecutionRecord(BaseModel):
    """Immutable facts. Lifecycle and access decisions remain with their owners."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="after")
    @classmethod
    def normalise_timestamp(cls, value):
        if isinstance(value, datetime):
            return value.astimezone(UTC)
        return value


class ProofOfDelivery(ExecutionRecord):
    recipient_name: Text
    handover_at: AwareDatetime
    signature_ref: Text | None = None
    photo_ref: Text | None = None

    def check_conditions(self, *, signature_required: bool = False, photo_required: bool = False) -> None:
        """Requirements must come from trusted handover terms, not driver input."""
        if signature_required and not self.signature_ref:
            raise ValueError("the handover requires a signature reference")
        if photo_required and not self.photo_ref:
            raise ValueError("the handover requires a photo reference")


class DeliveryIssue(ExecutionRecord):
    id: Text
    job_id: Text
    attempt_id: Text
    raised_at: AwareDatetime
    reason: Text


class AttemptOutcome(ExecutionRecord):
    kind: AttemptKind
    succeeded: StrictBool
    occurred_at: AwareDatetime
    failure_reason: Text | None = None
    all_parcels_handed_over: StrictBool = False
    proof: ProofOfDelivery | None = None

    @model_validator(mode="after")
    def validate_outcome(self):
        if not self.succeeded:
            if not self.failure_reason:
                raise ValueError("a failed attempt requires a nonblank failure reason")
            if self.proof is not None or self.all_parcels_handed_over:
                raise ValueError("a failed attempt cannot claim successful handover")
        elif self.failure_reason is not None:
            raise ValueError("a successful attempt cannot contain a failure reason")
        if self.kind == AttemptKind.PICKUP:
            if self.proof is not None or self.all_parcels_handed_over:
                raise ValueError("pickup is not delivery and cannot contain handover evidence")
        elif self.succeeded:
            if not self.all_parcels_handed_over or self.proof is None:
                raise ValueError("successful delivery requires full handover and proof")
            if self.proof.handover_at != self.occurred_at:
                raise ValueError("handover time must match the delivery occurrence time")
        return self


class DeliveryAttempt(ExecutionRecord):
    id: Text
    job_id: Text
    delivery_staff_id: Text
    allocation_id: Text
    recorded_at: AwareDatetime
    outcome: AttemptOutcome

    @model_validator(mode="after")
    def validate_recording_time(self):
        if self.outcome.occurred_at > self.recorded_at:
            raise ValueError("an attempt cannot occur after its recording time")
        return self

    def make_issue(self, issue_id: str) -> DeliveryIssue:
        if self.outcome.succeeded:
            raise ValueError("a successful attempt cannot raise a failed-attempt issue")
        return DeliveryIssue(
            id=issue_id,
            job_id=self.job_id,
            attempt_id=self.id,
            raised_at=self.outcome.occurred_at,
            reason=self.outcome.failure_reason,
        )


class IssueReview(ExecutionRecord):
    id: Text
    job_id: Text
    issue_id: Text
    reviewed_by: Text
    reviewed_at: AwareDatetime
    notes: Text
    issue_addressed: StrictBool
    parcels_with_driver: StrictBool
    collection_window: tuple[AwareDatetime, AwareDatetime] | None = None

    @field_validator("collection_window")
    @classmethod
    def validate_window(cls, value):
        if value is None:
            return None
        start, end = (timestamp.astimezone(UTC) for timestamp in value)
        if end <= start:
            raise ValueError("collection window must end after it starts")
        return start, end

    @model_validator(mode="after")
    def validate_resumption_details(self):
        if not self.issue_addressed:
            if self.collection_window is not None:
                raise ValueError("an unresolved issue cannot authorise a collection window")
            return self
        if self.parcels_with_driver:
            if self.collection_window is not None:
                raise ValueError("retained parcels do not require a collection window")
        elif self.collection_window is None or self.collection_window[0] < self.reviewed_at:
            raise ValueError("returned parcels require a future collection window")
        return self
