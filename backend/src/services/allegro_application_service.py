from asyncio import CancelledError, create_task
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from services.allegro_service import AllegroService
from services.allegro_state_store import AllegroStateStore
from services.allegro_stats.manager import AllegroMetricsManager
from services.domain.allegro import (
    AllegroAccount,
    AllegroApplyJob,
    AllegroMatchPreview,
    AllegroOrderPayment,
    AllegroOrderPayments,
    AllegroPageMatchCacheEntry,
    AllegroPageRequest,
    ApplyOutcome,
    MatchDecision,
)
from services.domain.job_base import JobStatus
from services.domain.match_result import MatchResult
from services.domain.metrics import AllegroMetrics
from services.domain.transaction import Transaction, TxTag
from services.domain.user_secrets import SecretType, UserSecretReadModel
from services.exceptions import (
    ExternalServiceFailed,
    InvalidMatchSelection,
    InvalidSecretId,
    MatchesNotComputed,
    SecretNotAccessible,
    TransactionNotFound,
)
from services.firefly_base_service import FireflyServiceError
from services.firefly_enrichment_service import FireflyEnrichmentService
from services.matching_cache.backend import MatchCacheUnavailable, Ticket
from services.tx_stats.models import MetricsState
from services.tx_stats.runner import MetricsProvider
from services.user_secrets_service import UserSecretsService


