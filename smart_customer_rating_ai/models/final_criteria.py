from odoo import _, api, fields, models
from odoo.exceptions import UserError


class FinalCriteria(models.Model):
    _name = "final.criteria"
    _description = "Assessment"
    _order = "name"

    name = fields.Char(string="Criteria", required=True)
    line_ids = fields.One2many("final.criteria.line", "final_id", string="Criteria")
    criteria_count = fields.Integer(string="# Criteria", compute="_compute_criteria_count", store=True)

    def init(self):
        super().init()
        # Ensure the default assessment record always exists.
        self._cr.execute(
            """
            INSERT INTO final_criteria (name)
            SELECT 'Criteria'
            WHERE NOT EXISTS (
                SELECT 1 FROM final_criteria WHERE lower(name) = 'criteria'
            )
            """
        )

    def _compute_criteria_count(self):
        for rec in self:
            rec.criteria_count = len(rec.line_ids)

    def unlink(self):
        protected = self.filtered(lambda rec: (rec.name or "").strip().lower() == "criteria")
        if protected:
            raise UserError(_("The 'Criteria' assessment cannot be deleted."))
        return super().unlink()


class FinalCriteriaLine(models.Model):
    _name = "final.criteria.line"
    _description = "Assessment Line"
    _order = "name"

    final_id = fields.Many2one("final.criteria", string="Assessment", ondelete="cascade", required=True)
    name = fields.Char(string="Criteria", required=True)

    def action_open_delete_warning(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Delete Criteria",
            "res_model": "final.criteria.line.delete.wizard",
            "view_mode": "form",
            "view_id": self.env.ref("smart_customer_rating_ai.view_final_criteria_line_delete_wizard_form").id,
            "target": "new",
            "context": {"default_line_id": self.id},
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        ratings = self.env["customer.rating"].search([("final_criteria_id", "in", records.mapped("final_id").ids)])
        before_map = ratings._snapshot_map() if ratings else {}
        ratings._sync_from_template()
        if ratings:
            ratings._log_history("template_sync", before_map)
        return records

    def write(self, vals):
        old_template_ids = self.mapped("final_id").ids
        result = super().write(vals)
        if "name" in vals or "final_id" in vals:
            all_template_ids = list(set(old_template_ids + self.mapped("final_id").ids))
            ratings = self.env["customer.rating"].search([("final_criteria_id", "in", all_template_ids)])
            before_map = ratings._snapshot_map() if ratings else {}
            ratings._sync_from_template()
            if ratings:
                ratings._log_history("template_sync", before_map)
        return result

    def unlink(self):
        template_ids = self.mapped("final_id").ids
        line_ids = self.ids
        rating_lines = self.env["customer.rating.criteria"].search([("template_line_id", "in", line_ids)])
        if rating_lines:
            rating_lines.unlink()
        result = super().unlink()
        ratings = self.env["customer.rating"].search([("final_criteria_id", "in", template_ids)])
        before_map = ratings._snapshot_map() if ratings else {}
        ratings._sync_from_template()
        if ratings:
            ratings._log_history("template_sync", before_map)
        return result
