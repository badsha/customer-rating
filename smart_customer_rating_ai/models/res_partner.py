from odoo import api, fields, models
from odoo.orm.identifiers import NewId


class ResPartner(models.Model):
    _inherit = "res.partner"

    def init(self):
        self._cr.execute("ALTER TABLE res_partner DROP COLUMN IF EXISTS customer_rating_id")

    customer_rating_ids = fields.One2many("customer.rating", "customer_id")
    customer_rating_id = fields.Many2one(
        "customer.rating",
        string="Customer Rating Record",
        compute="_compute_customer_rating_id",
        store=False,
        readonly=True,
    )
    customer_rating_stars = fields.Html(
        string="Customer Rating",
        compute="_compute_customer_rating_display",
        sanitize=False,
        readonly=True,
    )
    customer_rating_count = fields.Integer(
        string="Rating Count",
        compute="_compute_customer_rating_display",
        readonly=True,
    )
    criteria_ids = fields.One2many(
        "customer.rating.criteria",
        "customer_id",
        string="Rating Criteria Lines",
    )

    @api.depends("customer_rating_ids")
    def _compute_customer_rating_id(self):
        for partner in self:
            partner.customer_rating_id = partner.customer_rating_ids[:1]

    def action_sync_customer_rating(self):
        self._ensure_customer_rating()
        return True

    def _ensure_customer_rating(self):
        """Helper to create and sync rating record."""
        for partner in self:
            # Skip if it's a new record or transient
            p_id = partner._origin.id or partner.id
            if not p_id or isinstance(p_id, NewId):
                continue

            rating = partner.customer_rating_ids[:1]
            if not rating:
                rating = self.env["customer.rating"].create({"customer_id": p_id})

            if not rating.final_criteria_id:
                rating.write({"final_criteria_id": rating._get_default_template_id()})

            if not rating.criteria_ids:
                rating._sync_from_template()


    @api.depends("customer_rating_ids", "customer_rating_ids.rating", "customer_rating_ids.rating_stars")
    def _compute_customer_rating_display(self):
        rating_model = self.env["customer.rating"]
        empty_stars = rating_model.render_stars_html(0.0)
        for partner in self:
            rating = partner.customer_rating_ids[:1]
            partner.customer_rating_stars = rating.rating_stars if rating else empty_stars
            partner.customer_rating_count = 1 if rating else 0

    def action_open_customer_rating(self):
        self.ensure_one()
        rating = self.customer_rating_ids[:1]
        if not rating:
            rating = self.env["customer.rating"].create({"customer_id": self.id})
        rating._sync_from_template()
        return {
            "type": "ir.actions.act_window",
            "name": "Customer Rating",
            "res_model": "customer.rating",
            "view_mode": "form",
            "res_id": rating.id,
            "target": "current",
        }
