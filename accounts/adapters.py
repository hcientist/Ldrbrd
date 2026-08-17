"""allauth adapters that translate Canvas's user payload into a Django user.

Canvas's /api/v1/users/self is not an OIDC userinfo endpoint -- it returns
Canvas's own shape::

    {"id": 1234, "name": "Ada Lovelace", "sortable_name": "Lovelace, Ada",
     "short_name": "Ada", "login_id": "ada@example.edu",
     "primary_email": "ada@example.edu", "avatar_url": "..."}

None of "sub", "preferred_username", "given_name" or "family_name" are
present, so allauth's stock field extraction comes up empty.  The adapters
below fill in the gaps.
"""

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.utils.text import slugify


def canvas_payload(sociallogin) -> dict:
    """The raw Canvas user dict out of whatever allauth stashed."""
    extra = sociallogin.account.extra_data or {}
    if isinstance(extra.get("userinfo"), dict):
        return extra["userinfo"]
    if isinstance(extra.get("id_token"), dict):
        return extra["id_token"]
    return extra


def split_name(full_name: str) -> tuple[str, str]:
    """Best-effort first/last split of a Canvas display name."""
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


class LdrbrdAccountAdapter(DefaultAccountAdapter):
    """Canvas is the only way in, so local signup stays shut."""

    def is_open_for_signup(self, request) -> bool:
        return False


class CanvasSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin) -> bool:
        """Anyone who can authenticate with Canvas may register with Ldrbrd."""
        return True

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        canvas = canvas_payload(sociallogin)

        if not user.get_full_name():
            first, last = split_name(canvas.get("name", ""))
            user.first_name = user.first_name or first
            user.last_name = user.last_name or last

        if not user.email:
            email = canvas.get("primary_email") or canvas.get("email") or ""
            # login_id is an email address on most Canvas instances.
            if not email and "@" in str(canvas.get("login_id", "")):
                email = canvas["login_id"]
            user.email = email

        if not user.username:
            user.username = self.canvas_username(canvas)

        return user

    def canvas_username(self, canvas: dict) -> str:
        """A URL-safe username -- it becomes the instructor segment of a data URL."""
        login_id = str(canvas.get("login_id") or "")
        candidates = [
            login_id.split("@")[0] if login_id else "",
            canvas.get("short_name") or "",
            canvas.get("name") or "",
            f"canvas-{canvas['id']}" if canvas.get("id") is not None else "",
        ]
        for candidate in candidates:
            slug = slugify(candidate)[:140]
            if slug:
                return slug
        return "canvas-user"
