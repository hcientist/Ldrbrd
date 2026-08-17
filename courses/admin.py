from django.contrib import admin
from django.utils import timezone

from courses.models import App, AppData, Course, Enrollment


class EnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 0
    autocomplete_fields = ("user",)


class AppInline(admin.TabularInline):
    model = App
    extra = 0
    fields = ("name", "slug", "owner", "approved", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("owner",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "is_open", "app_count", "created_at")
    list_filter = ("is_open", "created_at")
    search_fields = ("name", "slug", "owner__username")
    readonly_fields = ("join_token", "join_url", "data_url", "created_at", "updated_at")
    autocomplete_fields = ("owner",)
    inlines = (EnrollmentInline, AppInline)

    @admin.display(description="apps")
    def app_count(self, obj) -> int:
        return obj.apps.count()


@admin.register(App)
class AppAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "course", "owner", "approved", "created_at")
    list_filter = ("approved", "course", "created_at")
    search_fields = ("name", "slug", "owner__username", "course__name")
    readonly_fields = (
        "secret_key",
        "data_url",
        "approved_at",
        "approved_by",
        "created_at",
        "updated_at",
    )
    autocomplete_fields = ("course", "owner")
    actions = ("approve_apps", "unapprove_apps", "rotate_secrets")

    @admin.action(description="Approve selected apps")
    def approve_apps(self, request, queryset):
        updated = queryset.update(
            approved=True, approved_at=timezone.now(), approved_by=request.user
        )
        self.message_user(request, f"Approved {updated} app(s).")

    @admin.action(description="Revoke approval for selected apps")
    def unapprove_apps(self, request, queryset):
        updated = queryset.update(approved=False, approved_at=None, approved_by=None)
        self.message_user(request, f"Revoked approval for {updated} app(s).")

    @admin.action(description="Rotate secret keys for selected apps")
    def rotate_secrets(self, request, queryset):
        count = 0
        for app in queryset:
            app.rotate_secret()
            count += 1
        self.message_user(request, f"Rotated {count} secret key(s).")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("user", "course", "joined_at")
    list_filter = ("course", "joined_at")
    search_fields = ("user__username", "course__name")
    autocomplete_fields = ("user", "course")


@admin.register(AppData)
class AppDataAdmin(admin.ModelAdmin):
    list_display = ("app", "updated_at")
    search_fields = ("app__name", "app__slug", "app__course__name")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("app",)
