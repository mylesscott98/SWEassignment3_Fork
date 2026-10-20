import hashlib
from datetime import UTC

from swe.app.errors import ForbiddenError
from swe.domain.job import JobStateName
from swe.domain.job.execution import AttemptKind, DeliveryAttempt, IssueReview
from swe.domain.job.execution_state import (
    CommandReceipt,
    CustodyTransfer,
    ExecutionChanged,
    ExecutionProgress,
)
from swe.domain.shared import DomainError
from swe.domain.user import Customer, DeliveryStaff, StaffMember
from swe.repo import ConflictError, NotFoundError
from swe.repo.schemas.execution import ATTEMPT, ISSUE, PROGRESS, RECEIPT, REVIEW, TRANSFER

RESULTS = {"attempt": ATTEMPT, "review": REVIEW, "transfer": TRANSFER}


class DeliveryExecution:
    """Execution commands share the system's repository, roster, users and clock."""

    def __init__(self, system, *, signature_required=False, photo_required=False):
        self.system, self.repo = system, system.repo
        self.signature_required, self.photo_required = signature_required, photo_required

    def _user(self, user_id):
        try:
            user = self.system.identify(user_id)
        except NotFoundError:
            raise ForbiddenError("no account for this user") from None
        if not getattr(user, "active", True):
            raise ForbiddenError("this account is inactive")
        return user

    def _role(self, user_id, role):
        if not isinstance(self._user(user_id), role):
            raise ForbiddenError("this account cannot perform this action")

    def progress(self, job_id):
        try:
            return self.repo._get(PROGRESS, job_id=job_id)
        except NotFoundError:
            return ExecutionProgress(job_id=job_id)

    def records(self, schema, job_id):
        return [schema.from_item(i) for i in self.repo._query_partition({"S": f"JOB#{job_id}"}, schema)]

    def _driver_has_seen(self, job, user_id):
        return job.delivery_staff_id == user_id or any(
            a.delivery_staff_id == user_id for a in self.repo.list_allocations(job.job_id)
        )

    def track(self, job_id, by):
        user, job = self._user(by), self.repo.get_job(job_id)
        allowed = (
            isinstance(user, StaffMember)
            or (isinstance(user, Customer) and job.customer_id == by)
            or (isinstance(user, DeliveryStaff) and self._driver_has_seen(job, by))
        )
        if not allowed:
            raise ForbiddenError("this job is not available to your account")
        return {
            "job": job,
            "request": self.repo.get_request(job.request_id),
            "quote": self.repo.get_quote(job.request_id, job.quote_id),
            "allocations": self.repo.list_allocations(job_id),
            "progress": self.progress(job_id),
            "attempts": sorted(self.records(ATTEMPT, job_id), key=lambda a: (a.outcome.occurred_at, a.id)),
            "issues": sorted(self.records(ISSUE, job_id), key=lambda i: (i.raised_at, i.id)),
            "reviews": sorted(self.records(REVIEW, job_id), key=lambda r: (r.reviewed_at, r.id)),
            "transfers": sorted(self.records(TRANSFER, job_id), key=lambda t: (t.occurred_at, t.id)),
            "history": self.repo.get_history(job.request_id),
        }

    def driver_jobs(self, by):
        self._role(by, DeliveryStaff)
        jobs = {
            j.job_id: j
            for state in JobStateName
            for j in self.repo.list_jobs_by_state(state)
            if self._driver_has_seen(j, by)
        }
        return [
            {"job": j, "request": self.repo.get_request(j.request_id)}
            for j in sorted(jobs.values(), key=lambda j: j.created_at, reverse=True)
        ]

    def _receipt(self, job_id, by, action, command):
        return CommandReceipt(
            id=command.submission_id,
            job_id=job_id,
            actor_id=by,
            action=action,
            fingerprint=hashlib.sha256(command.model_dump_json().encode()).hexdigest(),
        )

    def _replay(self, receipt):
        try:
            previous = self.repo._get(RECEIPT, job_id=receipt.job_id, id=receipt.id)
        except NotFoundError:
            return None
        if previous != receipt:
            raise ConflictError("submission ID already belongs to different input or another caller")
        return self.repo._get(RESULTS[receipt.action], job_id=receipt.job_id, id=receipt.id)

    def _now(self, progress):
        at = self.system.clock().astimezone(UTC)
        if progress.updated_at and at < progress.updated_at:
            raise DomainError("recording time cannot precede the previous update")
        return at

    def _allocation(self, job, by):
        self._role(by, DeliveryStaff)
        if job.delivery_staff_id != by or not job.allocation_id:
            raise ForbiddenError("only the current allocated driver may record attempts")
        allocation = next((a for a in self.repo.list_allocations(job.job_id) if a.id == job.allocation_id), None)
        if allocation is None:
            raise DomainError("the active allocation record is missing")
        return allocation

    def _save(self, job, previous_state, progress, changes, record, receipt, at, issue=None):
        updated = ExecutionProgress.model_validate(
            progress.model_dump()
            | changes
            | {
                "revision": progress.revision + 1,
                "updated_at": at,
            }
        )
        action = receipt.action
        if isinstance(record, DeliveryAttempt):
            action = f"{record.outcome.kind.value}_{'succeeded' if record.outcome.succeeded else 'failed'}"
        elif isinstance(record, IssueReview):
            action = "issue_resumed" if record.issue_addressed else "issue_reviewed"
        event = ExecutionChanged(
            request_id=job.request_id,
            job_id=job.job_id,
            record_id=record.id,
            action=action,
            previous_state=previous_state.value,
            resulting_state=job.state.value,
            created_at=at,
            created_by=receipt.actor_id,
        )
        tx = self.repo.transaction().update(
            job, ["state"], where={"state": previous_state, "allocation_id": job.allocation_id}
        )
        if progress.revision == 0:
            tx.create(updated)
        else:
            tx.update(
                updated,
                [f for f in ExecutionProgress.model_fields if f != "job_id"],
                where={"revision": progress.revision},
            )
        tx.create(record).create(receipt).create(event)
        if issue is not None:
            tx.create(issue)
        try:
            tx.commit()
        except ConflictError:
            replay = self._replay(receipt)
            if replay is not None:
                return replay
            raise
        return record

    def record_attempt(self, job_id, by, command):
        self._role(by, DeliveryStaff)
        receipt = self._receipt(job_id, by, "attempt", command)
        replay = self._replay(receipt)
        if replay is not None:
            return replay
        job = self.repo.get_job(job_id)
        allocation, progress = self._allocation(job, by), self.progress(job_id)
        at, outcome = self._now(progress), command.outcome
        earliest = max(job.created_at, allocation.created_at, progress.updated_at or job.created_at)
        if not earliest <= outcome.occurred_at <= at:
            raise DomainError("attempt time must follow allocation and prior updates, without being in the future")
        if progress.open_issue_id:
            raise DomainError("office staff must address the blocking issue before another attempt")
        if outcome.kind == AttemptKind.DELIVERY and progress.custodian_id != by:
            raise DomainError("the current driver must hold the parcels before delivery")
        if outcome.kind == AttemptKind.PICKUP and progress.collection_window:
            start, end = progress.collection_window
            if not start <= outcome.occurred_at <= end:
                raise DomainError("collection is outside the authorised reattempt window")
            if not self.system.roster.is_available(by, progress.collection_window):
                raise DomainError("driver is unavailable for the revised collection window")
        if outcome.proof:
            outcome.proof.check_conditions(
                signature_required=self.signature_required, photo_required=self.photo_required
            )
        previous_state = job.state
        successor = (
            job.current_state.record_pickup(succeeded=outcome.succeeded)
            if outcome.kind == AttemptKind.PICKUP
            else job.current_state.record_delivery(succeeded=outcome.succeeded)
        )
        record = DeliveryAttempt(
            id=command.submission_id,
            job_id=job_id,
            delivery_staff_id=by,
            allocation_id=allocation.id,
            recorded_at=at,
            outcome=outcome,
        )
        issue = None if outcome.succeeded else record.make_issue(command.submission_id)
        custodian = progress.custodian_id
        if outcome.succeeded:
            custodian = by if outcome.kind == AttemptKind.PICKUP else None
        job.enter(successor)
        return self._save(
            job,
            previous_state,
            progress,
            {
                "open_issue_id": issue.id if issue else None,
                "custodian_id": custodian,
                "last_occurred_at": outcome.occurred_at,
                "collection_window": None,
            },
            record,
            receipt,
            at,
            issue,
        )

    def review_issue(self, job_id, by, command):
        self._role(by, StaffMember)
        receipt = self._receipt(job_id, by, "review", command)
        replay = self._replay(receipt)
        if replay is not None:
            return replay
        job, progress = self.repo.get_job(job_id), self.progress(job_id)
        if job.state != JobStateName.ON_HOLD or progress.open_issue_id != command.issue_id:
            raise DomainError("this is not the current blocking issue")
        at = self._now(progress)
        record = IssueReview(
            id=command.submission_id,
            job_id=job_id,
            reviewed_by=by,
            reviewed_at=at,
            **command.model_dump(exclude={"submission_id"}),
        )
        if record.parcels_with_driver and (not progress.custodian_id or progress.custodian_id != job.delivery_staff_id):
            raise DomainError("the current driver does not hold the parcels")
        previous_state = job.state
        if record.issue_addressed:
            if not job.delivery_staff_id or not job.allocation_id:
                raise DomainError("an active allocation is required before resumption")
            self._allocation(job, job.delivery_staff_id)
            if not record.parcels_with_driver and not self.system.roster.is_available(
                job.delivery_staff_id, record.collection_window
            ):
                raise DomainError("the driver is unavailable for the revised collection window")
            job.enter(job.current_state.resume(issue_addressed=True, parcels_with_driver=record.parcels_with_driver))
        return self._save(
            job,
            previous_state,
            progress,
            {
                "open_issue_id": None if record.issue_addressed else progress.open_issue_id,
                "custodian_id": progress.custodian_id if record.parcels_with_driver else None,
                "collection_window": record.collection_window,
            },
            record,
            receipt,
            at,
        )

    def transfer_custody(self, job_id, by, command):
        self._role(by, StaffMember)
        receipt = self._receipt(job_id, by, "transfer", command)
        replay = self._replay(receipt)
        if replay is not None:
            return replay
        job, progress = self.repo.get_job(job_id), self.progress(job_id)
        if job.state not in (JobStateName.IN_TRANSIT, JobStateName.ON_HOLD):
            raise DomainError("only collected parcels can be transferred")
        if progress.custodian_id != command.from_driver_id or progress.custodian_id == job.delivery_staff_id:
            raise DomainError("the custodian must match and differ from the allocated receiving driver")
        if not job.delivery_staff_id:
            raise DomainError("a receiving driver must be allocated")
        allocation, at = self._allocation(job, job.delivery_staff_id), self._now(progress)
        if not max(allocation.created_at, progress.updated_at or job.created_at) <= command.occurred_at <= at:
            raise DomainError("transfer time must follow allocation and earlier updates, without being in the future")
        record = CustodyTransfer(
            id=command.submission_id,
            job_id=job_id,
            recorded_by=by,
            recorded_at=at,
            to_driver_id=job.delivery_staff_id,
            **command.model_dump(exclude={"submission_id"}),
        )
        return self._save(
            job,
            job.state,
            progress,
            {
                "custodian_id": job.delivery_staff_id,
                "last_occurred_at": command.occurred_at,
            },
            record,
            receipt,
            at,
        )
