"""Post-migration steps.

These functions are called at the end of ImportAll.__call__() to finalise
the imported content (create homepages, backfill tiles, etc.).

Each function receives the Plone *portal* object and operates with
admin privileges.  They commit their own transactions.
"""

from logging import getLogger

import transaction
from plone import api
from plone.app.multilingual.interfaces import ITranslationManager
from unibo.dipartimenti.subscribers.tiles import add_tiles_homepage
from unibo.tiles.utils import TilesFactory
from zope.publisher.browser import TestRequest

logger = getLogger(__name__)

# ---------------------------------------------------------------------------
# create_homepages
# ---------------------------------------------------------------------------

HOMEPAGE_ID = "index.html"
HOMEPAGE_TITLE_IT = "Homepage"
HOMEPAGE_TITLE_EN = "Homepage"
HOMEPAGE_TYPE = "HomePage"


def _set_default_page(obj, default_page_id):
    try:
        obj.setDefaultPage(default_page_id)
        logger.info(
            "Pagina predefinita impostata su %s in %s",
            default_page_id,
            obj.absolute_url(),
        )
    except Exception as exc:
        logger.error(
            "Errore impostando la pagina predefinita su %s in %s: %s",
            default_page_id,
            obj.absolute_url(),
            exc,
        )


def _publish_and_immutable(obj, label="Homepage"):
    """Porta l'oggetto da private -> published -> immutable."""
    try:
        state = api.content.get_state(obj=obj)
        if state == "private":
            api.content.transition(obj=obj, transition="publish")
            logger.info("%s pubblicato in %s", label, obj.absolute_url())
            state = api.content.get_state(obj=obj)
        elif state == "trashed":
            logger.warning("%s nello stato 'trashed' in %s", label, obj.absolute_url())
        if state == "published":
            api.content.transition(obj=obj, transition="make_immutable")
            logger.info("%s portato nello stato 'immutable' in %s", label, obj.absolute_url())
        elif state == "immutable":
            logger.info("%s già nello stato 'immutable' in %s", label, obj.absolute_url())
    except Exception as exc:
        logger.error(
            "Errore transizione stato per %s in %s: %s",
            label,
            obj.absolute_url(),
            exc,
        )


def _link_translation(obj_it, obj_en):
    tm = ITranslationManager(obj_it)
    if not tm.has_translation("en"):
        try:
            tm.register_translation("en", obj_en)
            logger.info("Collegata traduzione EN a IT per %s", obj_it.absolute_url())
        except Exception as exc:
            logger.error(
                "Errore collegamento traduzione EN a IT per %s: %s",
                obj_it.absolute_url(),
                exc,
            )
    else:
        logger.info("Traduzione EN già collegata a IT per %s", obj_it.absolute_url())


def _populate_tiles_if_empty(homepage):
    """Add the default tiles to a homepage that has none, e.g. one created by a
    previous run when the subscriber was still skipped during the import."""
    if any(getattr(homepage, f, None) for f in ("head_tiles", "content_tiles")):
        return
    add_tiles_homepage(homepage, None)
    logger.info("Tile di default create in %s", homepage.absolute_url())


def _create_homepages_for_site(lang_folder_it, lang_folder_en):
    """Create homepage objects in a single SiteContainer's language folders."""
    homepage_it = lang_folder_it.get(HOMEPAGE_ID)
    if not homepage_it:
        try:
            homepage_it = api.content.create(
                container=lang_folder_it,
                type=HOMEPAGE_TYPE,
                id=HOMEPAGE_ID,
                title=HOMEPAGE_TITLE_IT,
                language="it",
            )
            logger.info("Homepage IT creata in %s", lang_folder_it.absolute_url())
        except Exception as exc:
            logger.error(
                "Errore creando homepage IT in %s: %s",
                lang_folder_it.absolute_url(),
                exc,
            )
            return
    else:
        logger.info("Homepage IT già esistente in %s", lang_folder_it.absolute_url())

    homepage_en = lang_folder_en.get(HOMEPAGE_ID)
    if not homepage_en:
        try:
            homepage_en = api.content.create(
                container=lang_folder_en,
                type=HOMEPAGE_TYPE,
                id=HOMEPAGE_ID,
                title=HOMEPAGE_TITLE_EN,
                language="en",
            )
            logger.info("Homepage EN creata in %s", lang_folder_en.absolute_url())
        except Exception as exc:
            logger.error(
                "Errore creando homepage EN in %s: %s",
                lang_folder_en.absolute_url(),
                exc,
            )
            return
    else:
        logger.info("Homepage EN già esistente in %s", lang_folder_en.absolute_url())

    if homepage_en.title != HOMEPAGE_TITLE_EN:
        homepage_en.title = HOMEPAGE_TITLE_EN
        logger.info("Titolo homepage EN aggiornato in %s", lang_folder_en.absolute_url())

    _link_translation(homepage_it, homepage_en)
    _populate_tiles_if_empty(homepage_it)
    _populate_tiles_if_empty(homepage_en)
    _publish_and_immutable(homepage_it, label="Homepage IT")
    _publish_and_immutable(homepage_en, label="Homepage EN")
    homepage_it.reindexObject()
    homepage_en.reindexObject()
    _set_default_page(lang_folder_it, HOMEPAGE_ID)
    _set_default_page(lang_folder_en, HOMEPAGE_ID)


