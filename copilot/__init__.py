"""
Copilot API - An unofficial Python wrapper for Microsoft Copilot consumer chat.

Basic usage — one client, conversations addressed by id:

>>> from copilot import CopilotClient
>>> client = CopilotClient()
>>> r = client.chat("Hello!")               # new conversation
>>> r.text, r.conversation_id
>>> client.chat("And again?", r.conversation_id)   # continue it
>>> for chunk in client.stream("Stream this"):     # incremental output
...     print(chunk, end="")
"""

__version__ = '1.0.0'

from .auth import load_auth
from .browser import BrowserCopilot
from .client import ChatReply, CopilotClient
from .config import BASE_URL, is_enterprise
from .driver import ClearanceRequired, Copilot
from .sydney import SydneyDriver

__all__ = [
    'CopilotClient',
    'ChatReply',
    'Copilot',
    'SydneyDriver',
    'ClearanceRequired',
    'BrowserCopilot',
    'load_auth',
    'BASE_URL',
    'is_enterprise',
]
