from odoo import api, fields, models, _
from odoo.orm.identifiers import NewId


class ResPartner(models.Model):
    _inherit = "res.partner"

    def web_read(self, specification):
        # Avoid re-running `_ensure_customer_rating()` on every reload of the Ratings tab,
        # because it can overwrite the currently selected template while the user is editing.
        if isinstance(specification, dict) and (
            specification.get("criteria_ids") is not None
            or specification.get("customer_rating_ids") is not None
            or specification.get("selected_manual_criteria_ids") is not None
        ):
            partner_ids = self.ids
            if partner_ids:
                rating_count = self.env["customer.rating"].sudo().search_count(
                    [("customer_id", "in", partner_ids)]
                )
                if rating_count == 0:
                    self._ensure_customer_rating()
        return super().web_read(specification)

    customer_rating_ids = fields.One2many("customer.rating", "customer_id", string="Customer Ratings")
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

    # Select which *manual/template* criteria are shown on the contact form.
    customer_rating_template_id = fields.Many2one(
        "final.criteria",
        string="Assessment Template",
        help="Select which template's manual criteria are shown in the Ratings tab.",
        ondelete="set null",
    )

    selected_manual_rating_id = fields.Many2one(
        "customer.rating",
        string="Selected Manual Rating",
        compute="_compute_selected_manual_rating_id",
        store=False,
        readonly=True,
    )
    selected_manual_rating_stars = fields.Html(
        string="Manual Rating",
        compute="_compute_selected_manual_rating_stars",
        sanitize=False,
        readonly=True,
    )
    selected_manual_rating_score = fields.Float(
        string="Manual Score",
        related="selected_manual_rating_id.rating",
        readonly=True,
    )
    selected_manual_has_criteria = fields.Boolean(
        string="Selected Template Has Criteria",
        compute="_compute_selected_manual_has_criteria",
        store=False,
        readonly=True,
    )

    # Editable criteria lines for the currently selected manual template.
    # This is a real One2many so edits are persisted normally.
    selected_manual_criteria_ids = fields.One2many(
        "customer.rating.criteria",
        "customer_id",
        string="Selected Criteria Lines",
    )
    criteria_ids = fields.One2many(
        "customer.rating.criteria",
        "customer_id",
        string="Rating Criteria Lines",
    )

    @api.depends("customer_rating_ids", "customer_rating_ids.is_primary", "customer_rating_ids.rating", "customer_rating_ids.rating_stars")
    def _compute_customer_rating_id(self):
        for partner in self:
            primary = partner.customer_rating_ids.filtered(lambda r: r.is_primary)
            partner.customer_rating_id = primary[:1] or partner.customer_rating_ids.filtered(lambda r: r.is_automatic)[:1] or partner.customer_rating_ids[:1]

    def action_sync_customer_rating(self):
        self._ensure_customer_rating()
        return True

    def action_open_selected_manual_rating_dialog(self):
        """Open the manual rating breakdown dialog in edit mode."""
        self.ensure_one()
        self._ensure_customer_rating()
        if not self.selected_manual_rating_id:
            return False
        view = self.env.ref("smart_customer_rating_ai.view_customer_rating_dialog")
        return {
            "type": "ir.actions.act_window",
            "name": _("Manual Rating Breakdown"),
            "res_model": "customer.rating",
            "res_id": self.selected_manual_rating_id.id,
            "view_mode": "form",
            "views": [(view.id, "form")],
            "target": "new",
        }

    def _ensure_automatic_rating(self):
        """Ensure each partner has exactly one is_automatic=True rating record."""
        # Keep it consistent with the full Ratings tab initialization.
        self._ensure_customer_rating()

    def _ensure_customer_rating(self):
        """Ensure manual ratings for all templates + one automated (AI/rules) rating."""
        for partner in self:
            # Skip if it's a new record or transient
            p_id = partner._origin.id or partner.id
            if not p_id or isinstance(p_id, NewId):
                continue

            templates = self.env["final.criteria"].search([], order="id")

            # Ensure one *manual* rating per template.
            existing_ratings = self.env["customer.rating"].search([("customer_id", "=", p_id)])
            existing_manual_by_template = {
                r.final_criteria_id.id: r
                for r in existing_ratings
                if not r.is_automatic and r.final_criteria_id
            }

            for template in templates:
                rating = existing_manual_by_template.get(template.id)
                if not rating:
                    rating = self.env["customer.rating"].create({
                        "customer_id": p_id,
                        "final_criteria_id": template.id,
                        # We'll set a single primary below (typically to the automated rating).
                        "is_primary": False,
                    })
                    existing_manual_by_template[template.id] = rating
                if not rating.criteria_ids:
                    rating._sync_from_template()

            # Ensure one automated rating.
            auto_rating = existing_ratings.filtered(lambda r: r.is_automatic)[:1]
            if not auto_rating:
                auto_rating = self.env["customer.rating"].create({
                    "customer_id": p_id,
                    "is_automatic": True,
                    "is_primary": False,
                })

            # Default template selector (used for the editable/manual criteria section on the contact).
            if (
                not partner.customer_rating_template_id
                or partner.customer_rating_template_id.id not in templates.ids
            ):
                partner.customer_rating_template_id = templates[:1] if templates else False

            # Make the automated rating the primary one so the "Final Rating" line reflects AI/rules.
            all_ratings = self.env["customer.rating"].search([("customer_id", "=", p_id)])
            primaries = all_ratings.filtered("is_primary")
            if primaries:
                primaries.write({"is_primary": False})
            auto_rating.write({"is_primary": True})


    @api.depends("customer_rating_ids", "customer_rating_ids.is_primary", "customer_rating_ids.rating", "customer_rating_ids.rating_stars")
    def _compute_customer_rating_display(self):
        rating_model = self.env["customer.rating"]
        empty_stars = rating_model.render_stars_html(0.0)
        for partner in self:
            rating = partner.customer_rating_ids.filtered(lambda r: r.is_primary)[:1] or partner.customer_rating_ids[:1]
            partner.customer_rating_stars = rating.rating_stars if rating else empty_stars

    @api.depends(
        "customer_rating_template_id",
        "customer_rating_ids.final_criteria_id",
        "customer_rating_ids.is_automatic",
    )
    def _compute_selected_manual_rating_id(self):
        for partner in self:
            manual_ratings = partner.customer_rating_ids.filtered(lambda r: not r.is_automatic)
            target_tid = partner.customer_rating_template_id.id if partner.customer_rating_template_id else False
            if target_tid:
                partner.selected_manual_rating_id = manual_ratings.filtered(
                    lambda r: r.final_criteria_id.id == target_tid
                )[:1]
            else:
                partner.selected_manual_rating_id = manual_ratings[:1]

    @api.depends("selected_manual_rating_id")
    def _compute_selected_manual_rating_stars(self):
        rating_model = self.env["customer.rating"]
        empty_stars = rating_model.render_stars_html(0.0)
        for partner in self:
            partner.selected_manual_rating_stars = (
                partner.selected_manual_rating_id.rating_stars if partner.selected_manual_rating_id else empty_stars
            )

    @api.depends("selected_manual_rating_id")
    def _compute_selected_manual_has_criteria(self):
        for partner in self:
            partner.selected_manual_has_criteria = bool(
                partner.selected_manual_rating_id and partner.selected_manual_rating_id.criteria_ids
            )
