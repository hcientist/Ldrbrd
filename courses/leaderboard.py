"""The /top leaderboard: which apps are getting used, ranked.

Shared by the HTML page (accounts/views.py) and the JSON endpoint so the two
can never drift apart.
"""

from django.db.models import BigIntegerField, F, Q, Value
from django.db.models.functions import Coalesce

from courses.models import App, Course

SORTS = {
    "total": "activity",
    "reads": "reads",
    "writes": "writes",
    "recent": "last_active_at",
}
DEFAULT_SORT = "total"


def resolve_course(course_ref: str | None) -> Course | None:
    """Look up the course filter.

    Accepts either the ``instructor/slug`` pair used throughout the data plane
    or a bare numeric id, so the dropdown and a hand-typed URL both work.
    """
    if not course_ref:
        return None
    ref = course_ref.strip().strip("/")
    if not ref:
        return None
    if "/" in ref:
        instructor, _, slug = ref.partition("/")
        return Course.objects.filter(
            owner__username=instructor, slug=slug
        ).select_related("owner").first()
    if ref.isdigit():
        return Course.objects.filter(pk=int(ref)).select_related("owner").first()
    # A bare slug is unambiguous only when one instructor uses it.
    matches = Course.objects.filter(slug=ref).select_related("owner")[:2]
    return matches[0] if len(matches) == 1 else None


def leaderboard(course: Course | None = None, sort: str = DEFAULT_SORT):
    """Apps ranked by usage, richest first.

    Apps that have never been touched still appear, with zeroes -- "registered
    but unused" is a fact the page should show, not hide.
    """
    zero = Value(0, output_field=BigIntegerField())
    apps = (
        App.objects.select_related("course", "course__owner", "owner")
        .annotate(
            reads=Coalesce("usage__read_count", zero),
            writes=Coalesce("usage__write_count", zero),
        )
        .annotate(activity=F("reads") + F("writes"))
    )
    if course is not None:
        apps = apps.filter(course=course)

    order = SORTS.get(sort, SORTS[DEFAULT_SORT])
    if order == "last_active_at":
        # Never-used apps sort last rather than jumbling in among the NULLs.
        apps = apps.order_by(
            F("usage__last_write_at").desc(nulls_last=True),
            F("usage__last_read_at").desc(nulls_last=True),
            "-activity",
        )
    else:
        apps = apps.order_by(f"-{order}", "-activity", "name")
    return apps


# Which measure the bars draw. A ranked bar chart is read as monotonic, so the
# bar has to encode whatever the ranking encodes -- otherwise rank 1 can show a
# shorter bar than rank 2. Ranking by recency is not a magnitude, so it keeps
# the full reads+writes stack.
BAR_MEASURE = {
    "total": "activity",
    "reads": "reads",
    "writes": "writes",
    "recent": "activity",
}


def rows(course: Course | None = None, sort: str = DEFAULT_SORT) -> list[dict]:
    """Leaderboard entries as plain dicts, with bar widths already worked out."""
    apps = list(leaderboard(course, sort))
    measure = BAR_MEASURE.get(sort, "activity")
    scale = max((getattr(a, measure) for a in apps), default=0)
    stacked = measure == "activity"

    out = []
    for rank, app in enumerate(apps, start=1):
        usage = getattr(app, "usage", None)
        share = (getattr(app, measure) / scale * 100) if scale else 0
        if stacked:
            bar_reads = (app.reads / scale * 100) if scale else 0
            bar_writes = (app.writes / scale * 100) if scale else 0
        else:
            # One measure: a single segment in that series' colour.
            bar_reads = share if measure == "reads" else 0
            bar_writes = share if measure == "writes" else 0
        out.append(
            {
                "rank": rank,
                "app": app,
                "name": app.name,
                "slug": app.slug,
                "owner": app.owner.username,
                "course_name": app.course.name,
                "course_ref": f"{app.course.owner.username}/{app.course.slug}",
                "instructor": app.course.owner.username,
                "approved": app.approved,
                "data_url": app.data_url,
                "reads": app.reads,
                "writes": app.writes,
                "activity": app.activity,
                "last_active_at": usage.last_active_at if usage else None,
                # Percentages of the leading app's bar, so the widest row fills
                # the track and the rest are read against it.
                "bar_total": share,
                "bar_reads": bar_reads,
                "bar_writes": bar_writes,
                # What the bar is actually showing, so the row can tell whether
                # it has any bar to draw at all.
                "bar_value": getattr(app, measure),
            }
        )
    return out


def totals(entries: list[dict]) -> dict:
    return {
        "apps": len(entries),
        "reads": sum(e["reads"] for e in entries),
        "writes": sum(e["writes"] for e in entries),
        "activity": sum(e["activity"] for e in entries),
        "active_apps": sum(1 for e in entries if e["activity"]),
    }


def course_choices():
    """Every course that has at least one registered app, for the filter."""
    return (
        Course.objects.filter(apps__isnull=False)
        .select_related("owner")
        .distinct()
        .order_by("owner__username", "name")
    )
