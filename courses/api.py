"""The management API: courses, enrolment, apps, approval.

Session-authenticated -- students and instructors are already signed in through
Canvas.  The public read/write data endpoints live in courses/data_api.py.

Fields that must not leak (a course's join link, an app's secret key) are
nullable on the schema and populated by the ``*_payload`` helpers only for the
people entitled to see them.  Doing it in the helper rather than by swapping
response schemas keeps one shape per endpoint, which is what django-ninja
actually serialises against.
"""

from datetime import datetime

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from ninja import Router, Schema
from ninja.errors import HttpError
from ninja.security import django_auth

from courses import leaderboard
from courses.models import App, Course, Enrollment, unique_slug

router = Router(auth=django_auth)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class UserOut(Schema):
    id: int
    username: str
    full_name: str = ""
    email: str = ""
    is_staff: bool
    is_superuser: bool

    @staticmethod
    def resolve_full_name(obj) -> str:
        return obj.get_full_name()


class CourseIn(Schema):
    name: str
    slug: str | None = None
    description: str = ""
    is_open: bool = True


class CoursePatch(Schema):
    name: str | None = None
    description: str | None = None
    is_open: bool | None = None


class CourseOut(Schema):
    id: int
    name: str
    slug: str
    description: str
    is_open: bool
    instructor: str
    data_url: str
    app_count: int
    created_at: datetime
    # Populated only for the course's instructor (or a superuser).
    join_url: str | None = None


class JoinIn(Schema):
    join_token: str


class AppIn(Schema):
    name: str
    slug: str | None = None


class LeaderboardRow(Schema):
    rank: int
    name: str
    slug: str
    owner: str
    course: str
    instructor: str
    approved: bool
    data_url: str
    reads: int
    writes: int
    activity: int
    last_active_at: datetime | None = None


class AppOut(Schema):
    id: int
    name: str
    slug: str
    owner: str
    course_id: int
    course_slug: str
    instructor: str
    approved: bool
    approved_at: datetime | None = None
    data_url: str
    created_at: datetime
    # Populated only for the app's own student, the instructor, or a superuser.
    secret_key: str | None = None


# --------------------------------------------------------------------------
# Serialisation helpers
# --------------------------------------------------------------------------


def course_payload(course: Course, user) -> dict:
    administers = course.is_administered_by(user)
    return {
        "id": course.id,
        "name": course.name,
        "slug": course.slug,
        "description": course.description,
        "is_open": course.is_open,
        "instructor": course.owner.username,
        "data_url": course.data_url,
        "app_count": getattr(course, "app_count", None) or course.apps.count(),
        "created_at": course.created_at,
        "join_url": course.join_url if administers else None,
    }


def app_payload(app: App, user) -> dict:
    return {
        "id": app.id,
        "name": app.name,
        "slug": app.slug,
        "owner": app.owner.username,
        "course_id": app.course_id,
        "course_slug": app.course.slug,
        "instructor": app.course.owner.username,
        "approved": app.approved,
        "approved_at": app.approved_at,
        "data_url": app.data_url,
        "created_at": app.created_at,
        "secret_key": app.secret_key if app.is_administered_by(user) else None,
    }


# --------------------------------------------------------------------------
# Access helpers
# --------------------------------------------------------------------------


def visible_courses(user):
    """Courses the user teaches or is enrolled in."""
    return (
        Course.objects.filter(Q(owner=user) | Q(enrollments__user=user))
        .select_related("owner")
        .annotate(app_count=Count("apps", distinct=True))
        .distinct()
    )


def get_visible_course(request, course_id: int) -> Course:
    course = get_object_or_404(Course.objects.select_related("owner"), pk=course_id)
    if course.is_administered_by(request.user):
        return course
    if Enrollment.objects.filter(course=course, user=request.user).exists():
        return course
    raise HttpError(404, "No such course.")


def get_owned_course(request, course_id: int) -> Course:
    course = get_object_or_404(Course.objects.select_related("owner"), pk=course_id)
    if not course.is_administered_by(request.user):
        raise HttpError(403, "Only the course's instructor can do that.")
    return course


def get_administered_app(request, app_id: int) -> App:
    app = get_object_or_404(
        App.objects.select_related("course", "course__owner", "owner"), pk=app_id
    )
    if not app.is_administered_by(request.user):
        raise HttpError(404, "No such app.")
    return app


# --------------------------------------------------------------------------
# Me
# --------------------------------------------------------------------------


@router.get("/me", response=UserOut)
def me(request):
    return request.user


# --------------------------------------------------------------------------
# Leaderboard -- the JSON twin of the /top page
# --------------------------------------------------------------------------


@router.get(
    "/top",
    auth=None,
    response=list[LeaderboardRow],
    summary="App usage leaderboard (public)",
)
def top(request, course: str | None = None, sort: str = leaderboard.DEFAULT_SORT):
    """Usage stats for every registered app, ranked.

    Public, like the data it summarises.  ``course`` takes the same
    ``instructor/slug`` reference the data plane uses; ``sort`` is one of
    total, reads, writes or recent.
    """
    if sort not in leaderboard.SORTS:
        raise HttpError(
            400, f"sort must be one of: {', '.join(sorted(leaderboard.SORTS))}"
        )
    found = leaderboard.resolve_course(course)
    if course and found is None:
        raise HttpError(404, f"No course matches '{course}'.")
    return [
        {
            "rank": row["rank"],
            "name": row["name"],
            "slug": row["slug"],
            "owner": row["owner"],
            "course": row["course_ref"],
            "instructor": row["instructor"],
            "approved": row["approved"],
            "data_url": row["data_url"],
            "reads": row["reads"],
            "writes": row["writes"],
            "activity": row["activity"],
            "last_active_at": row["last_active_at"],
        }
        for row in leaderboard.rows(found, sort)
    ]


