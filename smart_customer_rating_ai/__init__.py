from . import models
from . import wizard

from odoo import api, SUPERUSER_ID


def post_init_hook(cr, registry):
    """
    Remove leftover menu/action entries when the wizard/menu XML is removed.

    Odoo does not automatically delete existing records from the database when an
    XML record is removed from the module codebase, so this cleans up the UI.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    imd = env["ir.model.data"]

    # (module, name, model)
    xmlids = [
        ("smart_customer_rating_ai", "menu_ll_rating_recompute_wizard", "ir.ui.menu"),
        ("smart_customer_rating_ai", "action_ll_rating_recompute_wizard", "ir.actions.act_window"),
    ]

    for module, name, model in xmlids:
        data_row = imd.search(
            [("module", "=", module), ("name", "=", name)],
            limit=1,
        )
        if not data_row:
            continue

        rec = env[model].browse(data_row.res_id)
        if rec.exists():
            rec.unlink()
        # Remove the xmlid binding itself.
        data_row.unlink()
