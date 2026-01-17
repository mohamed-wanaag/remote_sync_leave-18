{
    'name': 'Remote Leave Management Sync',
    'version': '1.0',
    'category': 'Human Resources',
    'author': 'Wanaag Solutions',
    'website': 'https://wanaag.odoo.com/',
    'license': 'OPL-1',
    'price': '30.00',
    'currency': 'USD',
    'support': 'backend@wanaag.co.ke',
    'summary': 'Synchronize employee leave requests with remote Odoo database using OdooRPC',
    'description': """
        Sync employee leave/time-off requests between local and remote Odoo instances.
        - Uses OdooRPC library for cleaner, more Pythonic remote calls
        - Automatic sync when leave is requested, approved, or refused
        - Bidirectional sync support
        - Multiple sync configurations
    """,
    'depends': ['hr_holidays'],
    'external_dependencies': {
        'python': ['odoorpc'],  # Requires: pip install OdooRPC
    },
    'data': [
        'security/ir.model.access.csv',
        'views/leave_sync_views.xml',
        'views/menu_views.xml',
    ],
    'images': ['images/main_screenshot.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
