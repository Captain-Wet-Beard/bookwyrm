""" outlink data """
from typing import Optional, Iterable
from urllib.parse import urlparse

from django.core.exceptions import PermissionDenied
from django.db import models
from django.utils.translation import gettext_lazy as _

from bookwyrm import activitypub
from bookwyrm.utils.db import add_update_fields
from .activitypub_mixin import ActivitypubMixin
from .base_model import BookWyrmModel
from . import fields


class Link(ActivitypubMixin, BookWyrmModel):
    """a link to a website"""

    url = fields.URLField(max_length=255, activitypub_field="href")
    added_by = fields.ForeignKey(
        "User", on_delete=models.SET_NULL, null=True, activitypub_field="attributedTo"
    )
    domain = models.ForeignKey(
        "LinkDomain",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="links",
    )

    activity_serializer = activitypub.Link
    reverse_unfurl = True

    @property
    def name(self):
        """link name via the associated domain"""
        return self.domain.name

    def save(self, *args, update_fields: Optional[Iterable[str]] = None, **kwargs):
        """create a link"""
        # get or create the associated domain
        if not self.domain:
            domain = urlparse(self.url).hostname
            self.domain, _ = LinkDomain.objects.get_or_create(domain=domain)
            update_fields = add_update_fields(update_fields, "domain")

        # this is never broadcast, the owning model broadcasts an update
        if "broadcast" in kwargs:
            del kwargs["broadcast"]

        super().save(*args, update_fields=update_fields, **kwargs)


AvailabilityChoices = [
    ("free", _("Free")),
    ("purchase", _("Purchasable")),
    ("loan", _("Available for loan")),
]


class FileLink(Link):
    """a link to a file"""

    book = models.ForeignKey(
        "Book", on_delete=models.CASCADE, related_name="file_links", null=True
    )
    filetype = fields.CharField(max_length=50, activitypub_field="mediaType")
    availability = fields.CharField(
        max_length=100, choices=AvailabilityChoices, default="free"
    )


StatusChoices = [
    ("approved", _("Approved")),
    ("blocked", _("Blocked")),
    ("pending", _("Pending")),
]


class LinkDomain(BookWyrmModel):
    """List of domains used in links"""

    domain = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=50, choices=StatusChoices, default="pending")
    name = models.CharField(max_length=100)
    reported_by = models.ForeignKey(
        "User", blank=True, null=True, on_delete=models.SET_NULL
    )

    def raise_not_editable(self, viewer):
        if viewer.has_perm("bookwyrm.moderate_post"):
            return
        raise PermissionDenied()

    def save(self, *args, update_fields: Optional[Iterable[str]] = None, **kwargs):
        """set a default name"""
        if not self.name:
            self.name = self.domain
            update_fields = add_update_fields(update_fields, "name")

        super().save(*args, update_fields=update_fields, **kwargs)
