from odoo import _, api, fields, models


class RatingRecomputeWizard(models.TransientModel):
    _name = "ll.rating.recompute.wizard"
    _description = "Rating Recompute Wizard"

    partner_ids = fields.Many2many(
        "res.partner",
        string="Customers",
        default=lambda self: self._context.get('active_ids', []),
        help="Leave empty to recompute all customers that have an automatic rating.",
    )

    def action_recompute(self):
        self.ensure_one()
        if self.partner_ids:
            # Ensure each selected partner has an automatic rating record
            for partner in self.partner_ids:
                partner._ensure_automatic_rating()
            ratings = self.env['customer.rating'].search([
                ('customer_id', 'in', self.partner_ids.ids),
                ('is_automatic', '=', True),
            ])
        else:
            ratings = self.env['customer.rating'].search([('is_automatic', '=', True)])

        ratings.recompute_automatic_rating()

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Done'),
                'message': _('Ratings recomputed for %s record(s).') % len(ratings),
                'type': 'success',
                'sticky': False,
            },
        }
