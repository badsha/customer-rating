import json
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
        # New deterministic metrics
        ('avg_payment_delay_days', 'Avg Payment Delay (Days)'),
        ('late_payment_ratio', 'Late Payment Ratio'),
        ('total_revenue', 'Total Revenue'),
        ('outstanding_amount', 'Outstanding Amount'),
        ('credit_utilization_ratio', 'Credit Utilization Ratio'),
        ('order_frequency_12m', 'Order Frequency (12M)'),
        ('avg_order_value', 'Average Order Value'),
        ('last_order_days_ago', 'Last Order Days Ago'),
        ('refund_ratio', 'Refund Ratio'),
        ('dispute_count', 'Dispute Count'),
        ('overdue_invoices_count', 'Overdue Invoices Count'),
        ('customer_lifetime_days', 'Customer Lifetime (Days)'),
        ('repeat_purchase_ratio', 'Repeat Purchase Ratio'),
        # Optional AI enrichment metrics
        ('risk_score', 'AI Risk Score'),
        ('loyalty_score', 'AI Loyalty Score'),
        ('financial_health', 'AI Financial Health'),
        # Legacy aliases for backward compatibility
        ('revenue', 'Total Revenue (Legacy)'),
        ('aup', 'Average Unit Price (Legacy)'),
        ('quantity', 'Total Quantity Sold (Legacy)'),
        ('frequency', 'Order Frequency (Legacy)'),
        ('dso', 'Days Sales Outstanding (Legacy)'),
        ('overdue', 'Overdue Amount (Legacy)'),
        ('returns', 'Return Rate (Legacy)'),
        ('sentiment', 'AI Sentiment Score (Legacy)'),
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

    def init(self):
        # Upgrade-safe migration for legacy metric keys.
        self.env.cr.execute("""
            UPDATE ll_rating_rule
               SET metric_key = CASE metric_key
                   WHEN 'revenue' THEN 'total_revenue'
                   WHEN 'frequency' THEN 'order_frequency_12m'
                   WHEN 'returns' THEN 'refund_ratio'
                   WHEN 'overdue' THEN 'outstanding_amount'
                   WHEN 'sentiment' THEN 'financial_health'
                   ELSE metric_key
               END
             WHERE metric_key IN ('revenue', 'frequency', 'returns', 'overdue', 'sentiment')
        """)

    @api.depends('metric_key')
    def _compute_description(self):
        descriptions = {
            'avg_payment_delay_days': 'Average delay (in days) between due date and payment date.',
            'late_payment_ratio': 'Share of paid invoices settled after due date.',
            'total_revenue': 'Total untaxed posted invoice revenue.',
            'outstanding_amount': 'Current unpaid residual amount.',
            'credit_utilization_ratio': 'Outstanding amount divided by partner credit limit.',
            'order_frequency_12m': 'Number of confirmed sales orders in last 12 months.',
            'avg_order_value': 'Average total amount per confirmed sales order.',
            'last_order_days_ago': 'Days since last confirmed sales order.',
            'refund_ratio': 'Refund untaxed amount divided by invoice untaxed amount.',
            'dispute_count': 'Count of invoices in payment state "reversed".',
            'overdue_invoices_count': 'Count of unpaid invoices past due date.',
            'customer_lifetime_days': 'Days since first posted invoice or confirmed order.',
            'repeat_purchase_ratio': 'Share of repeat orders among all confirmed orders.',
            'risk_score': 'AI interpretation of structured ERP metrics (0-100).',
            'loyalty_score': 'AI interpretation of loyalty likelihood (0-100).',
            'financial_health': 'AI interpretation of financial strength (0-100).',
            'revenue': 'Legacy alias of Total Revenue.',
            'aup': 'Legacy average unit price metric.',
            'quantity': 'Legacy total sold quantity metric.',
            'frequency': 'Legacy alias of Order Frequency (12M).',
            'dso': 'Legacy average receivable settlement time.',
            'overdue': 'Legacy overdue amount metric.',
            'returns': 'Legacy alias of Refund Ratio.',
            'sentiment': 'Legacy AI metric mapped to Financial Health.',
        }
        for rec in self:
            rec.description = descriptions.get(rec.metric_key, '')

    # -------------------------------------------------------------------------
    # Metrics core
    # -------------------------------------------------------------------------

    @api.model
    def _empty_metrics(self):
        return {
            "avg_payment_delay_days": 0.0,
            "late_payment_ratio": 0.0,
            "total_revenue": 0.0,
            "outstanding_amount": 0.0,
            "credit_utilization_ratio": 0.0,
            "order_frequency_12m": 0.0,
            "avg_order_value": 0.0,
            "last_order_days_ago": 0.0,
            "refund_ratio": 0.0,
            "dispute_count": 0.0,
            "overdue_invoices_count": 0.0,
            "customer_lifetime_days": 0.0,
            "repeat_purchase_ratio": 0.0,
            # Useful helper values
            "overdue_amount": 0.0,
            "avg_unit_price": 0.0,
            "total_quantity": 0.0,
            "dso": 0.0,
            # AI enrichment defaults
            "risk_score": 0.0,
            "loyalty_score": 0.0,
            "financial_health": 0.0,
            "summary": "",
            "persona_tag": "",
            "persona_reason": "",
            "suggested_star": 0.0,
        }

    @api.model
    def _compute_partner_metrics(self, partner):
        metrics = self._empty_metrics()
        today = fields.Date.today()

        invoices = self.env["account.move"].search([
            ("partner_id", "=", partner.id),
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
        ])
        refunds = self.env["account.move"].search([
            ("partner_id", "=", partner.id),
            ("move_type", "=", "out_refund"),
            ("state", "=", "posted"),
        ])
        sale_orders = self.env["sale.order"].search([
            ("partner_id", "=", partner.id),
            ("state", "in", ("sale", "done")),
        ])

        inv_total_untaxed = sum(invoices.mapped("amount_untaxed"))
        ref_total_untaxed = sum(refunds.mapped("amount_untaxed"))
        metrics["total_revenue"] = float(inv_total_untaxed)
        metrics["refund_ratio"] = (ref_total_untaxed / inv_total_untaxed) if inv_total_untaxed else 0.0

        total_qty = sum(invoices.mapped("invoice_line_ids.quantity"))
        metrics["total_quantity"] = float(total_qty)
        metrics["avg_unit_price"] = (inv_total_untaxed / total_qty) if total_qty else 0.0

        unpaid_invoices = invoices.filtered(lambda m: m.payment_state not in ("paid", "in_payment"))
        metrics["outstanding_amount"] = float(sum(unpaid_invoices.mapped("amount_residual")))

        # Credit utilization
        credit_limit = float(partner.credit_limit or 0.0)
        metrics["credit_utilization_ratio"] = (
            (metrics["outstanding_amount"] / credit_limit) if credit_limit > 0 else 0.0
        )

        # Overdue indicators
        overdue_invoices = unpaid_invoices.filtered(
            lambda m: m.invoice_date_due and m.invoice_date_due < today
        )
        metrics["overdue_invoices_count"] = float(len(overdue_invoices))
        metrics["overdue_amount"] = float(sum(overdue_invoices.mapped("amount_residual")))

        # Payment behavior and DSO
        paid_invoices = invoices.filtered(lambda m: m.payment_state in ("paid", "in_payment"))
        delays = []
        late_count = 0
        dso_days = []
        for inv in paid_invoices:
            rec_lines = inv.line_ids.filtered(
                lambda l: l.account_type in ("asset_receivable", "liability_payable")
            )
            payment_dates = (
                rec_lines.matched_debit_ids.mapped("max_date")
                + rec_lines.matched_credit_ids.mapped("max_date")
            )
            if payment_dates:
                paid_on = max(payment_dates)
                if inv.invoice_date_due:
                    delay = (paid_on - inv.invoice_date_due).days
                    delays.append(max(delay, 0))
                    if delay > 0:
                        late_count += 1
                if inv.invoice_date:
                    dso_days.append((paid_on - inv.invoice_date).days)
        metrics["avg_payment_delay_days"] = (sum(delays) / len(delays)) if delays else 0.0
        metrics["late_payment_ratio"] = (late_count / len(paid_invoices)) if paid_invoices else 0.0
        metrics["dso"] = (sum(dso_days) / len(dso_days)) if dso_days else 0.0

        # Purchase behavior
        cutoff = today - relativedelta(months=12)
        so_12m = sale_orders.filtered(lambda so: so.date_order and so.date_order.date() >= cutoff)
        metrics["order_frequency_12m"] = float(len(so_12m))
        metrics["avg_order_value"] = (
            float(sum(sale_orders.mapped("amount_total")) / len(sale_orders))
            if sale_orders else 0.0
        )
        last_order = sale_orders.sorted(lambda so: so.date_order or fields.Datetime.from_string("1970-01-01"))[-1:] if sale_orders else self.env["sale.order"]
        if last_order and last_order.date_order:
            metrics["last_order_days_ago"] = float((today - last_order.date_order.date()).days)
        else:
            metrics["last_order_days_ago"] = 0.0

        # Loyalty indicators
        first_dates = []
        if invoices:
            first_dates.append(min(invoices.mapped("invoice_date")))
        if sale_orders:
            first_dates.append(min(sale_orders.mapped("date_order")).date())
        if first_dates:
            metrics["customer_lifetime_days"] = float((today - min(first_dates)).days)

        total_orders = len(sale_orders)
        metrics["repeat_purchase_ratio"] = (
            ((total_orders - 1) / total_orders) if total_orders > 0 else 0.0
        )

        # Risk proxy; keeps module robust if no dedicated dispute model exists.
        metrics["dispute_count"] = float(len(invoices.filtered(lambda m: m.payment_state == "reversed")))

        return metrics

    @api.model
    def _is_ai_enabled(self):
        config = self.env["ir.config_parameter"].sudo()
        enabled = bool(config.get_param("smart_customer_rating_ai.ai_enabled", True))
        provider = config.get_param("smart_customer_rating_ai.ai_provider", "none")
        _logger.info("AI Enablement Check: enabled=%s, provider=%s", enabled, provider)
        return enabled and provider != "none"

    @api.model
    def _has_ai_values(self, metrics_dict):
        """Check if metrics contain AI values"""
        return any(key in metrics_dict for key in ['risk_score', 'loyalty_score', 'financial_health'])

    @api.model
    def _get_cache_hours(self):
        config = self.env["ir.config_parameter"].sudo()
        raw = config.get_param("smart_customer_rating_ai.metrics_cache_hours", "24")
        try:
            return max(int(raw), 0)
        except Exception:
            return 24

    @api.model
    def _fetch_ai_interpretation(self, metrics):
        config = self.env["ir.config_parameter"].sudo()
        provider = config.get_param("smart_customer_rating_ai.ai_provider", "none")
        _logger.info("AI Provider from config: %s", provider)
        if provider == "none":
            _logger.info("AI Provider is 'none' - returning defaults")
            return {
                "risk_score": 0.0,
                "loyalty_score": 0.0,
                "financial_health": 0.0,
                "summary": "",
                "persona_tag": "",
                "persona_reason": "",
                "suggested_star": 0.0,
            }, "none"

        prompt = (
            "You are a business analyst rating a B2B customer. Analyze these metrics and return ONLY JSON:\n"
            "Context: total_revenue=sales revenue, outstanding_amount=unpaid bills, refund_ratio=returns/sales,\n"
            "order_frequency_12m=orders in last year, avg_payment_delay_days=average payment delay\n"
            "Scale: risk_score (0=low risk, 100=high risk), loyalty_score (0=low, 100=high), financial_health (0=poor, 100=excellent)\n"
            "suggested_star (0-5, where 5=best customer)\n"
            f"Data: {json.dumps(metrics, ensure_ascii=True)}\n"
            "Return JSON: {\"risk_score\": 0-100, \"loyalty_score\": 0-100, \"financial_health\": 0-100, \"summary\": \"brief analysis\", \"persona_tag\": \"2-4 words\", \"persona_reason\": \"brief reason\", \"suggested_star\": 0-5}"
        )

        def _parse_payload(text):
            # First try direct JSON, then fallback to first {...} chunk.
            try:
                payload = json.loads(text)
            except Exception:
                m = re.search(r"\{.*\}", text or "", flags=re.S)
                if not m:
                    return None
                try:
                    payload = json.loads(m.group(0))
                except Exception:
                    return None
            return payload if isinstance(payload, dict) else None

        defaults = {
            "risk_score": 0.0,
            "loyalty_score": 0.0,
            "financial_health": 0.0,
            "summary": "",
            "persona_tag": "",
            "persona_reason": "",
            "suggested_star": 0.0,
        }

        try:
            if provider == "ollama":
                url = config.get_param("smart_customer_rating_ai.ollama_url", "http://localhost:11434")
                model = config.get_param("smart_customer_rating_ai.ollama_model", "llama3")
                _logger.info("AI Ollama call: URL=%s, Model=%s", url, model)
                resp = requests.post(
                    f"{url}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False},
                    timeout=12,
                )
                _logger.info("AI Ollama response status: %s", resp.status_code)
                if resp.status_code != 200:
                    _logger.error("AI Ollama failed: %s", resp.text)
                    return defaults, f"ollama:{model}:http_{resp.status_code}"
                response_text = resp.json().get("response", "")
                _logger.info("AI Ollama raw response: %s", response_text[:200])
                data = _parse_payload(response_text)
                if not data:
                    _logger.error("AI Ollama JSON parse failed from: %s", response_text)
                    return defaults, f"ollama:{model}:invalid_json"
                _logger.info("AI Ollama parsed data: %s", data)
                return self._sanitize_ai_metrics(data), f"ollama:{model}"

            if provider == "openai":
                key = config.get_param("smart_customer_rating_ai.openai_api_key")
                if not key:
                    return defaults, "openai:no_key"
                resp = requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}]},
                    timeout=12,
                )
                if resp.status_code != 200:
                    return defaults, f"openai:gpt-4o:http_{resp.status_code}"
                content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                data = _parse_payload(content)
                if not data:
                    return defaults, "openai:gpt-4o:invalid_json"
                return self._sanitize_ai_metrics(data), "openai:gpt-4o"

            if provider == "custom":
                url = config.get_param("smart_customer_rating_ai.custom_url", "")
                api_key = config.get_param("smart_customer_rating_ai.custom_api_key", "")
                model = config.get_param("smart_customer_rating_ai.custom_model", "")
                if not url or not api_key:
                    return defaults, "custom:no_config"
                headers = {}
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
                resp = requests.post(
                    url,
                    json={"model": model, "prompt": prompt, "stream": False},
                    headers=headers,
                    timeout=12,
                )
                if resp.status_code != 200:
                    return defaults, f"custom:http_{resp.status_code}"
                response_text = resp.json().get("response", resp.text)
                data = _parse_payload(response_text)
                if not data:
                    return defaults, f"custom:invalid_json"
                return self._sanitize_ai_metrics(data), f"custom:{model}"

            if provider == "gemini":
                key = config.get_param("smart_customer_rating_ai.gemini_api_key")
                if not key:
                    return defaults, "gemini:no_key"
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models/"
                    f"gemini-1.5-flash:generateContent?key={key}"
                )
                resp = requests.post(
                    url,
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=12,
                )
                if resp.status_code != 200:
                    return defaults, f"gemini:flash:http_{resp.status_code}"
                text = (
                    resp.json().get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                data = _parse_payload(text)
                if not data:
                    return defaults, "gemini:flash:invalid_json"
                return self._sanitize_ai_metrics(data), "gemini:flash"

        except Exception as exc:
            _logger.error("AI interpretation failed: %s", exc)
            return defaults, provider

        return defaults, provider

    @api.model
    def _sanitize_ai_metrics(self, values):
        def _clamp(v):
            try:
                return max(0.0, min(100.0, float(v)))
            except Exception:
                return 0.0
        def _clamp_star(v):
            try:
                return max(0.0, min(5.0, float(v)))
            except Exception:
                return 0.0

        summary = (values.get("summary") or "").strip()
        words = summary.split()
        if len(words) > 20:
            summary = " ".join(words[:20])
        persona_tag = (values.get("persona_tag") or "").strip()
        tag_words = persona_tag.split()
        if len(tag_words) > 4:
            persona_tag = " ".join(tag_words[:4])
        persona_reason = (values.get("persona_reason") or "").strip()
        reason_words = persona_reason.split()
        if len(reason_words) > 14:
            persona_reason = " ".join(reason_words[:14])
        return {
            "risk_score": _clamp(values.get("risk_score", 0.0)),
            "loyalty_score": _clamp(values.get("loyalty_score", 0.0)),
            "financial_health": _clamp(values.get("financial_health", 0.0)),
            "summary": summary,
            "persona_tag": persona_tag,
            "persona_reason": persona_reason,
            "suggested_star": _clamp_star(values.get("suggested_star", 0.0)),
        }

    @api.model
    def _get_partner_metrics(self, partner):
        """
        Return merged deterministic+optional-AI metrics with cache.
        Never raises; returns safe defaults on failure.
        """
        cache_model = self.env["ll.partner.metrics.cache"].sudo()
        defaults = self._empty_metrics()
        try:
            base = self._compute_partner_metrics(partner)
            source_hash = cache_model.build_hash(base)
            cache_hours = self._get_cache_hours()
            ttl_ok = False
            row = cache_model.search([("partner_id", "=", partner.id)], limit=1)
            if row and row.last_updated:
                age_hours = (
                    fields.Datetime.now() - row.last_updated
                ).total_seconds() / 3600.0
                ttl_ok = age_hours <= cache_hours

            if row and ttl_ok and row.source_hash == source_hash:
                _logger.info("Using cached metrics for partner %s (age: %.1f hours)", partner.id, age_hours)
                try:
                    cached = json.loads(row.metrics_json or "{}")
                    # Check if cache has AI values
                    has_ai = any(key in cached for key in ['risk_score', 'loyalty_score', 'financial_health'])
                    _logger.info("Cache has AI values: %s for partner %s", has_ai, partner.id)
                    defaults.update(cached)
                    return defaults
                except Exception:
                    pass

            final_metrics = dict(base)
            ai_payload = {
                "risk_score": 0.0,
                "loyalty_score": 0.0,
                "financial_health": 0.0,
                "summary": "",
                "persona_tag": "",
                "persona_reason": "",
                "suggested_star": 0.0,
            }
            ai_provider = "disabled"
            
            # Always recompute AI metrics if AI is enabled and no AI values in cache
            if self._is_ai_enabled():
                has_cached_ai = row and ttl_ok and self._has_ai_values(cached if row else {})
                if not has_cached_ai:
                    _logger.info("Forcing fresh AI computation for partner %s (no cached AI values)", partner.id)
                    ai_payload, ai_provider = self._fetch_ai_interpretation(base)
                    final_metrics.update(ai_payload)
                    final_metrics["ai_provider_used"] = ai_provider
                else:
                    _logger.info("Using cached AI values for partner %s", partner.id)
                    final_metrics.update(cached)
                    ai_provider = cached.get("ai_provider_used", "disabled")
            else:
                _logger.info("AI is disabled for partner %s", partner.id)

            final_metrics["ai_provider_used"] = ai_provider

            vals = {
                "partner_id": partner.id,
                "metrics_json": json.dumps(final_metrics, ensure_ascii=True),
                "ai_json": json.dumps(ai_payload, ensure_ascii=True),
                "source_hash": source_hash,
                "last_updated": fields.Datetime.now(),
            }
            if row:
                row.write(vals)
            else:
                cache_model.create(vals)

            defaults.update(final_metrics)
            return defaults
        except Exception as exc:
            _logger.error("Partner metrics computation failed for %s: %s", partner.id, exc)
            return defaults

    def _get_metric_value(self, partner, metrics=None):
        """Compute and return the raw metric value for a partner."""
        self.ensure_one()
        metrics = metrics or self._get_partner_metrics(partner)

        # Legacy compatibility mapping
        key = self.metric_key
        if key == "revenue":
            key = "total_revenue"
        elif key == "frequency":
            key = "order_frequency_12m"
        elif key == "returns":
            key = "refund_ratio"
        elif key == "overdue":
            key = "overdue_amount"
        elif key == "aup":
            key = "avg_unit_price"
        elif key == "quantity":
            key = "total_quantity"
        elif key == "dso":
            key = "dso"
        elif key == "sentiment":
            key = "financial_health"

        try:
            return float(metrics.get(key, 0.0) or 0.0)
        except Exception:
            return 0.0

    def _evaluate(self, partner, metrics=None):
        """
        Evaluate the rule against a partner.
        Returns (points_awarded, metric_value, threshold_met, ai_provider_used).
        """
        self.ensure_one()
        metrics = metrics or self._get_partner_metrics(partner)
        val = self._get_metric_value(partner, metrics=metrics)
        ai_provider_used = metrics.get("ai_provider_used", False) if self.category == "ai" else False
        
        # Debug logging for AI rules
        if self.category == "ai":
            _logger.info("AI Rule Evaluation: %s for partner %s, metric_key=%s, value=%.2f", 
                        self.name, partner.id, self.metric_key, val)

        if self.threshold_type == 'gt':
            met = val > self.threshold_min
        elif self.threshold_type == 'lt':
            met = val < self.threshold_min
        elif self.threshold_type == 'between':
            met = self.threshold_min <= val <= self.threshold_max
        else:
            met = False

        return (self.score if met else 0, val, met, ai_provider_used)
