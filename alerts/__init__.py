"""MU Advisor Telegram Alert System."""

# Install the requests.post interceptor so legacy DeepSeek calls have their
# usage captured automatically. Must run before any module makes a DeepSeek
# request — placing it here in the package init guarantees that.
from . import deepseek_client  # noqa: F401
