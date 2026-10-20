from swe.api.identity import CurrentUser, Driver, Staff
from swe.app.delivery_execution import DeliveryExecution
from swe.domain.job.execution import DeliveryAttempt, IssueReview
from swe.domain.job.execution_state import CustodyTransfer, RecordAttempt, ReviewIssue, TransferCustody


def install_execution_routes(app):
    execution = DeliveryExecution(app.state.dms)
    app.state.execution = execution

    @app.get("/tracking/jobs")
    def driver_jobs(user: Driver):
        return execution.driver_jobs(user.user_id)

    @app.get("/tracking/jobs/{job_id}")
    def track(job_id: str, user: CurrentUser):
        return execution.track(job_id, user.user_id)

    @app.post("/jobs/{job_id}/attempts")
    def record_attempt(job_id: str, body: RecordAttempt, user: Driver) -> DeliveryAttempt:
        return execution.record_attempt(job_id, user.user_id, body)

    @app.post("/jobs/{job_id}/issue-reviews")
    def review_issue(job_id: str, body: ReviewIssue, user: Staff) -> IssueReview:
        return execution.review_issue(job_id, user.user_id, body)

    @app.post("/jobs/{job_id}/custody-transfers")
    def transfer(job_id: str, body: TransferCustody, user: Staff) -> CustodyTransfer:
        return execution.transfer_custody(job_id, user.user_id, body)
