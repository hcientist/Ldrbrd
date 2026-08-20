"""Canvas OIDC discovery shim plus a couple of thin HTML pages."""

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import cache_control, never_cache

from courses import leaderboard as lb
from courses.models import Course, Enrollment


@cache_control(max_age=300, public=True)
def canvas_openid_configuration(request):
    """Serve the discovery document Canvas itself does not publish.

    Canvas exposes OAuth2 endpoints and a JWKS, but no
    /.well-known/openid-configuration.  The allauth OIDC adapter insists on
    fetching one, so Ldrbrd generates it from CANVAS_BASE_URL.  Anything an
    operator needs to change per-deployment can be layered on top through the
    fork's CANVAS_OPENID_CONFIG_OVERRIDES without touching this view.
    """
    canvas = settings.CANVAS_BASE_URL
    config = {
        "issuer": canvas,
        "authorization_endpoint": f"{canvas}/login/oauth2/auth",
        "token_endpoint": f"{canvas}/login/oauth2/token",
        "userinfo_endpoint": f"{canvas}/api/v1/users/self/profile",
        "jwks_uri": f"{canvas}/login/oauth2/jwks",
        "end_session_endpoint": f"{canvas}/login/oauth2/logout",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        # Canvas wants client_id/client_secret in the POST body.
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "claims_supported": ["id", "name", "short_name", "sortable_name", "login_id"],
        "scopes_supported": settings.CANVAS_OAUTH_SCOPE,
    }
    config.update(settings.CANVAS_OPENID_CONFIG_OVERRIDES)
    return JsonResponse(config)


@never_cache
def healthz(request):
    """Liveness probe for the container healthcheck.

    Touches the database, because a Ldrbrd that cannot reach its SQLite file
    is not healthy in any useful sense -- an HTTP 200 from a process that
    cannot serve a request would just paper over the failure.
    """
    try:
        connection.ensure_connection()
    except Exception:  # noqa: BLE001 - any DB failure is unhealthy
        return JsonResponse(
            {"status": "error", "database": "unavailable"}, status=503
        )
    return JsonResponse({"status": "ok"})


def canvas_login_url() -> str:
    """The provider-specific allauth login entry point for Canvas."""
    return reverse(
        "openid_connect_login", kwargs={"provider_id": settings.CANVAS_PROVIDER_ID}
    )


def home(request):
    context = {"canvas_login_url": canvas_login_url()}
    if request.user.is_authenticated:
        context["taught_courses"] = Course.objects.filter(owner=request.user)
        context["enrolled_courses"] = Course.objects.filter(
            enrollments__user=request.user
        )
        context["apps"] = request.user.apps.select_related("course", "course__owner")
    return render(request, "home.html", context)


def top(request):
    """Usage leaderboard for every registered app.

    Public, like the data it summarises.  ``?course=`` narrows to one course
    and ``?sort=`` picks the ranking; both round-trip through the querystring
    so a filtered view is a shareable link.
    """
    course_ref = request.GET.get("course", "")
    sort = request.GET.get("sort", lb.DEFAULT_SORT)
    if sort not in lb.SORTS:
        sort = lb.DEFAULT_SORT

    course = lb.resolve_course(course_ref)
    # A ?course= that matched nothing should say so, not silently show everything.
    unknown_course = bool(course_ref.strip()) and course is None

    entries = [] if unknown_course else lb.rows(course, sort)
    return render(
        request,
        "top.html",
        {
            "entries": entries,
            "totals": lb.totals(entries),
            "courses": lb.course_choices(),
            "course": course,
            "course_ref": course_ref,
            "unknown_course": unknown_course,
            "sort": sort,
            "bar_measure": lb.BAR_MEASURE.get(sort, "activity"),
            "sorts": [
                ("total", "Most activity"),
                ("reads", "Most reads"),
                ("writes", "Most writes"),
                ("recent", "Most recent"),
            ],
        },
    )


@login_required
def join_course(request, join_token):
    """Redeem an instructor's course link.

    A student who is not signed in gets bounced through Canvas first; the
    @login_required redirect brings them back here afterwards.
    """
    course = get_object_or_404(Course, join_token=join_token)
    already = Enrollment.objects.filter(course=course, user=request.user).exists()

    if request.method == "POST":
        if not course.is_open and not already:
            return render(
                request,
                "join.html",
                {"course": course, "closed": True, "enrolled": already},
                status=403,
            )
        Enrollment.objects.get_or_create(course=course, user=request.user)
        return redirect("home")

    return render(
        request,
        "join.html",
        {"course": course, "enrolled": already, "closed": not course.is_open},
    )
