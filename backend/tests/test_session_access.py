import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from google.api_core.exceptions import AlreadyExists

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for variable in (
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "AGORA_APP_ID",
    "AGORA_APP_CERTIFICATE",
    "AGORA_CUSTOMER_ID",
    "AGORA_CUSTOMER_SECRET",
    "PUBLIC_BASE_URL",
):
    os.environ.setdefault(variable, "test")

from app import firestore_db
from app.api import user as user_api
from app import account_deletion
from app.models.interview_profile import InterviewProfile
from app.orchestration.orchestrator import InterviewStage, resolve_focused_stage


class FocusedStageTests(unittest.TestCase):
    def setUp(self):
        self.profile = InterviewProfile(
            role="Software Engineer",
            skills=["Python", "communication"],
            system_design_topics=["Distributed systems", "Caching"],
            responsibilities=["Lead cross-functional teams"],
            candidate_highlights=[],
        )

    def test_system_design_skill_uses_system_design_stage(self):
        self.assertEqual(
            resolve_focused_stage("Distributed systems", self.profile),
            InterviewStage.SYSTEM_DESIGN,
        )

    def test_behavioral_skill_falls_back_to_technical_stage(self):
        self.assertEqual(
            resolve_focused_stage("Lead cross-functional teams", self.profile),
            InterviewStage.TECHNICAL,
        )
        self.assertEqual(
            resolve_focused_stage("communication", self.profile),
            InterviewStage.TECHNICAL,
        )


class SessionAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        firestore_db._session_owner_cache.clear()

    async def test_local_session_owner_is_allowed_without_firestore(self):
        firestore_db._session_owner_cache["roundtable-test"] = "owner-1"
        with patch("app.firestore_db.get_db", return_value=None):
            await firestore_db.require_session_owner("roundtable-test", "owner-1")

    async def test_local_session_rejects_a_different_user(self):
        firestore_db._session_owner_cache["roundtable-test"] = "owner-1"
        with patch("app.firestore_db.get_db", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                await firestore_db.require_session_owner("roundtable-test", "owner-2")

        self.assertEqual(raised.exception.status_code, 404)

    async def test_firestore_session_owner_is_checked(self):
        document = Mock(exists=True)
        document.to_dict.return_value = {"uid": "owner-1"}
        document_reference = Mock()
        document_reference.get.return_value = document
        collection = Mock()
        collection.document.return_value = document_reference
        db = Mock()
        db.collection.return_value = collection

        with patch("app.firestore_db.get_db", return_value=db):
            with self.assertRaises(HTTPException) as raised:
                await firestore_db.require_session_owner("roundtable-test", "owner-2")

        self.assertEqual(raised.exception.status_code, 404)


class FirestoreWaitlistTests(unittest.IsolatedAsyncioTestCase):
    async def test_waitlist_signup_creates_firestore_document(self):
        document_reference = Mock()
        collection = Mock()
        collection.document.return_value = document_reference
        db = Mock()
        db.collection.return_value = collection

        with patch("app.firestore_db.get_db", return_value=db):
            result = await firestore_db.save_waitlist_user("uid-1", "user@example.com", "User")

        self.assertFalse(result["already_joined"])
        document_reference.create.assert_called_once()

    async def test_duplicate_waitlist_signup_is_idempotent(self):
        document_reference = Mock()
        document_reference.create.side_effect = AlreadyExists("already exists")
        collection = Mock()
        collection.document.return_value = document_reference
        db = Mock()
        db.collection.return_value = collection

        with patch("app.firestore_db.get_db", return_value=db):
            result = await firestore_db.save_waitlist_user("uid-1", "user@example.com", "User")

        self.assertTrue(result["already_joined"])

    async def test_waitlist_signup_fails_if_firestore_is_unavailable(self):
        with patch("app.firestore_db.get_db", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                await firestore_db.save_waitlist_user("uid-1", "user@example.com", "User")

        self.assertEqual(raised.exception.status_code, 503)


class InterviewQuotaTests(unittest.IsolatedAsyncioTestCase):
    def make_quota_firestore(self, recent_sessions, previous_status):
        user_document = Mock(exists=True)
        user_document.to_dict.return_value = {
            "recent_sessions": recent_sessions,
            "last_channel": "previous-session",
        }
        user_reference = Mock()
        user_reference.get.return_value = user_document

        previous_session_document = Mock(exists=True)
        previous_session_document.to_dict.return_value = {"status": previous_status}
        previous_session_reference = Mock()
        previous_session_reference.get.return_value = previous_session_document
        new_session_reference = Mock()
        sessions = Mock()
        sessions.document.side_effect = lambda channel: (
            previous_session_reference
            if channel == "previous-session"
            else new_session_reference
        )

        users = Mock()
        users.document.return_value = user_reference
        db = Mock()
        db.collection.side_effect = lambda name: users if name == "users" else sessions
        transaction = Mock()
        db.transaction.return_value = transaction
        return db, user_reference, new_session_reference, transaction

    def test_two_session_rolling_limit(self):
        self.assertEqual(firestore_db.FREE_TIER_DAILY_LIMIT, 2)

        no_sessions = firestore_db._user_limits(0, False)
        one_session = firestore_db._user_limits(1, False)
        two_sessions = firestore_db._user_limits(2, False)

        self.assertEqual((no_sessions["remaining"], no_sessions["can_start"]), (2, True))
        self.assertEqual((one_session["remaining"], one_session["can_start"]), (1, True))
        self.assertEqual((two_sessions["remaining"], two_sessions["can_start"]), (0, False))

    def test_only_sessions_inside_the_24_hour_window_count(self):
        recent = firestore_db._recent_session_epochs(
            [90_000, 99_999, "invalid"],
            cutoff=95_000,
        )

        self.assertEqual(recent, [99_999])

    async def test_limits_fail_closed_when_firestore_is_unavailable(self):
        with patch("app.firestore_db.get_db", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                await firestore_db.get_user_rate_limits("uid-1")

        self.assertEqual(raised.exception.status_code, 503)

    async def test_session_start_fails_closed_when_firestore_is_unavailable(self):
        with patch("app.firestore_db.get_db", return_value=None):
            with self.assertRaises(HTTPException) as raised:
                await firestore_db.check_and_record_session(
                    "uid-1",
                    "roundtable-test",
                    "profile-1",
                )

        self.assertEqual(raised.exception.status_code, 503)

    async def test_third_focused_interview_is_rejected(self):
        now = 2_000_000
        db, _, _, _ = self.make_quota_firestore(
            [now - 1000, now - 60],
            previous_status="active",
        )
        with (
            patch("app.firestore_db.get_db", return_value=db),
            patch("app.firestore_db.time.time", return_value=now),
            patch("app.firestore_db.firestore.transactional", side_effect=lambda fn: fn),
        ):
            with self.assertRaises(HTTPException) as raised:
                await firestore_db.check_and_record_session(
                    "uid-1",
                    "new-session",
                    "profile-1",
                    mode="focused",
                )

        self.assertEqual(raised.exception.status_code, 429)

    async def test_failed_agent_start_retry_reuses_its_quota_slot(self):
        now = 2_000_000
        db, _, _, transaction = self.make_quota_firestore(
            [now - 1000, now - 60],
            previous_status="failed",
        )
        with (
            patch("app.firestore_db.get_db", return_value=db),
            patch("app.firestore_db.time.time", return_value=now),
            patch("app.firestore_db.firestore.transactional", side_effect=lambda fn: fn),
        ):
            await firestore_db.check_and_record_session(
                "uid-1",
                "retry-session",
                "profile-1",
                mode="focused",
            )

        updated_user = transaction.set.call_args_list[0].args[1]
        self.assertEqual(updated_user["recent_sessions"], [now - 1000, now])


class AccountDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_deletion_is_persisted_idempotently(self):
        queued_snapshot = Mock(exists=False)
        request_reference = Mock()
        request_reference.get.return_value = queued_snapshot
        requests = Mock()
        requests.document.return_value = request_reference
        transaction = Mock()
        db = Mock()
        db.collection.return_value = requests
        db.transaction.return_value = transaction

        with (
            patch("app.firestore_db.get_db", return_value=db),
            patch("app.firestore_db.firestore.transactional", side_effect=lambda function: function),
        ):
            created = await firestore_db.enqueue_account_deletion("uid-1")

        self.assertTrue(created)
        queued_data = transaction.set.call_args.args[1]
        self.assertEqual(queued_data["uid"], "uid-1")
        self.assertEqual(queued_data["status"], "pending")
        self.assertEqual(queued_data["attempts"], 0)
        self.assertEqual(queued_data["delete_by_epoch"] - queued_data["requested_at_epoch"], 48 * 60 * 60)

        queued_snapshot.exists = True
        queued_snapshot.to_dict.return_value = {"status": "pending"}
        with (
            patch("app.firestore_db.get_db", return_value=db),
            patch("app.firestore_db.firestore.transactional", side_effect=lambda function: function),
        ):
            created_again = await firestore_db.enqueue_account_deletion("uid-1")

        self.assertFalse(created_again)
        transaction.set.assert_called_once()

    async def test_deletion_removes_owned_firestore_data_but_honors_active_legal_hold(self):
        profile_to_keep = SimpleNamespace(id="profile-kept", reference=Mock())
        profile_to_delete = SimpleNamespace(id="profile-delete", reference=Mock())
        session_to_keep = SimpleNamespace(id="session-kept", reference=Mock())
        session_to_delete = SimpleNamespace(id="session-delete", reference=Mock())

        profiles = Mock()
        profiles.where.return_value.stream.return_value = [profile_to_keep, profile_to_delete]
        sessions = Mock()
        sessions.where.return_value.stream.return_value = [session_to_keep, session_to_delete]
        hold_reference = Mock()
        hold_snapshot = Mock(exists=True)
        hold_snapshot.to_dict.return_value = {
            "reason": "Required for a current legal claim",
            "expires_at_epoch": 2_000_100,
            "profile_ids": ["profile-kept"],
            "session_ids": ["session-kept"],
        }
        hold_reference.get.return_value = hold_snapshot
        users_reference = Mock()
        waitlist_reference = Mock()
        users = Mock()
        users.document.return_value = users_reference
        waitlist = Mock()
        waitlist.document.return_value = waitlist_reference
        legal_holds = Mock()
        legal_holds.document.return_value = hold_reference

        batch = Mock()
        db = Mock()
        db.collection.side_effect = {
            "profiles": profiles,
            "sessions": sessions,
            "legal_holds": legal_holds,
            "users": users,
            "waitlist": waitlist,
        }.__getitem__
        db.batch.return_value = batch

        with (
            patch("app.firestore_db.get_db", return_value=db),
            patch("app.firestore_db.time.time", return_value=2_000_000),
        ):
            profile_ids, session_ids = await firestore_db.delete_user_data("uid-1")

        self.assertEqual(profile_ids, ["profile-delete"])
        self.assertEqual(session_ids, ["session-delete"])
        deleted_references = [call.args[0] for call in batch.delete.call_args_list]
        self.assertNotIn(profile_to_keep.reference, deleted_references)
        self.assertNotIn(session_to_keep.reference, deleted_references)
        self.assertIn(profile_to_delete.reference, deleted_references)
        self.assertIn(session_to_delete.reference, deleted_references)
        self.assertIn(users_reference, deleted_references)
        self.assertIn(waitlist_reference, deleted_references)

    async def test_account_endpoint_queues_request_without_waiting_for_deletion(self):
        enqueue = AsyncMock(return_value=True)
        with patch.object(user_api, "enqueue_account_deletion", new=enqueue):
            response = await user_api.delete_account({"uid": "uid-1"})

        self.assertEqual(response["status"], "accepted")
        self.assertIn("within 48 hours", response["message"])
        enqueue.assert_awaited_once_with("uid-1")

    async def test_account_deletion_worker_completes_queued_request(self):
        with (
            patch.object(account_deletion, "get_due_account_deletions", new=AsyncMock(return_value=["uid-1"])),
            patch.object(account_deletion, "claim_account_deletion", new=AsyncMock(return_value=1)),
            patch.object(account_deletion, "_delete_account_data", new=AsyncMock()) as delete_data,
            patch.object(account_deletion, "complete_account_deletion", new=AsyncMock()) as complete,
            patch.object(account_deletion, "retry_account_deletion", new=AsyncMock()) as retry,
        ):
            await account_deletion.process_due_account_deletions()

        delete_data.assert_awaited_once_with("uid-1")
        complete.assert_awaited_once_with("uid-1")
        retry.assert_not_awaited()

    async def test_account_deletion_worker_requeues_failed_request(self):
        with (
            patch.object(account_deletion, "get_due_account_deletions", new=AsyncMock(return_value=["uid-1"])),
            patch.object(account_deletion, "claim_account_deletion", new=AsyncMock(return_value=3)),
            patch.object(
                account_deletion,
                "_delete_account_data",
                new=AsyncMock(side_effect=RuntimeError("provider unavailable")),
            ),
            patch.object(account_deletion, "complete_account_deletion", new=AsyncMock()) as complete,
            patch.object(account_deletion, "retry_account_deletion", new=AsyncMock()) as retry,
        ):
            await account_deletion.process_due_account_deletions()

        complete.assert_not_awaited()
        retry.assert_awaited_once_with("uid-1", 3, "RuntimeError")


if __name__ == "__main__":
    unittest.main()
