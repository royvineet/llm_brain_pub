"""
Site recipes for web.py. Each module defines:

    ACTIONS = {"action_name": fn}      # fn(page, ctx) -> one-line summary for Telegram

and its first docstring line describes the site. Recipes log in on demand
(sessions persist in the site's browser profile) and raise on anything
unexpected — web.py screenshots the page and sends it to Telegram.
"""
