from odoo import fields, models


class FinalCriteriaLineDeleteWizard(models.TransientModel):
    _name = "final.criteria.line.delete.wizard"
    _description = "Confirm Final Criteria Line Delete"

    line_id = fields.Many2one("final.criteria.line", required=True, readonly=True)

    def action_confirm(self):
        self.ensure_one()
        if self.line_id:
            self.line_id.unlink()
        return {"type": "ir.actions.act_window_close"}

    def action_cancel(self):
        return {"type": "ir.actions.act_window_close"}
