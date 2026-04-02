import re
import logging
import requests
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class RatingRule(models.Model):
    _name = "ll.rating.rule"
    _description = "Customer Rating Rule"
    _order = "sequence, id"

    name = fields.Char(string="Rule Name", required=True, translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    category = fields.Selection([
        ('value', 'Value & Exclusivity'),
        ('financial', 'Financial & Risk'),
        ('behavioral', 'Behavioral & Cost'),
        ('ai', 'AI Intelligence'),
    ], string="Category", required=True, default='value')

    metric_key = fields.Selection([
        ('revenue', 'Total Revenue'),
        ('aup', 'Average Unit Price (AUP)'),
        ('quantity', 'Total Quantity Sold'),
        ('frequency', 'Order Frequency'),
        ('dso', 'Days Sales Outstanding (DSO)'),
        ('overdue', 'Overdue Amount'),
        ('returns', 'Return Rate (%)'),
        ('sentiment', 'AI Sentiment Score'),
    ], string="Metric", required=True)

    description = fields.Text(string="Description", compute="_compute_description")

    threshold_type = fields.Selection([
        ('gt', 'Greater than'),
        ('lt', 'Less than'),
        ('between', 'Between'),
    ], string="Condition", required=True, default='gt')

    threshold_min = fields.Float(string="Min Value / Threshold")
    threshold_max = fields.Float(string="Max Value")

    score = fields.Integer(
        string="Points Awarded", default=100,
        help="Points granted if threshold is met (0-100)"
    )
    weight = fields.Float(
        string="Impact Weight", default=1.0,
        help="Multiplier for the score in final calculation"
    )

    @api.depends('metric_key')
    def _compute_description(self):
        descriptions = {
            'revenue': 'Total untaxed revenue from all posted invoices.',
            'aup': 'Average Unit Price (Total Revenue / Total Quantity). Identifies exclusive buyers.',
            'quantity': 'Total quantity of items purchased across all orders.',
            'frequency': 'Number of sales orders in the last 12 months.',
            'dso': 'Average days between invoice date and payment date.',
            'overdue': 'Total amount currently overdue.',
            'returns': 'Percentage of total invoices that were credited or returned.',
            'sentiment': 'Sentiment analysis of CRM and Helpdesk notes (0-100 score).',
        }
        for rec in self:
            rec.description = descriptions.get(rec.metric_key, '')

    def _get_metric_value(self, partner):
        """Compute and return the raw metric value for a partner."""
        self.ensure_one()
        if self.metric_key == 'revenue':
            return partner.total_invoiced

        elif self.metric_key == 'aup':
            invoices = self.env['account.move'].search([
                ('partner_id', '=', partner.id),
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
            ])
            revenue = sum(invoices.mapped('amount_untaxed'))
            qty = sum(invoices.mapped('invoice_line_ids.quantity'))
            return revenue / qty if qty else 0.0

        elif self.metric_key == 'quantity':
            invoices = self.env['account.move'].search([
                ('partner_id', '=', partner.id),
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
            ])
            return sum(invoices.mapped('invoice_line_ids.quantity'))

        elif self.metric_key == 'frequency':
            cutoff = fields.Date.today() - relativedelta(months=12)
            return float(self.env['sale.order'].search_count([
                ('partner_id', '=', partner.id),
                ('state', 'in', ('sale', 'done')),
                ('date_order', '>=', cutoff),
            ]))

        elif self.metric_key == 'dso':
            invoices = self.env['account.move'].search([
                ('partner_id', '=', partner.id),
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('payment_state', 'in', ('paid', 'in_payment')),
            ])
            if not invoices:
                return 0.0
            days = []
            for inv in invoices:
                rec_lines = inv.line_ids.filtered(
                    lambda l: l.account_type in ('asset_receivable', 'liability_payable')
                )
                # Collect payment dates from partial reconciles (Odoo 19 API)
                payment_dates = (
                    rec_lines.matched_debit_ids.mapped('max_date')
                    + rec_lines.matched_credit_ids.mapped('max_date')
                )
                if payment_dates and inv.invoice_date:
                    days.append((max(payment_dates) - inv.invoice_date).days)
            return sum(days) / len(days) if days else 0.0

        elif self.metric_key == 'overdue':
            # Compute overdue amount directly from invoices/refunds.
            # Some databases do not provide `res.partner.total_overdue`, so we
            # avoid relying on that field.
            today = fields.Date.today()

            invoices = self.env["account.move"].search([
                ("partner_id", "=", partner.id),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("invoice_date_due", "!=", False),
                ("invoice_date_due", "<", today),
                ("payment_state", "not in", ("paid", "in_payment")),
            ])
            refunds = self.env["account.move"].search([
                ("partner_id", "=", partner.id),
                ("move_type", "=", "out_refund"),
                ("state", "=", "posted"),
                ("invoice_date_due", "!=", False),
                ("invoice_date_due", "<", today),
                ("payment_state", "not in", ("paid", "in_payment")),
            ])
            inv_total = sum(invoices.mapped("amount_residual"))
            ref_total = sum(refunds.mapped("amount_residual"))
            # Avoid negative values if refunds fully compensate invoices.
            return max(inv_total - ref_total, 0.0)

        elif self.metric_key == 'returns':
            invoices = self.env['account.move'].search([
                ('partner_id', '=', partner.id),
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
            ])
            refunds = self.env['account.move'].search([
                ('partner_id', '=', partner.id),
                ('move_type', '=', 'out_refund'),
                ('state', '=', 'posted'),
            ])
            inv_total = sum(invoices.mapped('amount_untaxed'))
            ref_total = sum(refunds.mapped('amount_untaxed'))
            return (ref_total / inv_total * 100) if inv_total else 0.0

        elif self.metric_key == 'sentiment':
            score, _ai_provider = self._fetch_ai_sentiment(partner)
            return score

        return 0.0

    def _evaluate(self, partner):
        """
        Evaluate the rule against a partner.
        Returns (points_awarded, metric_value, threshold_met, ai_provider_used).
        """
        self.ensure_one()

        if self.metric_key == 'sentiment':
            val, ai_provider_used = self._fetch_ai_sentiment(partner)
        else:
            val = self._get_metric_value(partner)
            ai_provider_used = False

        if self.threshold_type == 'gt':
            met = val > self.threshold_min
        elif self.threshold_type == 'lt':
            met = val < self.threshold_min
        elif self.threshold_type == 'between':
            met = self.threshold_min <= val <= self.threshold_max
        else:
            met = False

        return (self.score if met else 0, val, met, ai_provider_used)

    def _fetch_ai_sentiment(self, partner):
        """Call the configured AI provider and return (score, ai_provider_string)."""
        config = self.env['ir.config_parameter'].sudo()
        provider = config.get_param('smart_customer_rating_ai.ai_provider', 'none')
        if provider == 'none':
            return 0.0, 'none'

        messages = self.env['mail.message'].search([
            ('res_id', '=', partner.id),
            ('model', '=', 'res.partner'),
            ('message_type', '=', 'comment'),
        ], limit=5, order='id desc')

        if not messages:
            # We skip the external request if there is no input text.
            return 50.0, f"{provider}:no_messages"

        text_content = re.sub(r'<[^>]+>', '', ' '.join(messages.mapped('body')) or '')
        prompt = (
            "Analyze the sentiment of the following customer communication on a scale of 0 to 100, "
            "where 0 is very negative and 100 is very positive. Return ONLY the number.\n\n"
            f"Content: {text_content}"
        )

        try:
            if provider == 'ollama':
                url = config.get_param('smart_customer_rating_ai.ollama_url', 'http://localhost:11434')
                model = config.get_param('smart_customer_rating_ai.ollama_model', 'llama3')
                resp = requests.post(f"{url}/api/generate", json={
                    "model": model, "prompt": prompt, "stream": False,
                }, timeout=10)
                if resp.status_code == 200:
                    m = re.search(r'\d+', resp.json().get('response', ''))
                    score = float(m.group()) if m else 50.0
                    ai_provider_used = f"ollama:{model}"
                    _logger.info(
                        "AI sentiment via %s for partner %s => %s",
                        ai_provider_used, partner.id, score,
                    )
                    return score, ai_provider_used
                return 50.0, f"ollama:{model}:http_{resp.status_code}"

            elif provider == 'openai':
                key = config.get_param('smart_customer_rating_ai.openai_api_key')
                if not key:
                    return 50.0, "openai:no_key"
                resp = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}]},
                    timeout=10,
                )
                if resp.status_code == 200:
                    m = re.search(r'\d+', resp.json()['choices'][0]['message']['content'])
                    score = float(m.group()) if m else 50.0
                    ai_provider_used = "openai:gpt-4o"
                    _logger.info(
                        "AI sentiment via %s for partner %s => %s",
                        ai_provider_used, partner.id, score,
                    )
                    return score, ai_provider_used
                return 50.0, f"openai:gpt-4o:http_{resp.status_code}"

            elif provider == 'gemini':
                key = config.get_param('smart_customer_rating_ai.gemini_api_key')
                if not key:
                    return 50.0, "gemini:no_key"
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={key}"
                resp = requests.post(url, json={
                    "contents": [{"parts": [{"text": prompt}]}]
                }, timeout=10)
                if resp.status_code == 200:
                    m = re.search(r'\d+', resp.json()['candidates'][0]['content']['parts'][0]['text'])
                    score = float(m.group()) if m else 50.0
                    ai_provider_used = "gemini:pro"
                    _logger.info(
                        "AI sentiment via %s for partner %s => %s",
                        ai_provider_used, partner.id, score,
                    )
                    return score, ai_provider_used
                return 50.0, f"gemini:pro:http_{resp.status_code}"

        except Exception as e:
            _logger.error("AI Sentiment failed for partner %s: %s", partner.id, e)
        return 50.0, provider
