from datetime import UTC, datetime, timedelta

from swe.domain.job.execution import AttemptOutcome, DeliveryAttempt, IssueReview
from swe.domain.job.execution_state import CommandReceipt, ExecutionChanged, ExecutionProgress
from swe.repo.schemas import event_schema_for, schema_for
from swe.repo.schemas.execution import ATTEMPT, PROGRESS, RECEIPT

T = datetime(2026, 10, 20, 1, tzinfo=UTC)


def test_execution_models_round_trip():
    attempt = DeliveryAttempt(
        id="attempt-1",
        job_id="job-1",
        delivery_staff_id="driver-1",
        allocation_id="allocation-1",
        recorded_at=T,
        outcome=AttemptOutcome(kind="pickup", succeeded=False, occurred_at=T, failure_reason="Absent"),
    )
    review = IssueReview(
        id="review-1",
        job_id="job-1",
        issue_id="attempt-1",
        reviewed_by="staff-1",
        reviewed_at=T,
        notes="Returned; approved retry",
        issue_addressed=True,
        parcels_with_driver=False,
        collection_window=(T, T + timedelta(hours=1)),
    )
    event = ExecutionChanged(
        request_id="request-1",
        job_id="job-1",
        record_id="attempt-1",
        action="pickup_failed",
        previous_state="allocated",
        resulting_state="on_hold",
        created_at=T,
        created_by="driver-1",
    )
    for record in [attempt, attempt.make_issue("attempt-1"), review, event, ExecutionProgress(job_id="job-1")]:
        schema = schema_for(record)
        assert schema.from_item(schema.to_item(record)) == record
    assert event_schema_for(schema_for(event).to_item(event)).model is ExecutionChanged


def test_execution_transaction_stores_the_complete_unit(repo):
    attempt = DeliveryAttempt(
        id="attempt-1",
        job_id="job-1",
        delivery_staff_id="driver-1",
        allocation_id="allocation-1",
        recorded_at=T,
        outcome=AttemptOutcome(kind="pickup", succeeded=True, occurred_at=T),
    )
    progress = ExecutionProgress(job_id="job-1", revision=1, custodian_id="driver-1", updated_at=T)
    receipt = CommandReceipt(
        id="attempt-1",
        job_id="job-1",
        actor_id="driver-1",
        action="attempt",
        fingerprint="fingerprint",
    )
    repo.transaction().create(attempt).create(progress).create(receipt).commit()
    assert repo._get(ATTEMPT, job_id="job-1", id="attempt-1") == attempt
    assert repo._get(PROGRESS, job_id="job-1") == progress
    assert repo._get(RECEIPT, job_id="job-1", id="attempt-1") == receipt