# --------------------------------------------------------------------------
# Courses
# --------------------------------------------------------------------------


@router.get("/courses", response=list[CourseOut])
def list_courses(request):
    return [course_payload(c, request.user) for c in visible_courses(request.user)]


@router.post("/courses", response={201: CourseOut})
def create_course(request, payload: CourseIn):
    if not request.user.is_staff:
        raise HttpError(403, "Only staff users can create courses.")
    course = Course(
        owner=request.user,
        name=payload.name,
        description=payload.description,
        is_open=payload.is_open,
    )
    if payload.slug:
        course.slug = unique_slug(
            Course, payload.slug, fallback="course", owner=request.user
        )
    course.save()
    return 201, course_payload(course, request.user)


@router.get("/courses/{int:course_id}", response=CourseOut)
def get_course(request, course_id: int):
    return course_payload(get_visible_course(request, course_id), request.user)


@router.patch("/courses/{int:course_id}", response=CourseOut)
def update_course(request, course_id: int, payload: CoursePatch):
    course = get_owned_course(request, course_id)
    for field, value in payload.dict(exclude_unset=True).items():
        setattr(course, field, value)
    course.save()
    return course_payload(course, request.user)


@router.delete("/courses/{int:course_id}", response={204: None})
def delete_course(request, course_id: int):
    get_owned_course(request, course_id).delete()
    return 204, None


@router.post("/courses/join", response=CourseOut)
def join_course(request, payload: JoinIn):
    """Redeem a join token.  The API twin of the /join/<token> page."""
    course = get_object_or_404(
        Course.objects.select_related("owner"), join_token=payload.join_token
    )
    enrolled = Enrollment.objects.filter(course=course, user=request.user).exists()
    if not course.is_open and not enrolled:
        raise HttpError(403, "This course is no longer accepting new students.")
    Enrollment.objects.get_or_create(course=course, user=request.user)
    return course_payload(course, request.user)


@router.get("/courses/{int:course_id}/roster", response=list[UserOut])
def course_roster(request, course_id: int):
    course = get_owned_course(request, course_id)
    return [e.user for e in course.enrollments.select_related("user")]


# --------------------------------------------------------------------------
# Apps
# --------------------------------------------------------------------------


@router.get("/apps", response=list[AppOut])
def list_my_apps(request):
    apps = request.user.apps.select_related("course", "course__owner", "owner")
    return [app_payload(a, request.user) for a in apps]


@router.get("/courses/{int:course_id}/apps", response=list[AppOut])
def list_course_apps(request, course_id: int):
    """Instructors see every app in the course; students see only their own."""
    course = get_visible_course(request, course_id)
    apps = course.apps.select_related("owner", "course", "course__owner")
    if not course.is_administered_by(request.user):
        apps = apps.filter(owner=request.user)
    return [app_payload(a, request.user) for a in apps]


@router.post("/courses/{int:course_id}/apps", response={201: AppOut})
def create_app(request, course_id: int, payload: AppIn):
    course = get_object_or_404(Course.objects.select_related("owner"), pk=course_id)
    enrolled = Enrollment.objects.filter(course=course, user=request.user).exists()
    if not enrolled and not course.is_administered_by(request.user):
        raise HttpError(403, "Join the course with your instructor's link first.")

    app = App(course=course, owner=request.user, name=payload.name)
    if payload.slug:
        app.slug = unique_slug(App, payload.slug, fallback="app", course=course)
    app.save()
    return 201, app_payload(app, request.user)


@router.get("/apps/{int:app_id}", response=AppOut)
def get_app(request, app_id: int):
    return app_payload(get_administered_app(request, app_id), request.user)


@router.post("/apps/{int:app_id}/approve", response=AppOut)
def approve_app(request, app_id: int):
    app = get_administered_app(request, app_id)
    if not app.course.is_administered_by(request.user):
        raise HttpError(403, "Only the course's instructor can approve apps.")
    app.approve(request.user)
    return app_payload(app, request.user)


@router.post("/apps/{int:app_id}/unapprove", response=AppOut)
def unapprove_app(request, app_id: int):
    app = get_administered_app(request, app_id)
    if not app.course.is_administered_by(request.user):
        raise HttpError(403, "Only the course's instructor can revoke approval.")
    app.unapprove()
    return app_payload(app, request.user)


@router.post("/apps/{int:app_id}/rotate-secret", response=AppOut)
def rotate_app_secret(request, app_id: int):
    app = get_administered_app(request, app_id)
    app.rotate_secret()
    return app_payload(app, request.user)


@router.delete("/apps/{int:app_id}", response={204: None})
def delete_app(request, app_id: int):
    get_administered_app(request, app_id).delete()
    return 204, None
