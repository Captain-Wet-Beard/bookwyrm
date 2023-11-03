""" database schema for books and shelves """
from itertools import chain
import re
from typing import Any

from django.contrib.postgres.search import SearchVectorField
from django.contrib.postgres.indexes import GinIndex
from django.core.cache import cache
from django.db import models, transaction
from django.db.models import Prefetch
from django.dispatch import receiver
from django.utils.translation import gettext_lazy as _
from model_utils import FieldTracker
from model_utils.managers import InheritanceManager
from imagekit.models import ImageSpecField

from bookwyrm import activitypub
from bookwyrm.isbn.isbn import hyphenator_singleton as hyphenator
from bookwyrm.preview_images import generate_edition_preview_image_task
from bookwyrm.settings import (
    DOMAIN,
    DEFAULT_LANGUAGE,
    LANGUAGE_ARTICLES,
    ENABLE_PREVIEW_IMAGES,
    ENABLE_THUMBNAIL_GENERATION,
)

from .activitypub_mixin import OrderedCollectionPageMixin, ObjectMixin
from .base_model import BookWyrmModel
from . import fields


class BookDataModel(ObjectMixin, BookWyrmModel):
    """fields shared between editable book data (books, works, authors)"""

    origin_id = models.CharField(max_length=255, null=True, blank=True)
    openlibrary_key = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    inventaire_id = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    librarything_key = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    goodreads_key = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    bnf_id = fields.CharField(  # Bibliothèque nationale de France
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    viaf = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    wikidata = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    asin = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    aasin = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    isfdb = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    search_vector = SearchVectorField(null=True)

    last_edited_by = fields.ForeignKey(
        "User",
        on_delete=models.PROTECT,
        null=True,
    )

    @property
    def openlibrary_link(self):
        """generate the url from the openlibrary id"""
        return f"https://openlibrary.org/books/{self.openlibrary_key}"

    @property
    def inventaire_link(self):
        """generate the url from the inventaire id"""
        return f"https://inventaire.io/entity/{self.inventaire_id}"

    @property
    def isfdb_link(self):
        """generate the url from the isfdb id"""
        return f"https://www.isfdb.org/cgi-bin/title.cgi?{self.isfdb}"

    class Meta:
        """can't initialize this model, that wouldn't make sense"""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """ensure that the remote_id is within this instance"""
        if self.id:
            self.remote_id = self.get_remote_id()
        else:
            self.origin_id = self.remote_id
            self.remote_id = None
        return super().save(*args, **kwargs)

    # pylint: disable=arguments-differ
    def broadcast(self, activity, sender, software="bookwyrm", **kwargs):
        """only send book data updates to other bookwyrm instances"""
        super().broadcast(activity, sender, software=software, **kwargs)


class Book(BookDataModel):
    """a generic book, which can mean either an edition or a work"""

    connector = models.ForeignKey("Connector", on_delete=models.PROTECT, null=True)

    # book/work metadata
    title = fields.TextField(max_length=255)
    sort_title = fields.CharField(max_length=255, blank=True, null=True)
    subtitle = fields.TextField(max_length=255, blank=True, null=True)
    description = fields.HtmlField(blank=True, null=True)
    languages = fields.ArrayField(
        models.CharField(max_length=255), blank=True, default=list
    )
    series = fields.TextField(max_length=255, blank=True, null=True)
    series_number = fields.CharField(max_length=255, blank=True, null=True)
    subjects = fields.ArrayField(
        models.CharField(max_length=255), blank=True, null=True, default=list
    )
    subject_places = fields.ArrayField(
        models.CharField(max_length=255), blank=True, null=True, default=list
    )
    authors = fields.ManyToManyField("Author")
    cover = fields.ImageField(
        upload_to="covers/", blank=True, null=True, alt_field="alt_text"
    )
    preview_image = models.ImageField(
        upload_to="previews/covers/", blank=True, null=True
    )
    first_published_date = fields.DateTimeField(blank=True, null=True)
    published_date = fields.DateTimeField(blank=True, null=True)

    objects = InheritanceManager()
    field_tracker = FieldTracker(fields=["authors", "title", "subtitle", "cover"])

    if ENABLE_THUMBNAIL_GENERATION:
        cover_bw_book_xsmall_webp = ImageSpecField(
            source="cover", id="bw:book:xsmall:webp"
        )
        cover_bw_book_xsmall_jpg = ImageSpecField(
            source="cover", id="bw:book:xsmall:jpg"
        )
        cover_bw_book_small_webp = ImageSpecField(
            source="cover", id="bw:book:small:webp"
        )
        cover_bw_book_small_jpg = ImageSpecField(source="cover", id="bw:book:small:jpg")
        cover_bw_book_medium_webp = ImageSpecField(
            source="cover", id="bw:book:medium:webp"
        )
        cover_bw_book_medium_jpg = ImageSpecField(
            source="cover", id="bw:book:medium:jpg"
        )
        cover_bw_book_large_webp = ImageSpecField(
            source="cover", id="bw:book:large:webp"
        )
        cover_bw_book_large_jpg = ImageSpecField(source="cover", id="bw:book:large:jpg")
        cover_bw_book_xlarge_webp = ImageSpecField(
            source="cover", id="bw:book:xlarge:webp"
        )
        cover_bw_book_xlarge_jpg = ImageSpecField(
            source="cover", id="bw:book:xlarge:jpg"
        )
        cover_bw_book_xxlarge_webp = ImageSpecField(
            source="cover", id="bw:book:xxlarge:webp"
        )
        cover_bw_book_xxlarge_jpg = ImageSpecField(
            source="cover", id="bw:book:xxlarge:jpg"
        )

    @property
    def author_text(self):
        """format a list of authors"""
        return ", ".join(a.name for a in self.authors.all())

    @property
    def latest_readthrough(self):
        """most recent readthrough activity"""
        return self.readthrough_set.order_by("-updated_date").first()

    @property
    def edition_info(self):
        """properties of this edition, as a string"""
        items = [
            self.physical_format if hasattr(self, "physical_format") else None,
            f"{self.languages[0]} language"
            if self.languages and self.languages[0] and self.languages[0] != "English"
            else None,
            str(self.published_date.year) if self.published_date else None,
            ", ".join(self.publishers) if hasattr(self, "publishers") else None,
        ]
        return ", ".join(i for i in items if i)

    @property
    def alt_text(self):
        """image alt test"""
        author = f"{name}: " if (name := self.author_text) else ""
        edition = f" ({info})" if (info := self.edition_info) else ""
        return f"{author}{self.title}{edition}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """can't be abstract for query reasons, but you shouldn't USE it"""
        if not isinstance(self, (Edition, Work)):
            raise ValueError("Books should be added as Editions or Works")

        return super().save(*args, **kwargs)

    def get_remote_id(self):
        """editions and works both use "book" instead of model_name"""
        return f"https://{DOMAIN}/book/{self.id}"

    def guess_sort_title(self):
        """Get a best-guess sort title for the current book"""
        articles = chain(
            *(LANGUAGE_ARTICLES.get(language, ()) for language in tuple(self.languages))
        )
        return re.sub(f'^{" |^".join(articles)} ', "", str(self.title).lower())

    def __repr__(self):
        # pylint: disable=consider-using-f-string
        return "<{} key={!r} title={!r}>".format(
            self.__class__,
            self.openlibrary_key,
            self.title,
        )

    class Meta:
        """sets up postgres GIN index field"""

        indexes = (GinIndex(fields=["search_vector"]),)


class Work(OrderedCollectionPageMixin, Book):
    """a work (an abstract concept of a book that manifests in an edition)"""

    # library of congress catalog control number
    lccn = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )

    def save(self, *args, **kwargs):
        """set some fields on the edition object"""
        # set rank
        for edition in self.editions.all():
            edition.save()
        return super().save(*args, **kwargs)

    @property
    def default_edition(self):
        """in case the default edition is not set"""
        return self.editions.order_by("-edition_rank").first()

    def author_edition(self, author):
        """in case the default edition doesn't have the required author"""
        return self.editions.filter(authors=author).order_by("-edition_rank").first()

    def to_edition_list(self, **kwargs):
        """an ordered collection of editions"""
        return self.to_ordered_collection(
            self.editions.order_by("-edition_rank").all(),
            remote_id=f"{self.remote_id}/editions",
            **kwargs,
        )

    activity_serializer = activitypub.Work
    serialize_reverse_fields = [
        ("editions", "editions", "-edition_rank"),
        ("file_links", "fileLinks", "-created_date"),
    ]
    deserialize_reverse_fields = [("editions", "editions"), ("file_links", "fileLinks")]


# https://schema.org/BookFormatType
FormatChoices = [
    ("AudiobookFormat", _("Audiobook")),
    ("EBook", _("eBook")),
    ("GraphicNovel", _("Graphic novel")),
    ("Hardcover", _("Hardcover")),
    ("Paperback", _("Paperback")),
]


class Edition(Book):
    """an edition of a book"""

    # these identifiers only apply to editions, not works
    isbn_10 = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    isbn_13 = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    oclc_number = fields.CharField(
        max_length=255, blank=True, null=True, deduplication_field=True
    )
    pages = fields.IntegerField(blank=True, null=True)
    physical_format = fields.CharField(
        max_length=255, choices=FormatChoices, null=True, blank=True
    )
    physical_format_detail = fields.CharField(max_length=255, blank=True, null=True)
    publishers = fields.ArrayField(
        models.CharField(max_length=255), blank=True, default=list
    )
    shelves = models.ManyToManyField(
        "Shelf",
        symmetrical=False,
        through="ShelfBook",
        through_fields=("book", "shelf"),
    )
    parent_work = fields.ForeignKey(
        "Work",
        on_delete=models.PROTECT,
        null=True,
        related_name="editions",
        activitypub_field="work",
    )
    edition_rank = fields.IntegerField(default=0)

    activity_serializer = activitypub.Edition
    name_field = "title"
    serialize_reverse_fields = [("file_links", "fileLinks", "-created_date")]
    deserialize_reverse_fields = [("file_links", "fileLinks")]

    @property
    def hyphenated_isbn13(self):
        """generate the hyphenated version of the ISBN-13"""
        return hyphenator.hyphenate(self.isbn_13)

    def get_rank(self):
        """calculate how complete the data is on this edition"""
        rank = 0
        # big ups for having a cover
        rank += int(bool(self.cover)) * 3
        # is it in the instance's preferred language?
        rank += int(bool(DEFAULT_LANGUAGE in self.languages))
        # prefer print editions
        if self.physical_format:
            rank += int(
                bool(self.physical_format.lower() in ["paperback", "hardcover"])
            )

        # does it have metadata?
        rank += int(bool(self.isbn_13))
        rank += int(bool(self.isbn_10))
        rank += int(bool(self.oclc_number))
        rank += int(bool(self.pages))
        rank += int(bool(self.physical_format))
        rank += int(bool(self.description))
        # max rank is 9
        return rank

    def save(self, *args: Any, **kwargs: Any) -> None:
        """set some fields on the edition object"""
        # calculate isbn 10/13
        if self.isbn_13 and self.isbn_13[:3] == "978" and not self.isbn_10:
            self.isbn_10 = isbn_13_to_10(self.isbn_13)
        if self.isbn_10 and not self.isbn_13:
            self.isbn_13 = isbn_10_to_13(self.isbn_10)

        # normalize isbn format
        if self.isbn_10:
            self.isbn_10 = normalize_isbn(self.isbn_10)
        if self.isbn_13:
            self.isbn_10 = normalize_isbn(self.isbn_13)

        # set rank
        self.edition_rank = self.get_rank()

        # clear author cache
        if self.id:
            for author_id in self.authors.values_list("id", flat=True):
                cache.delete(f"author-books-{author_id}")

        # Create sort title by removing articles from title
        if self.sort_title in [None, ""]:
            self.sort_title = self.guess_sort_title()

        return super().save(*args, **kwargs)

    @transaction.atomic
    def repair(self):
        """If an edition is in a bad state (missing a work), let's fix that"""
        # made sure it actually NEEDS reapir
        if self.parent_work:
            return

        new_work = Work.objects.create(title=self.title)
        new_work.authors.set(self.authors.all())

        self.parent_work = new_work
        self.save(update_fields=["parent_work"], broadcast=False)

    @classmethod
    def viewer_aware_objects(cls, viewer):
        """annotate a book query with metadata related to the user"""
        queryset = cls.objects
        if not viewer or not viewer.is_authenticated:
            return queryset

        queryset = queryset.prefetch_related(
            Prefetch(
                "shelfbook_set",
                queryset=viewer.shelfbook_set.all(),
                to_attr="current_shelves",
            ),
            Prefetch(
                "readthrough_set",
                queryset=viewer.readthrough_set.filter(is_active=True).all(),
                to_attr="active_readthroughs",
            ),
        )
        return queryset


def isbn_10_to_13(isbn_10):
    """convert an isbn 10 into an isbn 13"""
    isbn_10 = re.sub(r"[^0-9X]", "", isbn_10)
    # drop the last character of the isbn 10 number (the original checkdigit)
    converted = isbn_10[:9]
    # add "978" to the front
    converted = "978" + converted
    # add a check digit to the end
    # multiply the odd digits by 1 and the even digits by 3 and sum them
    try:
        checksum = sum(int(i) for i in converted[::2]) + sum(
            int(i) * 3 for i in converted[1::2]
        )
    except ValueError:
        return None
    # add the checksum mod 10 to the end
    checkdigit = checksum % 10
    if checkdigit != 0:
        checkdigit = 10 - checkdigit
    return converted + str(checkdigit)


def isbn_13_to_10(isbn_13):
    """convert isbn 13 to 10, if possible"""
    if isbn_13[:3] != "978":
        return None

    isbn_13 = re.sub(r"[^0-9X]", "", isbn_13)

    # remove '978' and old checkdigit
    converted = isbn_13[3:-1]
    # calculate checkdigit
    # multiple each digit by 10,9,8.. successively and sum them
    try:
        checksum = sum(int(d) * (10 - idx) for (idx, d) in enumerate(converted))
    except ValueError:
        return None
    checkdigit = checksum % 11
    checkdigit = 11 - checkdigit
    if checkdigit == 10:
        checkdigit = "X"
    return converted + str(checkdigit)


def normalize_isbn(isbn):
    """Remove unexpected characters from ISBN 10 or 13"""
    return re.sub(r"[^0-9X]", "", isbn)


# pylint: disable=unused-argument
@receiver(models.signals.post_save, sender=Edition)
def preview_image(instance, *args, **kwargs):
    """create preview image on book create"""
    if not ENABLE_PREVIEW_IMAGES:
        return
    changed_fields = {}
    if instance.field_tracker:
        changed_fields = instance.field_tracker.changed()

    if len(changed_fields) > 0:
        transaction.on_commit(
            lambda: generate_edition_preview_image_task.delay(instance.id)
        )
