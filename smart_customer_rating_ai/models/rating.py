import json
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomerRating(models.Model):
    _name = "customer.rating"
    _description = "Customer Rating"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    _LOW_MAX = 2.49
    _MEDIUM_MAX = 3.99

    customer_id = fields.Many2one(
        "res.partner", string="Customer", required=True, tracking=True,
    )
    notes = fields.Text(string="Notes", tracking=True)
    final_criteria_id = fields.Many2one(
        "final.criteria", string="Template",
        tracking=True,
    )
    criteria_ids = fields.One2many(
        "customer.rating.criteria", "rating_id", string="Criteria Lines",
    )
    insight_ids = fields.One2many(
        "ll.rating.insight", "rating_id", string="Rule Insights", readonly=True,
    )
    rating = fields.Float(
        string="Rating Score",
        compute="_compute_rating",
        store=True,
        digits=(16, 2),
        tracking=True,
    )
    # Stores the last score computed by the rules engine.
    # _compute_rating reads this for automatic ratings so the stored field
    # can be refreshed without triggering a recursive compute cycle.
    last_auto_score = fields.Float(
        string="Last Auto Score", digits=(16, 2), default=0.0,
    )
    rating_stars = fields.Html(
        string="Final Rating", compute="_compute_rating_stars", sanitize=False,
    )
    ai_persona_tag = fields.Char(string="AI Persona Tag", readonly=True, tracking=True)
    ai_persona_reason = fields.Char(string="AI Persona Reason", readonly=True)
    ai_suggested_star = fields.Float(string="AI Suggested Star", digits=(16, 2), readonly=True)
    ai_advisory_stars = fields.Html(
        string="AI Suggested Rating", compute="_compute_ai_advisory_stars", sanitize=False,
    )
    ai_provider_used = fields.Char(string="AI Provider Used", readonly=True)
    rating_bucket = fields.Selection(
        [("watchlist", "Watchlist"), ("rising", "Rising Star"), ("champion", "Champion")],
        string="Segment",
        compute="_compute_rating_bucket",
        store=True,
        index=True,
        tracking=True,
    )
    is_primary = fields.Boolean(string="Primary Rating", default=False, tracking=True)
    is_automatic = fields.Boolean(string="Is Automated", default=False, tracking=True)
    history_ids = fields.One2many(
        "customer.rating.history", "rating_id", string="Timeline", readonly=True,
    )

    _unique_customer_template = models.Constraint(
        "UNIQUE (customer_id, final_criteria_id)",
        "Only one rating of this template type is allowed per customer.",
    )

    def init(self):
        # Upgrade-safe migration from legacy low/medium/high buckets.
        self.env.cr.execute("""
            UPDATE customer_rating
               SET rating_bucket = CASE
                   WHEN rating_bucket = 'low' THEN 'watchlist'
                   WHEN rating_bucket = 'medium' THEN 'rising'
                   WHEN rating_bucket = 'high' THEN 'champion'
                   ELSE rating_bucket
               END
             WHERE rating_bucket IN ('low', 'medium', 'high')
        """)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @api.model
    def render_stars_html(self, value):
        val = value or 0.0
        full = int(val)
        half = 1 if (val - full) >= 0.5 else 0
        empty = 5 - full - half
        content = (
            "<i class='fa fa-star text-warning'></i>" * full
            + "<i class='fa fa-star-half-o text-warning'></i>" * half
            + "<i class='fa fa-star-o text-muted'></i>" * empty
        )
        return f"<span style='font-size:14px;white-space:nowrap;display:inline-block;'>{content}</span>"

    @api.model
    def get_rating_map(self, partner_ids):
        partner_ids = [pid for pid in partner_ids if pid]
        if not partner_ids:
            return {}
        ratings = self.search([("customer_id", "in", partner_ids)])
        return {r.customer_id.id: r for r in ratings}

    @api.model
    def _get_default_template(self):
        template = self.env["final.criteria"].search(
            [("name", "ilike", "criteria")], limit=1
        )
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
        return [
            (0, 0, self._prepare_template_line_vals(line, default_score=default_score))
            for line in template.line_ids
        ]

    # -------------------------------------------------------------------------
    # Template sync
    # -------------------------------------------------------------------------

    def _sync_from_template(self):
        criteria_model = self.env["customer.rating.criteria"]
        for rating in self:
            template = rating.final_criteria_id
            if not template:
                continue

            template_lines = template.line_ids
            template_line_ids = set(template_lines.ids)

            # Build name-based lookup for unlinked criteria
            template_by_name = {}
            duplicate_names = set()
            for t_line in template_lines:
                key = (t_line.name or "").strip().lower()
                if key in template_by_name:
                    duplicate_names.add(key)
                else:
                    template_by_name[key] = t_line

            for crit in rating.criteria_ids.filtered(lambda r: not r.template_line_id):
                key = (crit.name or "").strip().lower()
                if not key or key in duplicate_names or key not in template_by_name:
                    continue
                target = template_by_name[key]
                if not rating.criteria_ids.filtered(lambda r: r.template_line_id == target):
                    crit.write({"template_line_id": target.id, "name": target.name})

            linked = rating.criteria_ids.filtered(lambda r: r.template_line_id)
            linked_by_tid = {l.template_line_id.id: l for l in linked if l.template_line_id}

            # Remove obsolete lines
            obsolete = linked.filtered(lambda r: r.template_line_id.id not in template_line_ids)
            if obsolete:
                obsolete.unlink()

            # Add / update
            for t_line in template_lines:
                existing = linked_by_tid.get(t_line.id)
                if existing:
                    if existing.name != t_line.name:
                        existing.write({"name": t_line.name})
                else:
                    vals = self._prepare_template_line_vals(t_line)
                    vals["rating_id"] = rating.id
                    vals["customer_id"] = rating.customer_id.id
                    criteria_model.create(vals)

    # -------------------------------------------------------------------------
    # History helpers
    # -------------------------------------------------------------------------

    def _snapshot_state(self):
        self.ensure_one()
        criteria_rows = []
        for line in self.criteria_ids.sorted(
            lambda r: ((r.template_line_id.id or 0), r.id)
        ):
            criteria_rows.append({
                "line_id": line.id,
                "template_line_id": line.template_line_id.id or False,
                "name": line.name or "",
                "score": line.score or 0,
                "notes": line.notes or "",
            })
        return {
            "rating": round(self.rating or 0.0, 2),
            "template_id": self.final_criteria_id.id or False,
            "notes": self.notes or "",
            "criteria": criteria_rows,
        }

    def _snapshot_map(self):
        return {r.id: r._snapshot_state() for r in self}

    @api.model
    def _build_diff_summary(self, before, after, change_type):
        if not before:
            return _("Rating created. Score: %(score).2f, Criteria lines: %(count)s") % {
                "score": after["rating"],
                "count": len(after["criteria"]),
            }
        parts = []
        if before.get("rating") != after.get("rating"):
            parts.append(
                _("Score %(before).2f -> %(after).2f") % {
                    "before": before.get("rating", 0.0),
                    "after": after.get("rating", 0.0),
                }
            )
        before_ids = {r.get("template_line_id") for r in before.get("criteria", [])}
        after_ids = {r.get("template_line_id") for r in after.get("criteria", [])}
        added = len(after_ids - before_ids)
        removed = len(before_ids - after_ids)
        if added or removed:
            parts.append(
                _("Criteria changes: +%(added)s / -%(removed)s") % {
                    "added": added, "removed": removed,
                }
            )
        if before.get("notes") != after.get("notes"):
            parts.append(_("Notes updated"))
        if not parts:
            parts.append(_("No score delta (%s)") % change_type)
        return "; ".join(parts)

    def _log_history(self, change_type, before_map=None):
        values = []
        for record in self:
            before = before_map.get(record.id) if before_map else None
            after = record._snapshot_state()
            if before and before == after:
                continue
            values.append({
                "rating_id": record.id,
                "changed_by": self.env.user.id,
                "change_type": change_type,
                "before_score": before.get("rating", 0.0) if before else 0.0,
                "after_score": after.get("rating", 0.0),
                "summary": self._build_diff_summary(before, after, change_type),
                "details_json": json.dumps(
                    {"before": before, "after": after}, ensure_ascii=True
                ),
            })
        if values:
            self.env["customer.rating.history"].create(values)

    # -------------------------------------------------------------------------
    # Onchange / defaults
    # -------------------------------------------------------------------------

    @api.onchange("final_criteria_id")
    def _onchange_final_criteria_id(self):
        if self.final_criteria_id:
            self.criteria_ids = [(5, 0, 0)] + self._prepare_template_lines(
                self.final_criteria_id
            )

    @api.onchange("customer_id")
    def _onchange_customer_id(self):
        if not self.final_criteria_id:
            self.final_criteria_id = self._get_default_template()
        if self.final_criteria_id and not self.criteria_ids:
            self.criteria_ids = [(5, 0, 0)] + self._prepare_template_lines(
                self.final_criteria_id
            )

    @api.model
    def default_get(self, fields_list):
        return super().default_get(fields_list)

    # -------------------------------------------------------------------------
    # Constraints
    # -------------------------------------------------------------------------

    @api.constrains('is_primary', 'customer_id')
    def _check_single_primary(self):
        for record in self:
            if record.is_primary:
                domain = [
                    ('customer_id', '=', record.customer_id.id),
                    ('is_primary', '=', True),
                    ('id', '!=', record.id),
                ]
                if self.search_count(domain) > 0:
                    raise UserError(
                        _("Only one rating can be marked as primary per customer.")
                    )

    @api.onchange('is_primary')
    def _onchange_is_primary(self):
        if self.is_primary:
            other = self.search([
                ('customer_id', '=', self.customer_id.id),
                ('is_primary', '=', True),
                ('id', '!=', self._origin.id if hasattr(self, '_origin') else self.id),
            ])
            if other:
                other.write({'is_primary': False})

    # -------------------------------------------------------------------------
    # Computed fields
    # -------------------------------------------------------------------------

    @api.depends("criteria_ids.score", "is_automatic", "last_auto_score", "customer_id")
    def _compute_rating(self):
        for record in self:
            if record.is_automatic:
                # Reads from last_auto_score which is written by recompute_automatic_rating().
                # This avoids side-effects inside a stored compute method.
                record.rating = record.last_auto_score
            else:
                scores = [float(l.score) for l in record.criteria_ids if l.score]
                record.rating = sum(scores) / len(scores) if scores else 0.0

    @api.depends("rating", "customer_id", "is_automatic", "last_auto_score")
    def _compute_rating_bucket(self):
        rule_model = self.env["ll.rating.rule"]
        for record in self:
            metrics = {}
            if record.customer_id:
                metrics = rule_model._get_partner_metrics(record.customer_id)

            risk = float(metrics.get("risk_score", 0.0) or 0.0)
            loyalty = float(metrics.get("loyalty_score", 0.0) or 0.0)
            health = float(metrics.get("financial_health", 0.0) or 0.0)

            # AI-led segmentation where AI scores are available.
            if risk or loyalty or health:
                if risk <= 35.0 and loyalty >= 65.0 and health >= 65.0:
                    record.rating_bucket = "champion"
                elif risk >= 70.0 or health < 40.0:
                    record.rating_bucket = "watchlist"
                else:
                    record.rating_bucket = "rising"
                continue

            # Fallback segmentation (works even when AI is disabled).
            val = record.rating or 0.0
            if val <= self._LOW_MAX:
                record.rating_bucket = "watchlist"
            elif val <= self._MEDIUM_MAX:
                record.rating_bucket = "rising"
            else:
                record.rating_bucket = "champion"

    @api.depends("rating")
    def _compute_rating_stars(self):
        for record in self:
            record.rating_stars = self.render_stars_html(record.rating)

    @api.depends("ai_suggested_star")
    def _compute_ai_advisory_stars(self):
        for record in self:
            record.ai_advisory_stars = self.render_stars_html(record.ai_suggested_star or 0.0)

    # -------------------------------------------------------------------------
    # Rules engine
    # -------------------------------------------------------------------------

    def _run_rules_engine(self):
        """
        Evaluate all active rules against self.customer_id.
        Rebuilds insight lines and returns the 0-5 star score.
        """
        self.ensure_one()
        rules = self.env['ll.rating.rule'].search([('active', '=', True)])
        if not rules:
            self.insight_ids.unlink()
            return 0.0

        total_weighted_score = 0.0
        total_weight = 0.0
        insight_vals = []
        rule_model = self.env["ll.rating.rule"]
        metrics = rule_model._get_partner_metrics(self.customer_id)

        for rule in rules:
            points, metric_val, met, ai_provider_used = rule._evaluate(self.customer_id, metrics=metrics)
            total_weighted_score += points * rule.weight
            total_weight += rule.weight * 100  # max possible per rule
            insight_vals.append({
                'rating_id': self.id,
                'rule_id': rule.id,
                'metric_value': metric_val,
                'points_awarded': points,
                'threshold_met': met,
                'ai_provider': ai_provider_used,
            })

        # Replace insights atomically
        self.insight_ids.unlink()
        self.env['ll.rating.insight'].create(insight_vals)

        # Advisory AI outputs for user-facing intelligence labels.
        self.write({
            "ai_persona_tag": metrics.get("persona_tag", ""),
            "ai_persona_reason": metrics.get("persona_reason", ""),
            "ai_suggested_star": metrics.get("suggested_star", 0.0),
            "ai_provider_used": metrics.get("ai_provider_used", ""),
        })

        return (total_weighted_score / total_weight * 5.0) if total_weight else 0.0

    def recompute_automatic_rating(self):
        """
        Public method to recompute an automatic rating and persist the result.
        Writes last_auto_score which triggers _compute_rating via @api.depends.
        Safe to call from wizard and cron.
        """
        config = self.env['ir.config_parameter'].sudo()
        mode = config.get_param('smart_customer_rating_ai.ai_advisory_mode', 'advisory')
        raw_w = config.get_param('smart_customer_rating_ai.ai_blend_weight', '0.2')
        try:
            blend_w = float(raw_w)
        except Exception:
            blend_w = 0.2
        blend_w = max(0.0, min(1.0, blend_w))

        for record in self.filtered('is_automatic'):
            before_map = record._snapshot_map()
            rules_score = record._run_rules_engine()
            final_score = rules_score

            if mode == 'blend':
                ai_star = float(record.ai_suggested_star or 0.0)
                final_score = ((1.0 - blend_w) * rules_score) + (blend_w * ai_star)

            # Writing last_auto_score triggers _compute_rating -> rating update
            record.write({'last_auto_score': final_score})
            record._log_history("scheduler", before_map)

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Automatic ratings are rules-based — no template, no criteria lines
            if vals.get('is_automatic'):
                vals.pop('final_criteria_id', None)
                vals.pop('criteria_ids', None)
                continue
            template_id = vals.get("final_criteria_id") or self._get_default_template_id()
            if template_id:
                vals.setdefault("final_criteria_id", template_id)
                if not vals.get("criteria_ids"):
                    template = self.env["final.criteria"].browse(template_id)
                    vals["criteria_ids"] = self._prepare_template_lines(template)
        records = super().create(vals_list)
        records.filtered(lambda r: not r.is_automatic)._sync_from_template()
        records._log_history("create")
        return records

    def write(self, vals):
        tracked = {"criteria_ids", "notes", "final_criteria_id", "customer_id"}
        before_map = self._snapshot_map() if tracked.intersection(vals.keys()) else None
        result = super().write(vals)
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

    def action_clear_automated_rating(self):
        """Clear all automated rating data including AI values and cache."""
        self.ensure_one()
        
        # Take snapshot before clearing for change tracking
        before_map = self._snapshot_map()
        
        # Clear AI values from the rating record
        self.write({
            'ai_persona_tag': False,
            'ai_persona_reason': False,
            'ai_suggested_star': 0.0,
            'ai_provider_used': False,
            'last_auto_score': 0.0,
        })
        
        # Clear the metrics cache for this partner
        cache_model = self.env['ll.partner.metrics.cache'].sudo()
        cache_records = cache_model.search([('partner_id', '=', self.customer_id.id)])
        if cache_records:
            cache_records.unlink()
        
        # Clear AI insights
        self.insight_ids.unlink()
        
        # Log the clear action
        self._log_history("clear_automated", before_map)
        
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_recompute_now(self):
        """Button action: recompute this automatic rating immediately."""
        self.ensure_one()
        old_rating = self.rating
        old_ai_star = self.ai_suggested_star
        self.recompute_automatic_rating()
        
        # Show notification with changes
        message = _('Rating recomputed.')
        if old_rating != self.rating:
            message += _(' Score: %.1f → %.1f') % (old_rating, self.rating)
        if old_ai_star != self.ai_suggested_star:
            message += _(' AI Star: %.1f → %.1f') % (old_ai_star, self.ai_suggested_star)
            
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
            'params': {
                'notification': {
                    'title': _('Done'),
                    'message': message,
                    'type': 'success',
                    'sticky': False,
                }
            }
        }

    # -------------------------------------------------------------------------
    # Cron
    # -------------------------------------------------------------------------

    @api.model
    def cron_recompute_ratings(self):
        config = self.env['ir.config_parameter'].sudo()
        if not config.get_param('smart_customer_rating_ai.auto_recompute', False):
            return
        self.search([('is_automatic', '=', True)]).recompute_automatic_rating()

    @api.model
    def cron_recompute_metrics(self):
        """
        Daily metrics refresh helper.
        Uses ll.rating.rule cache strategy; does not crash on partial failures.
        """
        rule_model = self.env["ll.rating.rule"]
        partners = self.env["res.partner"].search([("customer_rank", ">", 0)])
        for partner in partners:
            rule_model._get_partner_metrics(partner)
        self.cron_recompute_ratings()


