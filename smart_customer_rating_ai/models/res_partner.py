from odoo import api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    customer_rating_id = fields.Many2one(
        "customer.rating",
        string="Customer Rating Record",
        compute="_compute_customer_rating_fields",
        store=True,
        readonly=True,
    )
    customer_rating_stars = fields.Html(
        string="Customer Rating",
        compute="_compute_customer_rating_fields",
        store=True,
        sanitize=False,
        readonly=True,
    )
    customer_rating_count = fields.Integer(
        string="Rating Count",
        compute="_compute_customer_rating_fields",
        store=True,
        readonly=True,
    )

    @api.depends("name", "customer_rating_id.rating")
    def _compute_display_name(self):
        super()._compute_display_name()
        for partner in self:
            rating = partner.customer_rating_id.rating
            if rating:
                partner.display_name = f"{partner.display_name} (⭐ {rating:.1f})"

    @api.depends("name")
    def _compute_customer_rating_fields(self):
        rating_model = self.env["customer.rating"]
        empty_stars = rating_model.render_stars_html(0.0)
        # We search for ratings linked to these partners
        ratings = rating_model.search([('customer_id', 'in', self.ids)])
        rating_map = {r.customer_id.id: r for r in ratings}

        for partner in self:
            rating = rating_map.get(partner.id)
            partner.customer_rating_id = rating.id if rating else False
            partner.customer_rating_stars = rating.rating_stars if rating else empty_stars
            partner.customer_rating_count = 1 if rating else 0

    def action_open_customer_rating(self):
        self.ensure_one()
        rating = self.env["customer.rating"].search([("customer_id", "=", self.id)], limit=1)
        if not rating:
            rating = self.env["customer.rating"].create({"customer_id": self.id})
        return {
            "type": "ir.actions.act_window",
            "name": "Customer Rating",
            "res_model": "customer.rating",
            "view_mode": "form",
            "res_id": rating.id,
            "target": "current",
        }
