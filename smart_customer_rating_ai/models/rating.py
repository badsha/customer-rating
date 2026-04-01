import json
from datetime import timedelta

from odoo import _, api, fields, models


class CustomerRating(models.Model):
    _name = "customer.rating"
    _description = "Customer Rating"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    _LOW_MAX = 2.49
    _MEDIUM_MAX = 3.99

    customer_id = fields.Many2one(
        "res.partner",
        string="Customer",
        required=True,
        tracking=True
    )
    notes = fields.Text(string="Notes", tracking=True)
    final_criteria_id = fields.Many2one(
        "final.criteria",
        string="Template",
        default=lambda self: self._get_default_template_id(),
        tracking=True
    )

    criteria_ids = fields.One2many(
        "customer.rating.criteria",
        "rating_id",
        string="Criteria Lines",
    )

    rating = fields.Float(
        string="Rating Score",
        compute="_compute_rating",
        store=True,
        digits=(16, 2),
        tracking=True
    )

    rating_stars = fields.Html(
        string="Final Rating",
        compute="_compute_rating_stars",
        sanitize=False,
    )
    rating_bucket = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
        ],
        string="Tag",
        compute="_compute_rating_bucket",
        store=True,
        index=True,
        tracking=True
    )
    history_ids = fields.One2many("customer.rating.history", "rating_id", string="Timeline", readonly=True)

    _sql_constraints = [
        ("unique_customer_id", "UNIQUE (customer_id)", "Only one rating is allowed per customer."),
    ]

    @api.model
    def get_rating_map(self, partner_ids):
        partner_ids = [pid for pid in partner_ids if pid]
        if not partner_ids:
            return {}
        ratings = self.search([("customer_id", "in", partner_ids)])
        return {rating.customer_id.id: rating for rating in ratings}

    @api.model
    def render_stars_html(self, value):
        val = value or 0.0
        full, half = int(val), 1 if (val - int(val)) >= 0.5 else 0
        empty = 5 - full - half
        content = "".join(
            ["<i class='fa fa-star text-warning'></i>" * full]
            + ["<i class='fa fa-star-half-o text-warning'></i>" * half]
            + ["<i class='fa fa-star-o text-muted'></i>" * empty]
        )
        return f"<span style='font-size:14px;white-space:nowrap;display:inline-block;'>{content}</span>"

    @api.model
    def _get_default_template(self):
        template = self.env["final.criteria"].search([("name", "ilike", "criteria")], limit=1)
        if not template:
            template = self.env["final.criteria"].search([], order="id", limit=1)
        return template

    @api.model
    def _get_default_template_id(self):
        return self._get_default_template().id

    @api.model
    def _prepare_template_line_vals(self, template_line, default_score="0"):
        return {
            "template_line_id": template_line.id,
            "name": template_line.name,
            "score": default_score,
        }

    @api.model
    def _prepare_template_lines(self, template, default_score="0"):
        return [(0, 0, self._prepare_template_line_vals(line, default_score=default_score)) for line in template.line_ids]

    def _sync_from_template(self):
        criteria_model = self.env["customer.rating.criteria"]
        for rating in self:
            template = rating.final_criteria_id
            if not template:
                continue

            template_lines = template.line_ids
            template_line_ids = set(template_lines.ids)

            template_by_normalized_name = {}
            duplicate_names = set()
            for t_line in template_lines:
                key = (t_line.name or "").strip().lower()
                if key in template_by_normalized_name:
                    duplicate_names.add(key)
                else:
                    template_by_normalized_name[key] = t_line

            for crit_line in rating.criteria_ids.filtered(lambda rec: not rec.template_line_id):
                key = (crit_line.name or "").strip().lower()
                if not key or key in duplicate_names or key not in template_by_normalized_name:
                    continue
                target_template_line = template_by_normalized_name[key]
                already_linked = rating.criteria_ids.filtered(lambda rec: rec.template_line_id == target_template_line)
                if not already_linked:
                    crit_line.write(
                        {
                            "template_line_id": target_template_line.id,
                            "name": target_template_line.name,
                        }
                    )

            linked_lines = rating.criteria_ids.filtered(lambda rec: rec.template_line_id)
            linked_by_template_id = {line.template_line_id.id: line for line in linked_lines if line.template_line_id}

            obsolete_lines = linked_lines.filtered(lambda rec: rec.template_line_id.id not in template_line_ids)
            if obsolete_lines:
                obsolete_lines.unlink()

            for template_line in template_lines:
                existing = linked_by_template_id.get(template_line.id)
                if existing:
                    if existing.name != template_line.name:
                        existing.write({"name": template_line.name})
                else:
                    vals = self._prepare_template_line_vals(template_line, default_score="0")
                    vals["rating_id"] = rating.id
                    vals["customer_id"] = rating.customer_id.id
                    criteria_model.create(vals)

    def _snapshot_state(self):
        self.ensure_one()
        criteria_rows = []
        for line in self.criteria_ids.sorted(lambda rec: ((rec.template_line_id.id or 0), rec.id)):
            criteria_rows.append(
                {
                    "line_id": line.id,
                    "template_line_id": line.template_line_id.id or False,
                    "name": line.name or "",
                    "score": line.score or 0,
                    "notes": line.notes or "",
                }
            )
        return {
            "rating": round(self.rating or 0.0, 2),
            "template_id": self.final_criteria_id.id or False,
            "notes": self.notes or "",
            "criteria": criteria_rows,
        }

    def _snapshot_map(self):
        return {record.id: record._snapshot_state() for record in self}

    @api.model
    def _build_diff_summary(self, before, after, change_type):
        if not before:
            return _("Rating created. Score: %(score).2f, Criteria lines: %(count)s") % {
                "score": after["rating"],
                "count": len(after["criteria"]),
            }

        summary_parts = []
        if before.get("rating") != after.get("rating"):
            summary_parts.append(
                _("Score %(before).2f -> %(after).2f")
                % {"before": before.get("rating", 0.0), "after": after.get("rating", 0.0)}
            )

        before_ids = {row.get("template_line_id") for row in before.get("criteria", [])}
        after_ids = {row.get("template_line_id") for row in after.get("criteria", [])}
        added = len(after_ids - before_ids)
        removed = len(before_ids - after_ids)
        if added or removed:
            summary_parts.append(_("Criteria changes: +%(added)s / -%(removed)s") % {"added": added, "removed": removed})

        if before.get("notes") != after.get("notes"):
            summary_parts.append(_("Notes updated"))

        if not summary_parts:
            summary_parts.append(_("No score delta (%s)") % change_type)
        return "; ".join(summary_parts)

    def _log_history(self, change_type, before_map=None):
        history_model = self.env["customer.rating.history"]
        values = []
        for record in self:
            before_state = before_map.get(record.id) if before_map else None
            after_state = record._snapshot_state()
            if before_state and before_state == after_state:
                continue
            values.append(
                {
                    "rating_id": record.id,
                    "changed_by": self.env.user.id,
                    "change_type": change_type,
                    "before_score": before_state.get("rating", 0.0) if before_state else 0.0,
                    "after_score": after_state.get("rating", 0.0),
                    "summary": self._build_diff_summary(before_state, after_state, change_type),
                    "details_json": json.dumps({"before": before_state, "after": after_state}, ensure_ascii=True),
                }
            )
        if values:
            history_model.create(values)

    @api.onchange("final_criteria_id")
    def _onchange_final_criteria_id(self):
        if self.final_criteria_id:
            self.criteria_ids = [(5, 0, 0)] + self._prepare_template_lines(self.final_criteria_id)

    @api.onchange("customer_id")
    def _onchange_customer_id(self):
        if not self.final_criteria_id:
            self.final_criteria_id = self._get_default_template()
        if self.final_criteria_id and not self.criteria_ids:
            self.criteria_ids = [(5, 0, 0)] + self._prepare_template_lines(self.final_criteria_id)

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        template_id = vals.get("final_criteria_id") or self._get_default_template_id()
        if template_id and not vals.get("criteria_ids"):
            template = self.env["final.criteria"].browse(template_id)
            vals["final_criteria_id"] = template_id
            vals["criteria_ids"] = self._prepare_template_lines(template)
        return vals

    @api.depends("criteria_ids.score")
    def _compute_rating(self):
        for record in self:
            scores = [int(line.score) for line in record.criteria_ids if line.score]
            record.rating = sum(scores) / len(scores) if scores else 0.0

    @api.depends("rating")
    def _compute_rating_bucket(self):
        for record in self:
            val = record.rating or 0.0
            if val <= self._LOW_MAX:
                record.rating_bucket = "low"
            elif val <= self._MEDIUM_MAX:
                record.rating_bucket = "medium"
            else:
                record.rating_bucket = "high"

    @api.depends("rating")
    def _compute_rating_stars(self):
        for record in self:
            record.rating_stars = self.render_stars_html(record.rating)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            template_id = vals.get("final_criteria_id") or self._get_default_template_id()
            if template_id:
                vals.setdefault("final_criteria_id", template_id)
                if not vals.get("criteria_ids"):
                    template = self.env["final.criteria"].browse(template_id)
                    vals["criteria_ids"] = self._prepare_template_lines(template)
        records = super(CustomerRating, self).create(vals_list)
        records._sync_from_template()
        records._log_history("create")
        return records

    def write(self, vals):
        tracked = {"criteria_ids", "notes", "final_criteria_id", "customer_id"}
        before_map = self._snapshot_map() if tracked.intersection(vals.keys()) else None
        result = super(CustomerRating, self).write(vals)
        if "final_criteria_id" in vals or "criteria_ids" in vals:
            self._sync_from_template()
        if before_map:
            self._log_history("manual_update", before_map)
        return result

    def action_sync_from_template(self):
        before_map = self._snapshot_map()
        self._sync_from_template()
        self._log_history("template_sync", before_map)
        return True


