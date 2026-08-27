from logging import getLogger

import transaction
from plone import api
from Products.Five import BrowserView

logger = getLogger(__name__)

# relstorage locks each modified object individually, so a whole-site
# transaction exhausts the postgres lock pool
COMMIT_EVERY = 500


class ResetLastModifiedBy(BrowserView):
    def __call__(self):
        self.title = "Reset last modified by"
        self.help_text = ("<p>Last modified by is changed by subscribers during import."
                          " This resets them to the original values of the imported content.</p>")
        if not self.request.form.get("form.submitted", False):
            return self.index()

        portal = api.portal.get()

        stats = {"restored": 0}

        def apply_func(obj, path):
            if not reset_modifier(obj, path):
                return
            stats["restored"] += 1
            if not stats["restored"] % COMMIT_EVERY:
                logger.info("Committing after %d resets", stats["restored"])
                transaction.commit()

        portal.ZopeFindAndApply(portal, search_sub=True, apply_func=apply_func)
        msg = f"Finished resetting last modified by on {stats['restored']} objects."
        logger.info(msg)
        api.portal.show_message(msg, self.request)
        return self.index()


def reset_modifier(obj, path):
    """Restore the last modifier exported with the content.

    Subscribers set last_modified_by to the importing user on every add/modify
    event, so import_content stashes the exported value on
    last_modifier_migrated for this pass to apply.
    """
    last_modifier = getattr(obj.aq_base, "last_modifier_migrated", None)
    if not last_modifier:
        return False
    del obj.last_modifier_migrated
    # aq_base: without it a parent's value would be read through acquisition
    if last_modifier == getattr(obj.aq_base, "last_modified_by", None):
        return False
    obj.last_modified_by = last_modifier
    obj.reindexObject(idxs=["last_modified_by"])
    return True
