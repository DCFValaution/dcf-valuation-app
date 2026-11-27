"""The counter, with Firestore replaced by a fake that records requests.

What these tests are really checking is the one property the whole design
rests on: THE +1 AND THE MARKER GO IN ONE COMMIT. If they could be split,
a client could create markers on its own and never be counted - and the
Firestore rules that refuse exactly that (see firebase/rules.test.mjs)
only help if this module actually sends them together.

The offline suite cannot reach the network (see conftest.py), so the fake
is not an optimisation here; it is the only way these can run at all.

Run with:  python -m pytest test_usage.py -v
"""
from datetime import datetime, timezone

import pytest

import usage as U

NOON = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
TOMORROW = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)

UID = "uid-alice"
TOKEN = "id-token-abc"
APP_CHECK = "app-check-xyz"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession:
    """Records every call and answers from a queue."""

    def __init__(self):
        self.gets = []
        self.posts = []
        self.deletes = []
        self.get_responses = []
        self.post_responses = []
        self.delete_responses = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.gets.append({"url": url, "headers": headers, "params": params})
        return self.get_responses.pop(0) if self.get_responses \
            else FakeResponse(404)

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append({"url": url, "headers": headers, "body": json})
        return self.post_responses.pop(0) if self.post_responses \
            else FakeResponse(200)

    def delete(self, url, headers=None, timeout=None):
        self.deletes.append({"url": url, "headers": headers})
        return self.delete_responses.pop(0) if self.delete_responses \
            else FakeResponse(200)


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def store(session):
    return U.UsageStore("intrinsic-test", session=session)


def doc(fields):
    """A Firestore REST document, in its type-tagged wire format."""
    wrapped = {}
    for key, value in fields.items():
        if isinstance(value, int):
            wrapped[key] = {"integerValue": str(value)}
        else:
            wrapped[key] = {"stringValue": value}
    return {"fields": wrapped}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class TestReading:
    def test_a_caller_with_no_row_has_used_nothing(self, store, session):
        session.get_responses = [FakeResponse(404)]
        assert store.read(UID, id_token=TOKEN) == U.Usage()

    def test_parses_the_counter(self, store, session):
        session.get_responses = [FakeResponse(200, doc(
            {"lifetime": 2, "day": "2026-11-25", "day_count": 1}))]
        got = store.read(UID, id_token=TOKEN)
        assert got.lifetime == 2
        assert got.day == "2026-11-25"
        assert got.day_count == 1

    def test_sends_the_id_token_as_the_credential(self, store, session):
        store.read(UID, id_token=TOKEN, app_check_token=APP_CHECK)
        headers = session.gets[0]["headers"]
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        assert headers["X-Firebase-AppCheck"] == APP_CHECK

    def test_omits_the_app_check_header_when_there_is_no_token(
            self, store, session):
        """An old client that has not shipped App Check yet still works,
        because enforcement is a separate switch flipped later."""
        store.read(UID, id_token=TOKEN)
        assert "X-Firebase-AppCheck" not in session.gets[0]["headers"]

    def test_an_error_is_raised_rather_than_read_as_zero(self, store, session):
        """Reading a 500 as "used nothing" would hand everybody an
        unlimited allowance the moment Firestore had a bad minute."""
        session.get_responses = [FakeResponse(500)]
        with pytest.raises(U.UsageUnavailable):
            store.read(UID, id_token=TOKEN)

    def test_a_network_failure_is_raised_as_unavailable(self, store, session):
        import requests

        def boom(*args, **kwargs):
            raise requests.ConnectionError("no route")

        session.get = boom
        with pytest.raises(U.UsageUnavailable):
            store.read(UID, id_token=TOKEN)


class TestWhichWindowIsRead:
    def test_lifetime_ignores_the_day(self):
        got = U.Usage(lifetime=3, day="2026-11-24", day_count=1)
        assert got.used("lifetime", key="lifetime") == 3

    def test_a_stale_day_reads_as_zero_rather_than_being_reset(self):
        """Nothing has to run at midnight, and two requests cannot race
        to reset it."""
        got = U.Usage(lifetime=9, day="2026-11-24", day_count=7)
        assert got.used("day", key="2026-11-25") == 0

    def test_todays_count_is_read_when_the_day_matches(self):
        got = U.Usage(lifetime=9, day="2026-11-25", day_count=7)
        assert got.used("day", key="2026-11-25") == 7


# ---------------------------------------------------------------------------
# Counting - the part the design rests on
# ---------------------------------------------------------------------------

