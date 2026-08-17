"""Canvas identity -> Django user mapping.

These lock down the bits that depend on the hcientist django-allauth fork and
on Canvas's non-standard userinfo shape.
"""

from allauth.socialaccount.adapter import get_adapter
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.db.utils import OperationalError
from django.test import TestCase

from accounts.adapters import CanvasSocialAccountAdapter, split_name

User = get_user_model()

# What Canvas's /api/v1/users/self actually returns -- note: no "sub",
# no "preferred_username", no "given_name"/"family_name".
CANVAS_USERINFO = {
    "id": 987654,
    "name": "Ada Lovelace",
    "created_at": "2024-08-01T12:00:00Z",
    "sortable_name": "Lovelace, Ada",
    "short_name": "Ada",
    "login_id": "lovelace@school.edu",
    "primary_email": "ada@school.edu",
    "avatar_url": "https://school.instructure.com/images/thumbnails/1",
}


def make_sociallogin(userinfo=None):
    account = SocialAccount(
        provider="canvas", extra_data={"userinfo": userinfo or CANVAS_USERINFO}
    )
    return SocialLogin(user=User(), account=account)


class SplitNameTests(TestCase):
    def test_splits_on_the_first_space(self):
        self.assertEqual(split_name("Ada Lovelace"), ("Ada", "Lovelace"))
        self.assertEqual(split_name("Ada King Lovelace"), ("Ada", "King Lovelace"))
        self.assertEqual(split_name("Prince"), ("Prince", ""))
        self.assertEqual(split_name(""), ("", ""))
        self.assertEqual(split_name("   "), ("", ""))


class PopulateUserTests(TestCase):
    def setUp(self):
        self.adapter = CanvasSocialAccountAdapter()

    def populate(self, userinfo=None):
        sociallogin = make_sociallogin(userinfo)
        return self.adapter.populate_user(None, sociallogin, {})

    def test_maps_canvas_fields_onto_the_user(self):
        user = self.populate()
        self.assertEqual(user.first_name, "Ada")
        self.assertEqual(user.last_name, "Lovelace")
        self.assertEqual(user.email, "ada@school.edu")
        self.assertEqual(user.username, "lovelace")

    def test_falls_back_to_login_id_when_there_is_no_email(self):
        info = dict(CANVAS_USERINFO)
        del info["primary_email"]
        self.assertEqual(self.populate(info).email, "lovelace@school.edu")

    def test_username_survives_a_missing_login_id(self):
        info = dict(CANVAS_USERINFO)
        del info["login_id"]
        self.assertEqual(self.populate(info).username, "ada")

    def test_username_falls_back_to_the_canvas_id(self):
        user = self.populate({"id": 42})
        self.assertEqual(user.username, "canvas-42")

    def test_username_is_url_safe(self):
        """The username becomes a path segment in every data URL."""
        user = self.populate({"id": 7, "login_id": "Ada O'Brien-Smith@school.edu"})
        self.assertEqual(user.username, "ada-obrien-smith")
        self.assertNotIn("/", user.username)
        self.assertNotIn(" ", user.username)
        self.assertNotIn("@", user.username)

    def test_handles_a_sparse_payload_without_blowing_up(self):
        user = self.populate({"id": 1})
        self.assertEqual(user.username, "canvas-1")
        self.assertEqual(user.email, "")
        self.assertEqual(user.first_name, "")


class UidFieldTests(TestCase):
    """The fork's uid_field must actually be used to key the account."""

    def test_uid_comes_from_canvas_id_not_sub(self):
        provider = get_adapter().get_provider(None, "openid_connect", client_id=None)
        uid = provider.extract_uid({"userinfo": CANVAS_USERINFO})
        self.assertEqual(uid, "987654")

    def test_extraction_does_not_require_a_sub_claim(self):
        provider = get_adapter().get_provider(None, "openid_connect", client_id=None)
        self.assertNotIn("sub", CANVAS_USERINFO)
        # Would raise KeyError on stock allauth, which hardcodes "sub".
        self.assertTrue(provider.extract_uid({"userinfo": CANVAS_USERINFO}))


class SignupPolicyTests(TestCase):
    def test_canvas_signup_is_open(self):
        """Anyone who can authenticate with Canvas may register."""
        adapter = CanvasSocialAccountAdapter()
        self.assertTrue(adapter.is_open_for_signup(None, make_sociallogin()))

    def test_local_signup_is_closed(self):
        from accounts.adapters import LdrbrdAccountAdapter

        self.assertFalse(LdrbrdAccountAdapter().is_open_for_signup(None))

    def test_new_users_are_not_staff(self):
        """Promotion has to be a deliberate admin action."""
        user = CanvasSocialAccountAdapter().populate_user(
            None, make_sociallogin(), {}
        )
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)


class HealthzTests(TestCase):
    """Backs the container HEALTHCHECK, which greps the body for status ok."""

    def test_healthz_is_public_and_reports_ok(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_body_matches_what_the_dockerfile_greps_for(self):
        # Dockerfile: curl -fsS .../healthz | grep -q '"status": "ok"'
        self.assertIn(b'"status": "ok"', self.client.get("/healthz").content)

    def test_healthz_is_never_cached(self):
        response = self.client.get("/healthz")
        self.assertIn("no-cache", response.headers.get("Cache-Control", ""))

    def test_healthz_reports_503_when_the_database_is_unreachable(self):
        from unittest.mock import patch

        with patch(
            "accounts.views.connection.ensure_connection",
            side_effect=OperationalError("no such table"),
        ):
            response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")
