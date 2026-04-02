from odoo import fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ll_rating_ai_advisory_mode = fields.Selection([
        ('off', 'Off (Ignore AI advisory values)'),
        ('advisory', 'Advisory only (show AI, do not affect score)'),
        ('blend', 'Blend AI suggested stars into final score'),
    ], string="AI Advisory Mode", config_parameter='smart_customer_rating_ai.ai_advisory_mode', default='advisory')
    ll_rating_ai_blend_weight = fields.Float(
        string="AI Blend Weight",
        config_parameter='smart_customer_rating_ai.ai_blend_weight',
        default=0.2,
        help="Only used in Blend mode. Final Score = (1-w)*Rules Score + w*AI Suggested Stars.",
    )
    ll_rating_ai_enabled = fields.Boolean(
        string="Enable AI Enrichment",
        config_parameter='smart_customer_rating_ai.ai_enabled',
        default=True,
    )
    ll_rating_ai_provider = fields.Selection([
        ('none', 'Disabled'),
        ('openai', 'OpenAI'),
        ('gemini', 'Google Gemini'),
        ('ollama', 'Local Ollama'),
        ('custom', 'Custom API Endpoint'),
    ], string="AI Provider", config_parameter='smart_customer_rating_ai.ai_provider', default='none')
    ll_rating_metrics_cache_hours = fields.Integer(
        string="Metrics Cache (Hours)",
        config_parameter='smart_customer_rating_ai.metrics_cache_hours',
        default=24,
    )

    ll_rating_openai_api_key = fields.Char(string="OpenAI API Key", config_parameter='smart_customer_rating_ai.openai_api_key')
    ll_rating_gemini_api_key = fields.Char(string="Gemini API Key", config_parameter='smart_customer_rating_ai.gemini_api_key')
    ll_rating_custom_url = fields.Char(string="Custom API URL", config_parameter='smart_customer_rating_ai.custom_url', default="")
    ll_rating_custom_api_key = fields.Char(string="Custom API Key", config_parameter='smart_customer_rating_ai.custom_api_key', default="")
    ll_rating_custom_model = fields.Char(string="Custom Model", config_parameter='smart_customer_rating_ai.custom_model', default="")
    ll_rating_ollama_url = fields.Char(string="Ollama URL", config_parameter='smart_customer_rating_ai.ollama_url', default="http://localhost:11434")
    ll_rating_ollama_model = fields.Char(string="Ollama Model", config_parameter='smart_customer_rating_ai.ollama_model', default="llama3")

    ll_rating_auto_recompute = fields.Boolean(string="Daily Recompute", config_parameter='smart_customer_rating_ai.auto_recompute', default=True)
