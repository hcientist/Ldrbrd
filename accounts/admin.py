"""Admin surface for promoting Canvas-registered users to staff.

Ldrbrd uses Django's own ``is_staff`` flag as the instructor bit: staff users
are the ones allowed to create courses.  Promotion is a superuser action.
"""

from allauth.socialaccount.models import SocialAccount
from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

User = get_user_model()

admin.site.site_header = "Ldrbrd administration"
admin.site.site_title = "Ldrbrd admin"
admin.site.index_title = "Ldrbrd"


class CanvasAccountInline(admin.TabularInline):
    """Shows which Canvas identity a user signed in with."""

    model = SocialAccount
    extra = 0
    fields = ("provider", "uid", "last_login", "date_joined")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


class LdrbrdUserAdmin(DjangoUserAdmin):
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "is_superuser",
        "course_count",
        "date_joined",
    )
    list_filter = ("is_staff", "is_superuser", "is_active", "date_joined")
    actions = ("promote_to_staff", "demote_from_staff")
    inlines = (CanvasAccountInline,)

    @admin.display(description="courses")
    def course_count(self, obj) -> int:
        return obj.courses.count()

    def _require_superuser(self, request) -> bool:
        if request.user.is_superuser:
            return True
        self.message_user(
            request,
            "Only superusers can change staff status.",
            level=messages.ERROR,
        )
        return False

    @admin.action(description="Promote to staff (may create courses)")
    def promote_to_staff(self, request, queryset):
        if not self._require_superuser(request):
            return
        updated = queryset.exclude(is_staff=True).update(is_staff=True)
        self.message_user(request, f"Promoted {updated} user(s) to staff.")

    @admin.action(description="Demote from staff")
    def demote_from_staff(self, request, queryset):
        if not self._require_superuser(request):
            return
        # Never let an admin lock every superuser out of the admin site.
        updated = queryset.exclude(is_superuser=True).filter(is_staff=True).update(
            is_staff=False
        )
        self.message_user(request, f"Demoted {updated} user(s).")


admin.site.unregister(User)
admin.site.register(User, LdrbrdUserAdmin)