class AllegroApplicationService:
    def __init__(
        self,
        secrets_service: UserSecretsService,
        enrichment_service: FireflyEnrichmentService,
        metrics_provider: MetricsProvider[AllegroMetrics],
        allegro_service: AllegroService,
        state_store: AllegroStateStore,
        filter_desc_allegro: str,
    ) -> None:
        self.secrets_service = secrets_service
        self.enrichment_service = enrichment_service
        self.metrics_provider = metrics_provider
        self.allegro_service = allegro_service
        self.state_store = state_store
        self.filter_desc_allegro = filter_desc_allegro
        if self.state_store.metrics_manager is None:
            self.state_store.metrics_manager = AllegroMetricsManager(
                provider=self.metrics_provider
            )

    def get_allegro_secrets(self, user_id: UUID) -> list[UserSecretReadModel]:
        secrets = self.secrets_service.list_secrets(user_id=user_id)
        allegro_secrets = [
            secret for secret in secrets if secret.type == SecretType.ALLEGRO
        ]
        return allegro_secrets

    def fetch_allegro_data(
        self,
        *,
        user_id: UUID,
        secret_id: UUID,
        vault_session_id: str | None,
        page: AllegroPageRequest,
    ) -> AllegroOrderPayments:
        """Resolve the decrypted secret via UserSecretsService before external use."""
        try:
            secret = self.secrets_service.get_secret_for_internal_use(
                secret_id=secret_id,
                user_id=user_id,
                vault_session_id=vault_session_id,
            )
        except SecretNotAccessible as e:
            raise InvalidSecretId(
                f"Secret with id {secret_id} not found for user {user_id}"
            ) from e
        account = AllegroAccount(secret=secret.secret, id=secret.id)
        try:
            data = self.allegro_service.fetch(
                account=account,
                limit=page.limit,
                offset=page.offset,
            )
            return data
        except Exception as e:
            raise ExternalServiceFailed(
                f"Failed to fetch allegro data for secret {secret_id}"
            ) from e

    async def _match_page(
        self, *, payments: list[AllegroOrderPayment]
    ) -> tuple[list[MatchResult], list[AllegroOrderPayment]]:
        try:
            matches, unmatched = await self.enrichment_service.match_with_unmatched(
                filter_text=self.filter_desc_allegro,
                tag_done=TxTag.allegro_done,
                candidates=payments,
            )
            unmatched_payments = [cast(AllegroOrderPayment, item) for item in unmatched]
            return matches, unmatched_payments
        except FireflyServiceError as e:
            raise ExternalServiceFailed(str(e)) from e

    async def _cache_page(
        self,
        *,
        user_id: UUID,
        revision: str,
        ticket: Ticket,
        secret_id: UUID,
        page: AllegroPageRequest,
        payments: list[AllegroOrderPayment],
        matches: list[MatchResult],
    ) -> None:
        login = payments[0].allegro_login if payments else "unknown"
        stored = await self.state_store.put_page_matches(
            owner=user_id,
            revision=revision,
            ticket=ticket,
            secret_id=secret_id,
            entry=AllegroPageMatchCacheEntry(
                page=page,
                login=login,
                payments=payments,
                matches=matches,
            ),
        )
        if not stored:
            raise MatchesNotComputed("Matching inputs changed; preview again")

    async def preview_matches(
        self,
        *,
        user_id: UUID,
        secret_id: UUID,
        vault_session_id: str | None,
        page: AllegroPageRequest | None = None,
    ) -> AllegroMatchPreview:
        page_request = page or AllegroPageRequest()
        revision = self._matching_revision(user_id=user_id, secret_id=secret_id)
        ticket = await self.state_store.match_store.begin(
            owner=user_id, secret_id=secret_id, revision=revision
        )
        payments = self.fetch_allegro_data(
            user_id=user_id,
            secret_id=secret_id,
            vault_session_id=vault_session_id,
            page=page_request,
        )

        matches, unmatched_payments = await self._match_page(payments=payments.payments)
        if self._matching_revision(user_id=user_id, secret_id=secret_id) != revision:
            raise MatchesNotComputed("Secret changed; preview again")
        await self._cache_page(
            user_id=user_id,
            revision=revision,
            ticket=ticket,
            secret_id=secret_id,
            page=page_request,
            payments=payments.payments,
            matches=matches,
        )

        login = payments.payments[0].allegro_login if payments.payments else "unknown"
        not_matched = len([r for r in matches if not r.matches])
        with_one_match = len([r for r in matches if len(r.matches) == 1])
        with_many_matches = len([r for r in matches if len(r.matches) > 1])

        return AllegroMatchPreview(
            login=login,
            payments_fetched=len(payments.payments),
            transactions_found=len(matches),
            transactions_not_matched=not_matched,
            transactions_with_one_match=with_one_match,
            transactions_with_many_matches=with_many_matches,
            fetch_seconds=0.0,  # TODO: measure time taken for matching
            content=matches,
            unmatched_payments=unmatched_payments,
        )

    async def start_apply_job(
        self,
        *,
        user_id: UUID,
        secret_id: UUID,
        decisions: list[MatchDecision],
    ) -> AllegroApplyJob:
        revision = self._matching_revision(user_id=user_id, secret_id=secret_id)
        matches = await self.state_store.get_all_matches(
            owner=user_id, secret_id=secret_id, revision=revision, consume=True
        )
        if not matches:
            raise MatchesNotComputed()

        job = self.state_store.job_manager.create(
            secret_id=secret_id,
            total=len(decisions),
        )

        create_task(
            self._run_apply_job(
                job=job,
                decisions=decisions,
                matches=matches,
            )
        )

        return job

    def _index_matches(self, match: MatchResult) -> dict[str, AllegroOrderPayment]:
        index = {
            cast(AllegroOrderPayment, m).external_short_id: cast(AllegroOrderPayment, m)
            for m in match.matches
        }
        return index

    async def _run_apply_job(
        self,
        *,
        job: AllegroApplyJob,
        decisions: list[MatchDecision],
        matches: list[MatchResult],
    ):
        job.status = JobStatus.RUNNING
        final_status = JobStatus.DONE

        index = {int(cast(Transaction, m.tx).id): m for m in matches}

        try:
            for decision in decisions:
                tx_id = decision.transaction_id
                try:
                    match_result = index.get(tx_id)
                    if not match_result:
                        raise TransactionNotFound(f"Transaction id {tx_id} not found")
                    result_matches = self._index_matches(match_result)
                    payment = result_matches.get(decision.payment_id)
                    if not payment:
                        raise InvalidMatchSelection(
                            f"Payment id {decision.payment_id} not found for transaction id {tx_id}"  # shortID
                        )
                    tx = cast(Transaction, match_result.tx)
                    await self.enrichment_service.apply_match(tx=tx, evidence=payment)
                    job.applied += 1
                    job.results.append(
                        ApplyOutcome(transaction_id=tx_id, status="success")
                    )

                except Exception as e:
                    job.failed += 1
                    job.results.append(
                        ApplyOutcome(
                            transaction_id=tx_id, status="failed", reason=str(e)
                        )
                    )

        except CancelledError:
            final_status = JobStatus.FAILED
            raise
        finally:
            try:
                await self.state_store.match_store.backend.invalidate_all()
            except MatchCacheUnavailable:
                final_status = JobStatus.FAILED
                job.results.append(
                    ApplyOutcome(
                        transaction_id=-1,
                        status="failed",
                        reason="Matching cache unavailable; preview again",
                    )
                )
            job.finished_at = datetime.now(UTC)
            job.status = final_status

    async def start_auto_apply_single_matches(
        self,
        *,
        user_id: UUID,
        secret_id: UUID,
        limit: int | None = None,
    ) -> AllegroApplyJob:
        revision = self._matching_revision(user_id=user_id, secret_id=secret_id)
        matches = await self.state_store.get_all_matches(
            owner=user_id, secret_id=secret_id, revision=revision
        )
        if not matches:
            raise MatchesNotComputed()
        single = [m for m in matches if len(m.matches) == 1]

        if limit:
            single = single[:limit]

        decisions = [
            MatchDecision(
                transaction_id=int(cast(Transaction, m.tx).id),
                payment_id=cast(AllegroOrderPayment, m.matches[0]).external_short_id,
            )
            for m in single
        ]

        return await self.start_apply_job(
            user_id=user_id,
            secret_id=secret_id,
            decisions=decisions,
        )

    def get_apply_job(self, *, user_id: UUID, job_id: UUID) -> AllegroApplyJob | None:
        job = self.state_store.job_manager.get(job_id)
        if job is None:
            return None
        try:
            self._matching_revision(user_id=user_id, secret_id=job.secret_id)
        except InvalidSecretId:
            return None
        return job

    def _matching_revision(self, *, user_id: UUID, secret_id: UUID) -> str:
        try:
            return self.secrets_service.matching_revision(
                secret_id=secret_id, user_id=user_id
            )
        except SecretNotAccessible:
            raise InvalidSecretId("Secret not found") from None

    async def clear_cached_page(
        self, *, user_id: UUID, secret_id: UUID, page: AllegroPageRequest
    ) -> bool:
        revision = self._matching_revision(user_id=user_id, secret_id=secret_id)
        return await self.state_store.invalidate_page(
            owner=user_id, secret_id=secret_id, revision=revision, page=page
        )

    async def clear_cached_secret(self, *, user_id: UUID, secret_id: UUID) -> bool:
        revision = self._matching_revision(user_id=user_id, secret_id=secret_id)
        return await self.state_store.invalidate_secret(
            owner=user_id, secret_id=secret_id, revision=revision
        )

    # --------------------------------------------------
    # METRICS V2
    # --------------------------------------------------

    def _ensure_metrics_manager(self) -> AllegroMetricsManager:
        if self.state_store.metrics_manager is None:
            self.state_store.metrics_manager = AllegroMetricsManager(
                provider=self.metrics_provider
            )
        return self.state_store.metrics_manager

    async def get_metrics_state(self) -> MetricsState[AllegroMetrics]:
        manager = self._ensure_metrics_manager()
        return await manager.ensure_current()

    async def refresh_metrics_state(self) -> MetricsState[AllegroMetrics]:
        manager = self._ensure_metrics_manager()
        return await manager.refresh()
