from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from swe.domain.job.execution import (
    AttemptKind,
    AttemptOutcome,
    DeliveryAttempt,
    IssueReview,
    ProofOfDelivery,
)

T = datetime(2026, 10, 20, 1, tzinfo=UTC)


def outcome(**changes):
    return AttemptOutcome.model_validate({"kind": "pickup", "succeeded": True, "occurred_at": T} | changes)


def attempt(**changes):
    return DeliveryAttempt.model_validate(
        {
            "id": "attempt-1",
            "job_id": "job-1",
            "delivery_staff_id": "driver-1",
            "allocation_id": "allocation-1",
            "recorded_at": T,
            "outcome": outcome(),
        }
        | changes
    )


def review(**changes):
    return IssueReview.model_validate(
        {
            "id": "review-1",
            "job_id": "job-1",
            "issue_id": "issue-1",
            "reviewed_by": "staff-1",
            "reviewed_at": T,
            "notes": "Contacted the customer",
            "issue_addressed": False,
            "parcels_with_driver": False,
        }
        | changes
    )


def proof(**changes):
    return ProofOfDelivery.model_validate({"recipient_name": "Sam", "handover_at": T} | changes)


def test_successful_pickup_is_not_delivery():
    assert outcome().kind == AttemptKind.PICKUP
    assert outcome().proof is None


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_failed_attempt_requires_a_nonblank_reason(reason):
    with pytest.raises(ValidationError):
        outcome(succeeded=False, failure_reason=reason)


def test_failure_creates_a_separate_immutable_issue():
    failed = attempt(outcome=outcome(kind="delivery", succeeded=False, failure_reason=" Recipient absent "))
    issue = failed.make_issue("issue-1")
    assert (issue.attempt_id, issue.reason, issue.raised_at) == (failed.id, "Recipient absent", T)
    with pytest.raises(ValidationError):
        issue.reason = "Changed"
    assert failed.outcome.succeeded is False


@pytest.mark.parametrize(
    "changes",
    [
        {"failure_reason": "Unexpected"},
        {"proof": {"recipient_name": "Sam", "handover_at": T}},
        {"all_parcels_handed_over": True},
        {"succeeded": "true"},
        {"kind": "unknown"},
        {"occurred_at": "not-a-date"},
        {"occurred_at": T.replace(tzinfo=None)},
    ],
)
def test_invalid_pickup_input_is_rejected(changes):
    with pytest.raises(ValidationError):
        outcome(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"all_parcels_handed_over": True},
        {"proof": {"recipient_name": "Sam", "handover_at": T}},
    ],
)
def test_incomplete_delivery_is_not_successful(changes):
    with pytest.raises(ValidationError):
        outcome(kind="delivery", **changes)


def test_successful_delivery_retains_valid_proof():
    delivered = outcome(kind="delivery", all_parcels_handed_over=True, proof=proof())
    assert delivered.proof.recipient_name == "Sam"
    with pytest.raises(ValidationError):
        delivered.proof.recipient_name = "Changed"


@pytest.mark.parametrize(
    "changes",
    [
        {"recipient_name": " "},
        {"signature_ref": " "},
        {"photo_ref": " "},
        {"handover_at": T.replace(tzinfo=None)},
    ],
)
def test_invalid_proof_is_rejected(changes):
    with pytest.raises(ValidationError):
        proof(**changes)


def test_handover_time_must_match_the_attempt():
    with pytest.raises(ValidationError):
        outcome(kind="delivery", all_parcels_handed_over=True, proof=proof(handover_at=T + timedelta(seconds=1)))


@pytest.mark.parametrize("condition", ["signature_required", "photo_required"])
def test_trusted_handover_conditions_are_checked(condition):
    with pytest.raises(ValueError):
        proof().check_conditions(**{condition: True})
    proof(signature_ref="signature-1", photo_ref="photo-1").check_conditions(**{condition: True})


def test_failure_cannot_include_proof():
    with pytest.raises(ValidationError):
        outcome(kind="delivery", succeeded=False, failure_reason="Absent", proof=proof())


def test_attempt_cannot_be_recorded_before_it_occurs():
    with pytest.raises(ValidationError):
        attempt(recorded_at=T - timedelta(seconds=1))


def test_success_cannot_generate_a_failure_issue():
    with pytest.raises(ValueError):
        attempt().make_issue("issue-1")


def test_serialisation_round_trip_and_utc_normalisation():
    melbourne = timezone(timedelta(hours=11))
    recorded = attempt(recorded_at=T.astimezone(melbourne))
    assert recorded.recorded_at.tzinfo == UTC
    assert DeliveryAttempt.model_validate_json(recorded.model_dump_json()) == recorded


def test_unknown_fields_and_blank_attribution_are_rejected():
    with pytest.raises(ValidationError):
        attempt(delivery_staff_id=" ")
    with pytest.raises(ValidationError):
        outcome(status="delivered")


def test_review_can_record_an_unresolved_issue():
    assert review().issue_addressed is False


def test_returned_parcels_require_a_new_collection_window():
    with pytest.raises(ValidationError):
        review(issue_addressed=True)
    approved = review(issue_addressed=True, collection_window=(T, T + timedelta(hours=1)))
    assert approved.collection_window[0] == T


@pytest.mark.parametrize(
    "window",
    [
        (T, T),
        (T + timedelta(hours=1), T),
        (T - timedelta(hours=1), T + timedelta(hours=1)),
    ],
)
def test_invalid_reattempt_window_is_rejected(window):
    with pytest.raises(ValidationError):
        review(issue_addressed=True, collection_window=window)


def test_retained_parcels_need_no_recollection_window():
    assert review(issue_addressed=True, parcels_with_driver=True).collection_window is None
    with pytest.raises(ValidationError):
        review(issue_addressed=True, parcels_with_driver=True, collection_window=(T, T + timedelta(hours=1)))


def test_unresolved_review_cannot_schedule_collection():
    with pytest.raises(ValidationError):
        review(collection_window=(T, T + timedelta(hours=1)))
