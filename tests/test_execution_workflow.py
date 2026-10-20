from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from swe.api import create_app
from swe.app import DeliveryManagementSystem
from swe.domain.user import Customer, DeliveryStaff, StaffMember
from swe.events import handlers

T = datetime(2026, 10, 20, 1, tzinfo=UTC)


def post(c, path, body=None, user="staff-1"):
    return c.client.post(path, json=body, headers={"X-User-Id": user})


def tracking(c, user="staff-1"):
    response = c.client.get(f"/tracking/jobs/{c.job_id}", headers={"X-User-Id": user})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def flow(repo, monkeypatch):
    clock = [T]
    system = DeliveryManagementSystem(repo, clock=lambda: clock[0])
    for account in [
        Customer(user_id="cust-1", name="Sam", phone="0400000000"),
        Customer(user_id="cust-2", name="Jo", phone="0400000001"),
        StaffMember(user_id="staff-1", name="Alex", phone="0400000002"),
        DeliveryStaff(user_id="driver-1", name="Lee", phone="0400000003"),
        DeliveryStaff(user_id="driver-2", name="Kim", phone="0400000004"),
    ]:
        system.users.add(account)
    monkeypatch.setattr(handlers, "system", lambda: system)
    c = SimpleNamespace(client=TestClient(create_app(system)), clock=clock, repo=repo)
    response = post(
        c,
        "/requests",
        {
            "contact_name": "Sam",
            "contact_phone": "0400000000",
            "parcels": [{"size": "small", "weight_kg": "2.5"}],
            "pickup_address": {"street": "1 A St", "suburb": "Hawthorn", "postcode": "3122"},
            "dropoff_address": {"street": "2 B St", "suburb": "Kew", "postcode": "3101"},
        },
        user="cust-1",
    )
    assert response.status_code == 201, response.text
    request_id = response.json()["request_id"]
    response = post(
        c,
        f"/requests/{request_id}/quotes",
        {
            "price": "50",
            "eta_pickup": [T.isoformat(), (T + timedelta(hours=2)).isoformat()],
            "eta_dropoff": [(T + timedelta(hours=3)).isoformat(), (T + timedelta(hours=4)).isoformat()],
        },
    )
    assert response.status_code == 201, response.text
    quote_id = response.json()["quote_id"]
    response = post(c, f"/requests/{request_id}/quotes/{quote_id}/accept", user="cust-1")
    assert response.status_code == 202, response.text
    # Stand in for the stream/EventBridge hop, as the existing API tests do.
    handlers.create_delivery_job({"detail": response.json()})
    c.job_id = response.json()["job_id"]
    for driver in ("driver-1", "driver-2"):
        response = post(
            c,
            "/availability",
            {"window": [T.isoformat(), (T + timedelta(days=1)).isoformat()]},
            user=driver,
        )
        assert response.status_code == 201, response.text
    response = post(c, f"/jobs/{c.job_id}/allocate", {"delivery_staff_id": "driver-1"})
    assert response.status_code == 201, response.text
    return c


def attempt(c, kind="pickup", succeeded=True, user="driver-1", **changes):
    c.clock[0] += timedelta(minutes=1)
    outcome = {"kind": kind, "succeeded": succeeded, "occurred_at": c.clock[0].isoformat()}
    if not succeeded:
        outcome["failure_reason"] = "Recipient unavailable"
    if kind == "delivery" and succeeded:
        outcome["all_parcels_handed_over"] = True
        outcome["proof"] = {"recipient_name": "Sam", "handover_at": outcome["occurred_at"]}
    outcome.update(changes)
    body = {"submission_id": str(uuid4()), "outcome": outcome}
    return post(c, f"/jobs/{c.job_id}/attempts", body, user=user), body


def review(c, issue_id, addressed, retained, user="staff-1"):
    c.clock[0] += timedelta(minutes=1)
    body = {
        "submission_id": str(uuid4()),
        "issue_id": issue_id,
        "notes": "Contacted customer and recorded the next action",
        "issue_addressed": addressed,
        "parcels_with_driver": retained,
    }
    if addressed and not retained:
        start = c.clock[0] + timedelta(minutes=1)
        body["collection_window"] = [start.isoformat(), (start + timedelta(hours=1)).isoformat()]
    return post(c, f"/jobs/{c.job_id}/issue-reviews", body, user=user), body


