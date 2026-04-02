import json
import hashlib
from odoo import fields, models


class PartnerMetricsCache(models.Model):
    _name = "ll.partner.metrics.cache"
    _description = "Partner Metrics Cache"
    _order = "last_updated desc, id desc"

    partner_id = fields.Many2one("res.partner", required=True, index=True, ondelete="cascade")
    metrics_json = fields.Text(required=True)
    ai_json = fields.Text()
    source_hash = fields.Char()
    last_updated = fields.Datetime(required=True, default=fields.Datetime.now, index=True)

    _unique_partner = models.Constraint(
        "UNIQUE (partner_id)",
        "Only one metrics cache row is allowed per partner.",
    )

    @staticmethod
    def build_hash(payload):
        raw = json.dumps(payload or {}, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