class TestTheCountAndTheMarkerAreOneCommit:
    def test_both_writes_go_in_a_single_request(self, store, session):
        session.get_responses = [FakeResponse(404)]
        counted, after = store.count(UID, "AAPL", id_token=TOKEN, now=NOON)

        assert counted is True
        assert len(session.posts) == 1, "two requests would not be atomic"
        writes = session.posts[0]["body"]["writes"]
        assert len(writes) == 2

    def test_the_counter_names_the_marker_it_is_for(self, store, session):
        """Which is what lets the rule on the counter require that the
        marker exists after the commit."""
        session.get_responses = [FakeResponse(404)]
        store.count(UID, "AAPL", id_token=TOKEN, now=NOON)
        counter = session.posts[0]["body"]["writes"][0]["update"]["fields"]
        assert counter["last_marker"]["stringValue"] == "2026-11-25__AAPL"

    def test_the_marker_is_created_only_if_it_does_not_exist(
            self, store, session):
        """The dedupe, enforced by the database rather than by a read
        followed by a hopeful write."""
        session.get_responses = [FakeResponse(404)]
        store.count(UID, "AAPL", id_token=TOKEN, now=NOON)
        marker = session.posts[0]["body"]["writes"][1]
        assert marker["currentDocument"] == {"exists": False}
        assert marker["update"]["name"].endswith(
            f"usage/{UID}/valued/2026-11-25__AAPL")

    def test_the_first_valuation_starts_both_counters_at_one(
            self, store, session):
        session.get_responses = [FakeResponse(404)]
        _, after = store.count(UID, "AAPL", id_token=TOKEN, now=NOON)
        assert after.lifetime == 1
        assert after.day_count == 1
        assert after.day == "2026-11-25"

    def test_a_second_company_the_same_day_moves_both(self, store, session):
        session.get_responses = [FakeResponse(200, doc(
            {"lifetime": 1, "day": "2026-11-25", "day_count": 1}))]
        _, after = store.count(UID, "MSFT", id_token=TOKEN, now=NOON)
        assert after.lifetime == 2
        assert after.day_count == 2

    def test_a_new_day_restarts_the_day_counter_but_not_the_lifetime(
            self, store, session):
        session.get_responses = [FakeResponse(200, doc(
            {"lifetime": 5, "day": "2026-11-25", "day_count": 4}))]
        _, after = store.count(UID, "MSFT", id_token=TOKEN, now=TOMORROW)
        assert after.lifetime == 6
        assert after.day_count == 1
        assert after.day == "2026-11-26"

    def test_the_ticker_is_normalised_so_case_cannot_buy_a_valuation(
            self, store, session):
        session.get_responses = [FakeResponse(404)]
        store.count(UID, " aapl ", id_token=TOKEN, now=NOON)
        marker = session.posts[0]["body"]["writes"][1]
        assert marker["update"]["name"].endswith("2026-11-25__AAPL")
        assert marker["update"]["fields"]["ticker"]["stringValue"] == "AAPL"


class TestWhatDoesNotCount:
    def test_a_rejected_precondition_means_already_counted_today(
            self, store, session):
        """Firestore answers 409 when the marker is already there. That is
        the whole of "reopening the same company, dragging a slider,
        switching to the relative view and exporting are free"."""
        session.get_responses = [FakeResponse(200, doc(
            {"lifetime": 2, "day": "2026-11-25", "day_count": 2}))]
        session.post_responses = [FakeResponse(409, text="ALREADY_EXISTS")]

        counted, after = store.count(UID, "AAPL", id_token=TOKEN, now=NOON)
        assert counted is False
        assert after.lifetime == 2, "the counter must not move"
        assert after.day_count == 2

    def test_a_412_is_treated_the_same_way(self, store, session):
        session.get_responses = [FakeResponse(404)]
        session.post_responses = [FakeResponse(412)]
        counted, after = store.count(UID, "AAPL", id_token=TOKEN, now=NOON)
        assert counted is False
        assert after.lifetime == 0

    def test_any_other_failure_is_raised_rather_than_swallowed(
            self, store, session):
        """Silently not counting would be an unlimited allowance that
        looks exactly like a working one."""
        session.get_responses = [FakeResponse(404)]
        session.post_responses = [FakeResponse(403, text="PERMISSION_DENIED")]
        with pytest.raises(U.UsageUnavailable):
            store.count(UID, "AAPL", id_token=TOKEN, now=NOON)

    def test_a_caller_supplied_reading_saves_the_round_trip(
            self, store, session):
        """The gate has already read the counter to decide; reading it
        again would double every valuation's Firestore cost."""
        store.count(UID, "AAPL", id_token=TOKEN, now=NOON,
                    current=U.Usage(lifetime=1, day="2026-11-25",
                                    day_count=1))
        assert session.gets == []


# ---------------------------------------------------------------------------
# Deletion, which Play policy and our own /delete page both promise
# ---------------------------------------------------------------------------

class TestDeletion:
    def test_removes_the_markers_as_well_as_the_counter(self, store, session):
        session.get_responses = [FakeResponse(200, {"documents": [
            {"name": f"projects/p/databases/(default)/documents/usage/{UID}"
                     "/valued/2026-11-25__AAPL"},
            {"name": f"projects/p/databases/(default)/documents/usage/{UID}"
                     "/valued/2026-11-25__MSFT"},
        ]})]
        store.delete(UID, id_token=TOKEN)

        deleted = [call["url"] for call in session.deletes]
        assert any("2026-11-25__AAPL" in url for url in deleted)
        assert any("2026-11-25__MSFT" in url for url in deleted)
        assert any(url.endswith(f"usage/{UID}") for url in deleted)

    def test_a_subcollection_is_not_deleted_by_deleting_its_parent(
            self, store, session):
        """Firestore leaves a subcollection behind when the parent
        document goes, so orphaned markers would be exactly the data the
        deletion page promises is gone."""
        session.get_responses = [FakeResponse(200, {"documents": [
            {"name": "projects/p/databases/(default)/documents/usage/u/"
                     "valued/2026-11-25__AAPL"}]})]
        store.delete(UID, id_token=TOKEN)
        assert len(session.deletes) == 2

    def test_follows_the_pages_rather_than_deleting_the_first_three_hundred(
            self, store, session):
        session.get_responses = [
            FakeResponse(200, {
                "documents": [{"name": "a/b/usage/u/valued/one"}],
                "nextPageToken": "more",
            }),
            FakeResponse(200, {
                "documents": [{"name": "a/b/usage/u/valued/two"}],
            }),
        ]
        store.delete(UID, id_token=TOKEN)
        assert session.gets[1]["params"]["pageToken"] == "more"
        assert len(session.deletes) == 3

    def test_an_absent_counter_is_not_an_error(self, store, session):
        """Deleting an account that never valued anything must not fail
        the deletion of everything else."""
        session.get_responses = [FakeResponse(404)]
        session.delete_responses = [FakeResponse(404)]
        assert store.delete(UID, id_token=TOKEN) == 0