def test_successful_pickup_and_delivery_do_not_close_the_job(flow):
    response, _ = attempt(flow)
    assert response.status_code == 200, response.text
    assert tracking(flow)["job"]["state"] == "in_transit"
    response, _ = attempt(flow, kind="delivery")
    assert response.status_code == 200, response.text
    detail = tracking(flow, user="cust-1")
    assert detail["job"]["state"] == "delivered"
    assert len(detail["attempts"]) == 2
    assert detail["attempts"][-1]["outcome"]["proof"]["recipient_name"] == "Sam"
    assert any(event.get("action") == "delivery_succeeded" for event in detail["history"])
    response, _ = attempt(flow)
    assert response.status_code == 400
    assert len(tracking(flow)["attempts"]) == 2


def test_failed_delivery_return_review_recollection_and_success(flow):
    assert attempt(flow)[0].status_code == 200
    failed, _ = attempt(flow, kind="delivery", succeeded=False)
    assert failed.status_code == 200, failed.text
    failure_id = failed.json()["id"]
    assert tracking(flow)["job"]["state"] == "on_hold"
    assert attempt(flow, kind="delivery")[0].status_code == 400
    response, body = review(flow, failure_id, addressed=True, retained=False)
    assert response.status_code == 200, response.text
    assert tracking(flow)["job"]["state"] == "allocated"
    assert len(tracking(flow)["attempts"]) == 2
    flow.clock[0] = datetime.fromisoformat(body["collection_window"][0])
    assert attempt(flow)[0].status_code == 200
    assert attempt(flow, kind="delivery")[0].status_code == 200
    detail = tracking(flow)
    assert detail["job"]["state"] == "delivered"
    assert len(detail["attempts"]) == 4
    original = next(a for a in detail["attempts"] if a["id"] == failure_id)
    assert original["outcome"]["succeeded"] is False
    assert detail["issues"][0]["reason"] == "Recipient unavailable"
    assert len(detail["reviews"]) == 1


def test_unresolved_issue_stays_held_then_retained_parcels_resume_without_collection(flow):
    assert attempt(flow)[0].status_code == 200
    failed, _ = attempt(flow, kind="delivery", succeeded=False)
    assert failed.status_code == 200
    issue_id = failed.json()["id"]
    assert review(flow, issue_id, addressed=False, retained=True)[0].status_code == 200
    assert tracking(flow)["job"]["state"] == "on_hold"
    assert review(flow, issue_id, addressed=True, retained=True)[0].status_code == 200
    assert tracking(flow)["job"]["state"] == "in_transit"
    assert len(tracking(flow)["attempts"]) == 2
    assert attempt(flow, kind="delivery")[0].status_code == 200
    assert len(tracking(flow)["attempts"]) == 3


@pytest.mark.parametrize("user", ["driver-2", "cust-1", "staff-1"])
def test_only_the_current_driver_can_submit_attempts(flow, user):
    response, _ = attempt(flow, user=user)
    assert response.status_code == 403
    assert tracking(flow)["attempts"] == []
    assert tracking(flow)["job"]["state"] == "allocated"


def test_invalid_proof_causes_no_partial_write(flow):
    assert attempt(flow)[0].status_code == 200
    before = tracking(flow)
    response, _ = attempt(flow, kind="delivery", proof=None)
    assert response.status_code == 422
    after = tracking(flow)
    assert after["attempts"] == before["attempts"]
    assert after["history"] == before["history"]
    assert after["job"]["state"] == "in_transit"