class CustomerRatingCriteria(models.Model):
    _name = "customer.rating.criteria"
    _description = "Customer Rating Criteria"

    rating_id = fields.Many2one("customer.rating", ondelete="cascade")
    customer_id = fields.Many2one("res.partner", string="Customer", required=True, index=True)
    template_line_id = fields.Many2one("final.criteria.line", ondelete="set null", index=True)
    name = fields.Char(string="Criteria", required=True)

    score = fields.Selection(
        [
            ("0", "0"),
            ("1", "1"),
            ("2", "2"),
            ("3", "3"),
            ("4", "4"),
            ("5", "5"),
        ],
        string="Score",
        default="0",
        required=True,
    )

    notes = fields.Char(string="Notes")

    def init(self):
        self._cr.execute(
            """
            UPDATE customer_rating_criteria c
            SET customer_id = r.customer_id
            FROM customer_rating r
            WHERE c.customer_id IS NULL
            AND c.rating_id = r.id
            AND r.customer_id IS NOT NULL
            """
        )

    @api.model_create_multi
    def create(self, vals_list):
        if isinstance(vals_list, dict):
            vals_list = [vals_list]
        for vals in vals_list:
            if not vals.get("customer_id") and vals.get("rating_id"):
                rating = self.env["customer.rating"].browse(vals["rating_id"])
                vals["customer_id"] = rating.customer_id.id
        records = super().create(vals_list)
        for record in records:
            if record.rating_id:
                record.rating_id._log_history("manual_update")
        return records

    def write(self, vals):
        # Snapshot parents before changes
        parents = self.mapped("rating_id")
        before_maps = {parent.id: parent._snapshot_map() for parent in parents}
        
        result = super().write(vals)
        
        # Log history for each unique parent
        for parent in parents:
            parent._log_history("manual_update", before_maps.get(parent.id))
        return result

    def unlink(self):
        # Snapshot parents before deletion
        parents = self.mapped("rating_id")
        before_maps = {parent.id: parent._snapshot_map() for parent in parents}
        
        result = super().unlink()
        
        # Log history for each unique parent
        for parent in parents:
            if parent.exists():
                parent._log_history("manual_update", before_maps.get(parent.id))
        return result

    _sql_constraints = [
        ("unique_template_line", "UNIQUE (rating_id, template_line_id)", "A template criterion can appear only once per customer rating."),
    ]


class CustomerRatingHistory(models.Model):
    _name = "customer.rating.history"
    _description = "Customer Rating History"
    _order = "changed_on desc, id desc"

    rating_id = fields.Many2one("customer.rating", required=True, ondelete="cascade")
    changed_by = fields.Many2one("res.users", string="Changed By", required=True, default=lambda self: self.env.user)
    changed_on = fields.Datetime(string="Changed On", required=True, default=fields.Datetime.now)
    change_type = fields.Selection(
        [
            ("create", "Create"),
            ("manual_update", "Manual Update"),
            ("template_sync", "Template Sync"),
            ("scheduler", "Scheduler"),
        ],
        required=True,
        default="manual_update",
    )
    before_score = fields.Float(string="Before Score", digits=(16, 2))
    after_score = fields.Float(string="After Score", digits=(16, 2))
    summary = fields.Text(required=True)
    details_json = fields.Text(string="Details (JSON)")