def create_homepages(portal):
    """Create HomePage objects in every SiteContainer's language folders."""
    logger.info("post_migration: create_homepages — start")
    with api.env.adopt_user("admin"):
        brains = api.content.find(portal_type="SiteContainer")
        for brain in brains:
            dip_path = brain.getPath()
            lang_folder_it = api.content.get(f"{dip_path}/it")
            lang_folder_en = api.content.get(f"{dip_path}/en")
            if not lang_folder_it:
                logger.warning("Folder %s/it non trovata", dip_path)
                continue
            if not lang_folder_en:
                logger.warning("Folder %s/en non trovata", dip_path)
                continue
            try:
                _create_homepages_for_site(lang_folder_it, lang_folder_en)
            except Exception as exc:
                logger.error("Errore su %s: %s", dip_path, exc)
    transaction.commit()
    logger.info("post_migration: create_homepages — done")


# ---------------------------------------------------------------------------
# backfill_news_room_tiles
# ---------------------------------------------------------------------------

NEWS_ROOM_HEAD_TILES = (
    ("head_tiles", "unibo.tiles.ultimora"),
)


def _has_tile(obj, section, tile_name):
    tiles = getattr(obj, section, None) or ()
    prefix = f"@@{tile_name}/"
    return any(tile.startswith(prefix) for tile in tiles)


def _ensure_mutable_tiles_section(obj, section):
    """Normalize tuple-based tile sections to list for TilesFactory.append()."""
    value = getattr(obj, section, None)
    if isinstance(value, tuple):
        setattr(obj, section, list(value))


def backfill_news_room_tiles(portal):
    """Ensure every NewsRoom has the required head tiles."""
    logger.info("post_migration: backfill_news_room_tiles — start")
    commit_every = 50
    created = 0
    skipped = 0
    errors = 0
    pending = 0

    with api.env.adopt_user(username="admin"):
        request = TestRequest()
        factory = TilesFactory()

        brains = api.content.find(portal_type="NewsRoom")
        logger.info("Found %d NewsRoom objects", len(brains))

        for brain in brains:
            obj = brain.getObject()
            path = "/".join(obj.getPhysicalPath())

            missing = []
            for section, tile_name in NEWS_ROOM_HEAD_TILES:
                if not hasattr(obj, section):
                    continue
                if not _has_tile(obj, section, tile_name):
                    missing.append((section, tile_name))

            if not missing:
                skipped += 1
                continue

            try:
                for section, tile_name in missing:
                    _ensure_mutable_tiles_section(obj, section)
                    factory.create_tile(obj, request, tile_name, section)
                    created += 1
                obj.reindexObject()
                pending += 1
            except Exception as exc:
                errors += 1
                logger.error("[ERROR] %s: %s", path, exc)

            if pending >= commit_every:
                transaction.commit()
                pending = 0

    transaction.commit()
    logger.info(
        "post_migration: backfill_news_room_tiles — done "
        "(created=%d skipped=%d errors=%d)",
        created,
        skipped,
        errors,
    )


# ---------------------------------------------------------------------------
# backfill_opportunita_bandi
# ---------------------------------------------------------------------------


def _has_bandi_tile(obj):
    tiles = getattr(obj, "content_tiles", None) or ()
    return any(tile.startswith("@@unibo.tiles.bandi/") for tile in tiles)


def backfill_opportunita_bandi(portal):
    """Ensure every Opportunita has the bandi tile in content_tiles."""
    logger.info("post_migration: backfill_opportunita_bandi — start")
    commit_every = 50
    created = 0
    skipped = 0
    errors = 0
    pending = 0

    with api.env.adopt_user(username="admin"):
        request = TestRequest()
        factory = TilesFactory()

        brains = api.content.find(portal_type="Opportunita")
        logger.info("Found %d Opportunita objects", len(brains))

        for brain in brains:
            obj = brain.getObject()
            path = "/".join(obj.getPhysicalPath())

            if not hasattr(obj, "content_tiles"):
                skipped += 1
                continue

            if _has_bandi_tile(obj):
                skipped += 1
                continue

            try:
                factory.create_tile(obj, request, "unibo.tiles.bandi", "content_tiles")
                obj.reindexObject()
                created += 1
                pending += 1
            except Exception as exc:
                errors += 1
                logger.error("[ERROR] %s: %s", path, exc)

            if pending >= commit_every:
                transaction.commit()
                pending = 0

    transaction.commit()
    logger.info(
        "post_migration: backfill_opportunita_bandi — done "
        "(created=%d skipped=%d errors=%d)",
        created,
        skipped,
        errors,
    )
