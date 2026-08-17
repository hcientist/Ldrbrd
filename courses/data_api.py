"""The public data plane: /<instructor>/<course>/<appname>.

Reads are world readable and need no credentials at all.  Writes need the
app's secret_key in the querystring, and only work once the instructor has
approved the app.

This lives on its own NinjaAPI instance because it is a machine-to-machine
surface: no session, and therefore no CSRF.
"""

import json
from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError
from ninja.security import APIKeyQuery

from courses.models import App, AppData, Course

SECRET_KEY_PARAM = "secret_key"


class AppSecretKey(APIKeyQuery):
    """Authenticates a write as a particular App via ?secret_key=..."""

    param_name = SECRET_KEY_PARAM

    def authenticate(self, request: HttpRequest, key: str | None):
        if not key:
            return None
        app = (
            App.objects.select_related("course", "course__owner", "owner")
            .filter(secret_key=key)
            .first()
        )
        if app is None:
            return None
        # Stash it so the view does not have to look it up a second time.
        request.ldrbrd_app = app
        return app


app_secret_key = AppSecretKey()


class DataOut(Schema):
    instructor: str
    course: str
    app: str
    approved: bool
    updated_at: str | None = None
    data: Any = None


class WriteResult(Schema):
    instructor: str
    course: str
    app: str
    updated_at: str
    data: Any = None


def locate_app(instructor: str, course: str, app: str) -> App:
    return get_object_or_404(
        App.objects.select_related("course", "course__owner", "owner"),
        course__owner__username=instructor,
        course__slug=course,
        slug=app,
    )


def read_json_body(request: HttpRequest):
    """Parse the request body as arbitrary JSON.

    Deliberately not a Schema: an app may store any JSON value, including a
    bare list, string or number, which a pydantic model cannot express.
    """
    body = request.body
    if len(body) > settings.LDRBRD_MAX_PAYLOAD_BYTES:
        raise HttpError(
            413,
            f"Payload exceeds {settings.LDRBRD_MAX_PAYLOAD_BYTES} bytes.",
        )
    if not body:
        raise HttpError(400, "Request body is empty; send a JSON document.")
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HttpError(400, f"Request body is not valid JSON: {exc}") from exc


def authorised_app(request: HttpRequest, instructor: str, course: str, app: str) -> App:
    """The app the secret key unlocked, checked against the path and approved."""
    authed = getattr(request, "ldrbrd_app", None) or request.auth
    target = locate_app(instructor, course, app)
    if authed is None or authed.pk != target.pk:
        raise HttpError(403, "That secret_key does not belong to this app.")
    if not target.approved:
        raise HttpError(
            403,
            "This app is awaiting instructor approval; writes are not enabled yet.",
        )
    return target


def data_response(app: App, record: AppData | None) -> dict:
    return {
        "instructor": app.course.owner.username,
        "course": app.course.slug,
        "app": app.slug,
        "approved": app.approved,
        "updated_at": record.updated_at.isoformat() if record else None,
        "data": record.payload if record else None,
    }


def write_response(app: App, record: AppData) -> dict:
    return {
        "instructor": app.course.owner.username,
        "course": app.course.slug,
        "app": app.slug,
        "updated_at": record.updated_at.isoformat(),
        "data": record.payload,
    }


def register(api: NinjaAPI) -> None:
    """Attach the data-plane routes to ``api``."""

    @api.get(
        "/{str:instructor}/{str:course}/{str:app}",
        auth=None,
        response=DataOut,
        url_name="app_data_read",
        summary="Read an app's JSON document (public, no key required)",
    )
    def read_data(request, instructor: str, course: str, app: str):
        target = locate_app(instructor, course, app)
        record = AppData.objects.filter(app=target).first()
        return data_response(target, record)

    def do_replace(request, instructor: str, course: str, app: str) -> dict:
        target = authorised_app(request, instructor, course, app)
        payload = read_json_body(request)
        record, _ = AppData.objects.update_or_create(
            app=target, defaults={"payload": payload}
        )
        return write_response(target, record)

    @api.put(
        "/{str:instructor}/{str:course}/{str:app}",
        auth=app_secret_key,
        response=WriteResult,
        url_name="app_data_write",
        summary="Replace an app's JSON document (needs ?secret_key=)",
    )
    def replace_data(request, instructor: str, course: str, app: str):
        return do_replace(request, instructor, course, app)

    # POST behaves like PUT so that clients stuck with POST-only HTTP still work.
    @api.post(
        "/{str:instructor}/{str:course}/{str:app}",
        auth=app_secret_key,
        response=WriteResult,
        url_name="app_data_post",
        summary="Replace an app's JSON document (alias for PUT)",
    )
    def post_data(request, instructor: str, course: str, app: str):
        return do_replace(request, instructor, course, app)

    @api.patch(
        "/{str:instructor}/{str:course}/{str:app}",
        auth=app_secret_key,
        response=WriteResult,
        url_name="app_data_merge",
        summary="Shallow-merge into an app's JSON object (needs ?secret_key=)",
    )
    def merge_data(request, instructor: str, course: str, app: str):
        target = authorised_app(request, instructor, course, app)
        payload = read_json_body(request)
        if not isinstance(payload, dict):
            raise HttpError(400, "PATCH needs a JSON object; use PUT to replace.")
        record, _ = AppData.objects.get_or_create(app=target)
        if not isinstance(record.payload, dict):
            raise HttpError(
                409,
                "Stored document is not a JSON object, so it cannot be merged into. "
                "Use PUT to replace it.",
            )
        merged = dict(record.payload)
        merged.update(payload)
        record.payload = merged
        record.save(update_fields=["payload", "updated_at"])
        return write_response(target, record)

    @api.delete(
        "/{str:instructor}/{str:course}/{str:app}",
        auth=app_secret_key,
        response=WriteResult,
        url_name="app_data_clear",
        summary="Reset an app's JSON document to {} (needs ?secret_key=)",
    )
    def clear_data(request, instructor: str, course: str, app: str):
        target = authorised_app(request, instructor, course, app)
        record, _ = AppData.objects.update_or_create(app=target, defaults={"payload": {}})
        return write_response(target, record)

    @api.get(
        "/{str:instructor}/{str:course}",
        auth=None,
        response=list[DataOut],
        url_name="course_data_read",
        summary="Read every app's document in a course (public)",
    )
    def read_course(request, instructor: str, course: str):
        found = get_object_or_404(
            Course, owner__username=instructor, slug=course
        )
        apps = found.apps.select_related("course", "course__owner", "owner")
        records = {
            record.app_id: record
            for record in AppData.objects.filter(app__in=apps)
        }
        return [data_response(a, records.get(a.pk)) for a in apps]