class CustomerRatingCriteria(models.Model):
    _name = "customer.rating.criteria"
    _description = "Customer Rating Criteria"

    rating_id = fields.Many2one("customer.rating", ondelete="cascade")
    customer_id = fields.Many2one(
        "res.partner", string="Customer", required=True, index=True,
    )
    template_line_id = fields.Many2one(
        "final.criteria.line", ondelete="set null", index=True,
    )
    name = fields.Char(string="Criteria", required=True)
    score = fields.Selection(
        [("0", "0"), ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"), ("5", "5")],
        string="Score", default="0", required=True,
    )
    notes = fields.Char(string="Notes")

    _unique_template_line = models.Constraint(
        "UNIQUE (rating_id, template_line_id)",
        "A template criterion can appear only once per customer rating.",
    )

    def init(self):
        self.env.cr.execute("""
            UPDATE customer_rating_criteria c
            SET customer_id = r.customer_id
            FROM customer_rating r
            WHERE c.customer_id IS NULL
              AND c.rating_id = r.id
              AND r.customer_id IS NOT NULL
        """)

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
        parents = self.mapped("rating_id")
        before_maps = {p.id: p._snapshot_map() for p in parents}
        result = super().write(vals)
        for parent in parents:
            parent._log_history("manual_update", before_maps.get(parent.id))
        return result

    def unlink(self):
        parents = self.mapped("rating_id")
        before_maps = {p.id: p._snapshot_map() for p in parents}
        result = super().unlink()
        for parent in parents:
            if parent.exists():
                parent._log_history("manual_update", before_maps.get(parent.id))
        return result


class CustomerRatingHistory(models.Model):
    _name = "customer.rating.history"
    _description = "Customer Rating History"
    _order = "changed_on desc, id desc"

    rating_id = fields.Many2one("customer.rating", required=True, ondelete="cascade")
    changed_by = fields.Many2one(
        "res.users", string="Changed By", required=True,
        default=lambda self: self.env.user,
    )
    changed_on = fields.Datetime(
        string="Changed On", required=True, default=fields.Datetime.now,
    )
    change_type = fields.Selection(
        [
            ("create", "Create"),
            ("manual_update", "Manual Update"),
            ("template_sync", "Template Sync"),
            ("clear_automated", "Clear Automated"),
            ("scheduler", "Scheduler"),
        ],
        required=True, default="manual_update",
    )
    before_score = fields.Float(string="Before Score", digits=(16, 2))
    after_score = fields.Float(string="After Score", digits=(16, 2))
    summary = fields.Text(required=True)
    details_json = fields.Text(string="Details (JSON)")


class RatingInsight(models.Model):
    _name = "ll.rating.insight"
    _description = "Rating Rule Insight"
    _order = "rule_id"

    rating_id = fields.Many2one(
        "customer.rating", required=True, ondelete="cascade", index=True,
    )
    rule_id = fields.Many2one(
        "ll.rating.rule", string="Rule", required=True, ondelete="cascade",
    )
    rule_category = fields.Selection(
        related="rule_id.category", string="Category", store=True,
    )
    metric_value = fields.Float(string="Measured Value", digits=(16, 2))
    ai_provider = fields.Char(string="AI Provider")
    points_awarded = fields.Integer(string="Points Awarded")
    threshold_met = fields.Boolean(string="Threshold Met")

    @property
    def result_label(self):
        if self.threshold_met:
            return f"Met {self.rule_id.name}: +{self.points_awarded} pts"
        return f"Missed {self.rule_id.name}: +0 pts"
