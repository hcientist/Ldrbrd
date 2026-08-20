"""URL configuration for Ldrbrd.

Two django-ninja APIs are mounted:

  * ``/api/``  -- the session-authenticated management API.  CSRF is enforced
                  because ninja's ``django_auth`` is a cookie-based scheme and
                  checks the token itself.
  * ``/``      -- the public data plane, /<instructor>/<course>/<appname>.
                  Authenticated by querystring secret key, so no CSRF applies.

The data plane sits at the root and matches any two- or three-segment path, so
it must be included *last*; everything above it wins the resolution race.
"""

from django.contrib import admin
from django.urls import include, path
from ninja import NinjaAPI

from accounts import views as account_views
from courses import data_api
from courses.api import router as courses_router

api = NinjaAPI(
    title="Ldrbrd API",
    version="1.0.0",
    description="Course, app and approval management. Sign in with Canvas first.",
    urls_namespace="ldrbrd-api",
)
api.add_router("", courses_router)

data = NinjaAPI(
    title="Ldrbrd data",
    version="1.0.0",
    description=(
        "Public JSON storage. Reads are open to the world; writes need the "
        "app's secret_key in the querystring."
    ),
    urls_namespace="ldrbrd-data",
    docs_url="/docs",
)
data_api.register(data)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", api.urls),
    path("accounts/", include("allauth.urls")),
    path(
        ".well-known/canvas-openid-configuration",
        account_views.canvas_openid_configuration,
        name="canvas-openid-configuration",
    ),
    path("healthz", account_views.healthz, name="healthz"),
    path("courses/new", account_views.course_create, name="course-create"),
    path("courses/<int:course_id>/", account_views.course_detail, name="course-detail"),
    path("courses/<int:course_id>/edit", account_views.course_edit, name="course-edit"),
    path("apps/<int:app_id>/approval", account_views.app_set_approval, name="app-set-approval"),
    path("join/<str:join_token>", account_views.join_course, name="join-course"),
    path("top", account_views.top, name="top"),
    path("", account_views.home, name="home"),
    # Keep last: matches /<instructor>/<course>[/<appname>].
    path("", data.urls),
]
