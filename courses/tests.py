"""End-to-end coverage of the Ldrbrd permission and data model."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from courses.models import App, AppData, Course, Enrollment

User = get_user_model()


class LdrbrdTestCase(TestCase):
    """Shared fixture: one instructor, one student, one course."""

    def setUp(self):
        # No passwords anywhere: Canvas is the only real login, and the tests
        # use force_login().  Skipping the hasher keeps the suite quick.
        self.admin = User.objects.create_superuser("root", "root@x.test", password=None)
        self.instructor = User.objects.create_user("prof", "prof@x.test")
        self.instructor.is_staff = True
        self.instructor.save()
        self.student = User.objects.create_user("ada", "ada@x.test")
        self.other = User.objects.create_user("bob", "bob@x.test")

        self.course = Course.objects.create(owner=self.instructor, name="CS 101")

    def enrol(self, user, course=None):
        Enrollment.objects.get_or_create(course=course or self.course, user=user)

    def make_app(self, owner=None, name="Score Pusher", approved=False):
        app = App.objects.create(
            course=self.course, owner=owner or self.student, name=name
        )
        if approved:
            app.approve(self.instructor)
        return app

    def api(self, method, path, user=None, body=None):
        if user:
            self.client.force_login(user)
        else:
            self.client.logout()
        kwargs = {}
        if body is not None:
            kwargs["data"] = json.dumps(body)
            kwargs["content_type"] = "application/json"
        return getattr(self.client, method)(f"/api{path}", **kwargs)


class ModelTests(LdrbrdTestCase):
    def test_slugs_are_generated_and_scoped(self):
        self.assertEqual(self.course.slug, "cs-101")
        twin = Course.objects.create(owner=self.instructor, name="CS 101")
        self.assertEqual(twin.slug, "cs-101-2")
        # A different instructor may reuse the slug.
        other_prof = User.objects.create_user("prof2", "p2@x.test", "pw")
        mine = Course.objects.create(owner=other_prof, name="CS 101")
        self.assertEqual(mine.slug, "cs-101")

    def test_apps_get_a_unique_secret_key(self):
        a, b = self.make_app(name="One"), self.make_app(name="Two")
        self.assertTrue(a.secret_key)
        self.assertNotEqual(a.secret_key, b.secret_key)
        self.assertGreaterEqual(len(a.secret_key), 32)

    def test_rotate_secret_changes_the_key(self):
        app = self.make_app()
        old = app.secret_key
        new = app.rotate_secret()
        self.assertNotEqual(old, new)
        self.assertEqual(App.objects.get(pk=app.pk).secret_key, new)

    def test_data_url_matches_the_documented_shape(self):
        app = self.make_app()
        self.assertTrue(app.data_url.endswith("/prof/cs-101/score-pusher"))


class CanvasOidcTests(LdrbrdTestCase):
    def test_discovery_shim_describes_canvas(self):
        with override_settings(CANVAS_BASE_URL="https://school.instructure.com"):
            response = self.client.get("/.well-known/canvas-openid-configuration")
        self.assertEqual(response.status_code, 200)
        config = response.json()
        self.assertEqual(config["issuer"], "https://school.instructure.com")
        self.assertEqual(
            config["authorization_endpoint"],
            "https://school.instructure.com/login/oauth2/auth",
        )
        self.assertEqual(
            config["token_endpoint"],
            "https://school.instructure.com/login/oauth2/token",
        )
        self.assertEqual(
            config["userinfo_endpoint"],
            "https://school.instructure.com/api/v1/users/self",
        )
        # Canvas wants credentials in the body, not a Basic header.
        self.assertEqual(
            config["token_endpoint_auth_methods_supported"], ["client_secret_post"]
        )

    def test_overrides_win_over_the_generated_document(self):
        with override_settings(
            CANVAS_OPENID_CONFIG_OVERRIDES={"token_endpoint": "https://proxy/token"}
        ):
            config = self.client.get(
                "/.well-known/canvas-openid-configuration"
            ).json()
        self.assertEqual(config["token_endpoint"], "https://proxy/token")

    def test_provider_is_configured_for_canvas(self):
        """The fork-specific settings must actually reach the provider app."""
        from allauth.socialaccount.adapter import get_adapter

        provider = get_adapter().get_provider(
            None, "openid_connect", client_id=None
        )
        self.assertEqual(provider.app.settings["uid_field"], "id")
        self.assertEqual(
            provider.app.settings["token_auth_method"], "client_secret_post"
        )
        self.assertIn(
            "/.well-known/canvas-openid-configuration",
            provider.app.settings["server_url"],
        )

    def test_canvas_login_route_exists(self):
        url = reverse("openid_connect_login", kwargs={"provider_id": "canvas"})
        self.assertEqual(url, "/accounts/oidc/canvas/login/")


class CourseApiTests(LdrbrdTestCase):
    def test_only_staff_can_create_a_course(self):
        response = self.api("post", "/courses", self.student, {"name": "Rogue"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Course.objects.filter(name="Rogue").exists())

    def test_staff_can_create_a_course_and_gets_a_join_link(self):
        response = self.api(
            "post", "/courses", self.instructor, {"name": "Physics 200"}
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["slug"], "physics-200")
        self.assertIn("/join/", body["join_url"])

    def test_join_link_is_hidden_from_students(self):
        self.enrol(self.student)
        body = self.api("get", f"/courses/{self.course.pk}", self.student).json()
        self.assertIsNone(body["join_url"])
        # ...but the instructor sees it.
        body = self.api("get", f"/courses/{self.course.pk}", self.instructor).json()
        self.assertIn("/join/", body["join_url"])

    def test_students_join_with_the_token(self):
        response = self.api(
            "post",
            "/courses/join",
            self.student,
            {"join_token": self.course.join_token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Enrollment.objects.filter(course=self.course, user=self.student).exists()
        )

    def test_closed_course_refuses_new_students(self):
        self.course.is_open = False
        self.course.save()
        response = self.api(
            "post",
            "/courses/join",
            self.student,
            {"join_token": self.course.join_token},
        )
        self.assertEqual(response.status_code, 403)

    def test_unenrolled_student_cannot_see_a_course(self):
        self.assertEqual(
            self.api("get", f"/courses/{self.course.pk}", self.other).status_code, 404
        )

    def test_only_the_owner_can_edit_a_course(self):
        response = self.api(
            "patch", f"/courses/{self.course.pk}", self.other, {"name": "Hijacked"}
        )
        self.assertEqual(response.status_code, 403)
        self.course.refresh_from_db()
        self.assertEqual(self.course.name, "CS 101")

    def test_anonymous_requests_are_rejected(self):
        self.assertEqual(self.api("get", "/courses").status_code, 401)


class AppApiTests(LdrbrdTestCase):
    def test_app_creation_requires_enrolment(self):
        response = self.api(
            "post", f"/courses/{self.course.pk}/apps", self.other, {"name": "Nope"}
        )
        self.assertEqual(response.status_code, 403)

    def test_enrolled_student_creates_an_app_and_receives_the_secret(self):
        self.enrol(self.student)
        response = self.api(
            "post", f"/courses/{self.course.pk}/apps", self.student, {"name": "My Game"}
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["slug"], "my-game")
        self.assertFalse(body["approved"])
        self.assertTrue(body["secret_key"])

    def test_secret_key_is_not_exposed_to_other_students(self):
        app = self.make_app()
        self.enrol(self.other)
        listing = self.api(
            "get", f"/courses/{self.course.pk}/apps", self.other
        ).json()
        # A student only ever sees their own apps in the course listing.
        self.assertEqual(listing, [])
        self.assertEqual(
            self.api("get", f"/apps/{app.pk}", self.other).status_code, 404
        )

    def test_instructor_sees_every_app_in_the_course(self):
        self.make_app(owner=self.student, name="A")
        self.enrol(self.other)
        self.make_app(owner=self.other, name="B")
        listing = self.api(
            "get", f"/courses/{self.course.pk}/apps", self.instructor
        ).json()
        self.assertEqual(len(listing), 2)

    def test_only_the_instructor_approves(self):
        app = self.make_app()
        self.assertEqual(
            self.api("post", f"/apps/{app.pk}/approve", self.student).status_code, 403
        )
        response = self.api("post", f"/apps/{app.pk}/approve", self.instructor)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["approved"])
        app.refresh_from_db()
        self.assertEqual(app.approved_by, self.instructor)
        self.assertIsNotNone(app.approved_at)

    def test_unapprove_reverses_approval(self):
        app = self.make_app(approved=True)
        self.api("post", f"/apps/{app.pk}/unapprove", self.instructor)
        app.refresh_from_db()
        self.assertFalse(app.approved)
        self.assertIsNone(app.approved_at)


class DataPlaneTests(LdrbrdTestCase):
    def setUp(self):
        super().setUp()
        self.app = self.make_app(approved=True)
        self.path = "/prof/cs-101/score-pusher"

    def write(self, body, key=None, method="put", path=None):
        url = path or self.path
        if key is not None:
            url = f"{url}?secret_key={key}"
        return getattr(self.client, method)(
            url, data=json.dumps(body), content_type="application/json"
        )

    def test_write_then_public_read(self):
        scores = {"scores": [{"who": "ada", "points": 42}]}
        response = self.write(scores, key=self.app.secret_key)
        self.assertEqual(response.status_code, 200)

        self.client.logout()
        read = self.client.get(self.path)
        self.assertEqual(read.status_code, 200)
        body = read.json()
        self.assertEqual(body["data"], scores)
        self.assertEqual(body["instructor"], "prof")
        self.assertEqual(body["course"], "cs-101")
        self.assertEqual(body["app"], "score-pusher")

    def test_read_never_exposes_the_secret(self):
        self.write({"x": 1}, key=self.app.secret_key)
        body = self.client.get(self.path).json()
        self.assertNotIn("secret_key", body)
        self.assertNotIn(self.app.secret_key, json.dumps(body))

    def test_write_without_a_key_is_rejected(self):
        self.assertEqual(self.write({"x": 1}).status_code, 401)
        self.assertFalse(AppData.objects.filter(app=self.app).exists())

    def test_write_with_a_bogus_key_is_rejected(self):
        self.assertEqual(self.write({"x": 1}, key="not-a-real-key").status_code, 401)

    def test_another_apps_key_cannot_write_here(self):
        self.enrol(self.other)
        intruder = App.objects.create(
            course=self.course, owner=self.other, name="Intruder"
        )
        intruder.approve(self.instructor)
        response = self.write({"x": 1}, key=intruder.secret_key)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AppData.objects.filter(app=self.app).exists())

    def test_unapproved_app_cannot_write(self):
        pending = App.objects.create(
            course=self.course, owner=self.student, name="Pending"
        )
        response = self.write(
            {"x": 1}, key=pending.secret_key, path="/prof/cs-101/pending"
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("approval", response.json()["detail"].lower())

    def test_revoking_approval_stops_writes(self):
        self.write({"x": 1}, key=self.app.secret_key)
        self.app.unapprove()
        self.assertEqual(self.write({"x": 2}, key=self.app.secret_key).status_code, 403)
        # The already-published data stays readable.
        self.assertEqual(self.client.get(self.path).json()["data"], {"x": 1})

    def test_arbitrary_json_shapes_round_trip(self):
        for payload in ([1, 2, 3], "a string", 42, True, None, {"nested": {"a": [1]}}):
            with self.subTest(payload=payload):
                self.write(payload, key=self.app.secret_key)
                self.assertEqual(self.client.get(self.path).json()["data"], payload)

    def test_put_replaces_and_patch_merges(self):
        self.write({"a": 1, "b": 2}, key=self.app.secret_key)
        self.write({"b": 99}, key=self.app.secret_key, method="patch")
        self.assertEqual(
            self.client.get(self.path).json()["data"], {"a": 1, "b": 99}
        )
        self.write({"only": "this"}, key=self.app.secret_key, method="put")
        self.assertEqual(self.client.get(self.path).json()["data"], {"only": "this"})

    def test_patch_needs_an_object(self):
        self.write({"a": 1}, key=self.app.secret_key)
        response = self.write([1, 2], key=self.app.secret_key, method="patch")
        self.assertEqual(response.status_code, 400)

    def test_patch_refuses_to_merge_into_a_non_object(self):
        self.write([1, 2, 3], key=self.app.secret_key)
        response = self.write({"a": 1}, key=self.app.secret_key, method="patch")
        self.assertEqual(response.status_code, 409)

    def test_malformed_json_is_rejected(self):
        response = self.client.put(
            f"{self.path}?secret_key={self.app.secret_key}",
            data="{not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(LDRBRD_MAX_PAYLOAD_BYTES=50)
    def test_oversized_payload_is_rejected(self):
        response = self.write({"x": "y" * 500}, key=self.app.secret_key)
        self.assertEqual(response.status_code, 413)

    def test_reading_a_whole_course_is_public(self):
        self.write({"score": 1}, key=self.app.secret_key)
        self.enrol(self.other)
        second = App.objects.create(course=self.course, owner=self.other, name="Second")
        second.approve(self.instructor)

        self.client.logout()
        response = self.client.get("/prof/cs-101")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 2)
        by_slug = {row["app"]: row for row in body}
        self.assertEqual(by_slug["score-pusher"]["data"], {"score": 1})
        self.assertIsNone(by_slug["second"]["data"])

    def test_unknown_paths_404(self):
        self.assertEqual(self.client.get("/prof/cs-101/nope").status_code, 404)
        self.assertEqual(self.client.get("/nobody/nothing/nope").status_code, 404)
        self.assertEqual(self.client.get("/nobody/nothing").status_code, 404)

    def test_delete_clears_the_document(self):
        self.write({"a": 1}, key=self.app.secret_key)
        response = self.client.delete(f"{self.path}?secret_key={self.app.secret_key}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(self.path).json()["data"], {})

    def test_rotating_the_secret_invalidates_the_old_one(self):
        old = self.app.secret_key
        self.app.rotate_secret()
        self.assertEqual(self.write({"x": 1}, key=old).status_code, 401)
        self.assertEqual(self.write({"x": 1}, key=self.app.secret_key).status_code, 200)


class PromotionTests(LdrbrdTestCase):
    def test_superuser_promotes_a_student_to_staff(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("admin:auth_user_changelist"),
            {
                "action": "promote_to_staff",
                "_selected_action": [str(self.student.pk)],
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_staff)

    def test_promoted_user_can_then_create_a_course(self):
        self.student.is_staff = True
        self.student.save()
        response = self.api("post", "/courses", self.student, {"name": "Now I Teach"})
        self.assertEqual(response.status_code, 201)

    def test_demotion_never_strips_a_superuser(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse("admin:auth_user_changelist"),
            {
                "action": "demote_from_staff",
                "_selected_action": [str(self.admin.pk), str(self.instructor.pk)],
            },
            follow=True,
        )
        self.admin.refresh_from_db()
        self.instructor.refresh_from_db()
        self.assertTrue(self.admin.is_staff)
        self.assertFalse(self.instructor.is_staff)