def test_failed_pickup_does_not_establish_custody_and_driver_cannot_review(flow):
    failed, _ = attempt(flow, succeeded=False)
    assert failed.status_code == 200
    issue_id = failed.json()["id"]
    assert review(flow, issue_id, addressed=True, retained=True)[0].status_code == 400
    assert review(flow, issue_id, addressed=False, retained=False, user="driver-1")[0].status_code == 403
    assert tracking(flow)["job"]["state"] == "on_hold"
    assert tracking(flow)["reviews"] == []


def test_duplicate_submission_is_replayed_and_changed_input_is_rejected(flow):
    response, body = attempt(flow)
    assert response.status_code == 200
    before = tracking(flow)
    replay = post(flow, f"/jobs/{flow.job_id}/attempts", body, user="driver-1")
    assert replay.status_code == 200
    assert replay.json() == response.json()
    assert tracking(flow)["history"] == before["history"]
    assert len(tracking(flow)["attempts"]) == 1
    changed = deepcopy(body)
    changed["outcome"]["succeeded"] = False
    changed["outcome"]["failure_reason"] = "Different input"
    assert post(flow, f"/jobs/{flow.job_id}/attempts", changed, user="driver-1").status_code == 409


def test_tracking_isolated_to_customer_and_driver_relationships(flow):
    for user in ("cust-2", "driver-2"):
        response = flow.client.get(f"/tracking/jobs/{flow.job_id}", headers={"X-User-Id": user})
        assert response.status_code == 403
    assert tracking(flow, user="cust-1")["job"]["job_id"] == flow.job_id


def test_reallocation_requires_physical_custody_transfer_and_preserves_attribution(flow):
    assert attempt(flow)[0].status_code == 200
    response = post(
        flow,
        f"/jobs/{flow.job_id}/reallocate",
        {"delivery_staff_id": "driver-2", "reason": "Driver handover"},
    )
    assert response.status_code == 201, response.text
    assert attempt(flow, kind="delivery", user="driver-2")[0].status_code == 400
    flow.clock[0] += timedelta(minutes=1)
    response = post(
        flow,
        f"/jobs/{flow.job_id}/custody-transfers",
        {
            "submission_id": str(uuid4()),
            "from_driver_id": "driver-1",
            "occurred_at": flow.clock[0].isoformat(),
            "notes": "Parcels physically handed to the newly allocated driver",
        },
    )
    assert response.status_code == 200, response.text
    assert attempt(flow, kind="delivery", user="driver-2")[0].status_code == 200
    detail = tracking(flow, user="driver-1")
    assert detail["attempts"][0]["delivery_staff_id"] == "driver-1"
    assert detail["attempts"][-1]["delivery_staff_id"] == "driver-2"
    listed = flow.client.get("/tracking/jobs", headers={"X-User-Id": "driver-1"})
    assert listed.status_code == 200
    assert any(item["job"]["job_id"] == flow.job_id for item in listed.json())


def test_concurrent_reallocation_rolls_back_the_losing_attempt(flow, monkeypatch):
    original_factory = flow.repo.transaction

    def racing_transaction():
        transaction = original_factory()
        original_commit = transaction.commit

        def commit_after_reallocation():
            winner = flow.repo.get_job(flow.job_id)
            old_allocation = winner.allocation_id
            allocation, event = winner.reallocate(
                "driver-2",
                reason="Concurrent office reallocation",
                by="staff-1",
                at=flow.clock[0],
            )
            (
                original_factory()
                .update(
                    winner,
                    ["state", "allocation_id", "delivery_staff_id"],
                    where={"allocation_id": old_allocation},
                )
                .create(allocation)
                .create(event)
                .commit()
            )
            original_commit()

        monkeypatch.setattr(transaction, "commit", commit_after_reallocation)
        return transaction

    monkeypatch.setattr(flow.repo, "transaction", racing_transaction)
    response, _ = attempt(flow)
    assert response.status_code == 409, response.text
    detail = tracking(flow)
    assert detail["job"]["delivery_staff_id"] == "driver-2"
    assert detail["job"]["state"] == "allocated"
    assert detail["attempts"] == []
    assert detail["issues"] == []
    assert detail["progress"]["revision"] == 0
    assert not any(event.get("action") == "pickup_succeeded" for event in detail["history"])
