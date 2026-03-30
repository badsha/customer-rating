{
    "name": "Customer Rating Core",
    "version": "19.0.1.0.0",
    "summary": "Customer rating with criteria-based scoring (CRM, Sale, Contacts combined)",
    "author": "LogicLayer",
    "website": "https://logiclayerhq.com",
    "category": "Sales",
    "license": "OPL-1",
    "price": 9.99,
    "currency": "USD",
    "depends": ["base", "mail", "contacts", "crm", "sale"],
    "data": [
        "security/ir.model.access.csv",
        "data/demo_data.xml",
        "views/rating_views.xml",
        "views/res_partner_views.xml",
    ],
    "images": [
        "static/description/thumbnail.jpeg"
    ],
    "application": True,
    "installable": True,
}
