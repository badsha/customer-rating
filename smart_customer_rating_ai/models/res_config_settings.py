from odoo import fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ll_rating_ai_provider = fields.Selection([
        ('none', 'No AI'),
        ('openai', 'OpenAI'),
        ('gemini', 'Google Gemini'),
        ('ollama', 'Local Ollama'),
    ], string="AI Provider", config_parameter='smart_customer_rating_ai.ai_provider', default='none')

    ll_rating_openai_api_key = fields.Char(string="OpenAI API Key", config_parameter='smart_customer_rating_ai.openai_api_key')
    ll_rating_gemini_api_key = fields.Char(string="Gemini API Key", config_parameter='smart_customer_rating_ai.gemini_api_key')
    ll_rating_ollama_url = fields.Char(string="Ollama URL", config_parameter='smart_customer_rating_ai.ollama_url', default="http://localhost:11434")
    ll_rating_ollama_model = fields.Char(string="Ollama Model", config_parameter='smart_customer_rating_ai.ollama_model', default="llama3")

    ll_rating_auto_recompute = fields.Boolean(string="Daily Recompute", config_parameter='smart_customer_rating_ai.auto_recompute', default=True)
