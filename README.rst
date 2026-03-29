Customer Rating
===============

Overview
--------
Customer Rating is an Odoo 19 addon to evaluate each customer using configurable criteria and a 0-5 score model.

Main capabilities
-----------------
- One rating record per customer (duplicate prevention).
- Central assessment template (`Assessment`) with criteria synchronization.
- Automatic star rendering and average score calculation.
- Color-tag classification (`Low`, `Medium`, `High`) for fast list scanning.
- History timeline with who/when/what changed.
- Re-evaluation scheduler that creates follow-up activities.

Integrations
------------
This base addon can be extended by bridge addons:
- `customer_rating_contacts`
- `customer_rating_sale`
- `customer_rating_crm`

Installation
------------
1. Add the parent folder to `addons_path`:

   `D:\Teertha\customer_rating`

2. Restart Odoo.
3. Update Apps List.
4. Install `customer_rating`.
5. Optionally install bridge modules.

Configuration
-------------
1. Open `Customer Ratings > Assessment`.
2. Maintain criteria lines in the default assessment record `Criteria`.
3. Open `Customer Ratings > View Ratings` to manage customer scores.

Technical notes
---------------
- Odoo version: 19.0
- License: OPL-1
- Dependency: `base`, `mail`

Support
-------
For support/customization, use your publisher contact email in the app listing.
