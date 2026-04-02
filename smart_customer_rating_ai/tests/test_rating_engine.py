from dateutil.relativedelta import relativedelta
from odoo import fields
from odoo.tests.common import TransactionCase


class TestRatingEngine(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Partner = self.env['res.partner']
        self.Rule = self.env['ll.rating.rule']
        self.Rating = self.env['customer.rating']

        self.partner = self.Partner.create({'name': 'Test Customer'})

        # Total revenue: 6000, Total Qty: 15, AUP: 400
        self._create_invoice(self.partner, 1000, 10)
        self._create_invoice(self.partner, 5000, 5)

    def _create_invoice(self, partner, amount, qty):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [(0, 0, {
                'name': 'Test Line',
                'quantity': qty,
                'price_unit': amount / qty if qty else amount,
            })],
        })
        invoice.action_post()
        return invoice

    # _evaluate now returns (points, metric_value, threshold_met)
    def _points(self, rule, partner):
        points, _val, _met, _ai_provider = rule._evaluate(partner)
        return points

    def test_value_metrics(self):
        rule_revenue = self.Rule.create({
            'name': 'High Revenue', 'category': 'value',
            'metric_key': 'revenue', 'threshold_type': 'gt',
            'threshold_min': 5000, 'score': 100, 'weight': 1.0,
        })
        rule_aup = self.Rule.create({
            'name': 'High AUP', 'category': 'value',
            'metric_key': 'aup', 'threshold_type': 'gt',
            'threshold_min': 300, 'score': 100, 'weight': 1.0,
        })

        self.assertEqual(self._points(rule_revenue, self.partner), 100,
                         "Revenue 6000 should be > 5000")
        self.assertEqual(self._points(rule_aup, self.partner), 100,
                         "AUP 400 should be > 300")

    def test_financial_behavioral_metrics(self):
        invoice1 = self._create_invoice(self.partner, 1000, 1)
        # Backdate invoice 20 days
        self.env.cr.execute(
            "UPDATE account_move SET invoice_date = %s WHERE id = %s",
            (fields.Date.today() - relativedelta(days=20), invoice1.id),
        )
        invoice1.invalidate_recordset(['invoice_date'])

        # Pay it today → DSO ≈ 20 days
        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice1.ids
        ).create({'payment_date': fields.Date.today()})._create_payments()

        rule_dso = self.Rule.create({
            'name': 'Fast Payer', 'category': 'financial',
            'metric_key': 'dso', 'threshold_type': 'lt',
            'threshold_min': 30, 'score': 100, 'weight': 1.0,
        })
        self.assertEqual(self._points(rule_dso, self.partner), 100,
                         "DSO ~20 days should be < 30")

        # Refund 600 of 6000 = 10% return rate
        refund = self.env['account.move'].create({
            'move_type': 'out_refund',
            'partner_id': self.partner.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [(0, 0, {
                'name': 'Refund', 'quantity': 1, 'price_unit': 600,
            })],
        })
        refund.action_post()

        rule_returns = self.Rule.create({
            'name': 'High Returns', 'category': 'behavioral',
            'metric_key': 'returns', 'threshold_type': 'gt',
            'threshold_min': 5, 'score': 100, 'weight': 1.0,
        })
        self.assertEqual(self._points(rule_returns, self.partner), 100,
                         "Return rate 10% should be > 5%")

    def test_automatic_rating_and_insights(self):
        """End-to-end: create auto rating, recompute, verify insights."""
        self.partner._ensure_automatic_rating()
        auto = self.partner.customer_rating_ids.filtered('is_automatic')
        self.assertTrue(auto, "Automatic rating should be created")
        self.assertFalse(auto.final_criteria_id,
                         "Automatic rating should have no template")

        # Activate only the revenue rule for a clean test
        self.env['ll.rating.rule'].search([]).write({'active': False})
        rule = self.Rule.create({
            'name': 'Test Revenue', 'category': 'value',
            'metric_key': 'revenue', 'threshold_type': 'gt',
            'threshold_min': 5000, 'score': 100, 'weight': 1.0,
            'active': True,
        })

        auto.recompute_automatic_rating()

        self.assertEqual(len(auto.insight_ids), 1)
        insight = auto.insight_ids[0]
        self.assertEqual(insight.rule_id, rule)
        self.assertTrue(insight.threshold_met)
        self.assertEqual(insight.points_awarded, 100)
        self.assertGreater(auto.rating, 0)
        self.assertTrue(auto.history_ids)
