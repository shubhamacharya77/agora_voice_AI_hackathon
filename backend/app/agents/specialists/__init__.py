"""The specialist line-up. Each one is a named group of typed tools."""

from app.agents.specialists.company_info import COMPANY_INFO
from app.agents.specialists.conversations import CONVERSATIONS
from app.agents.specialists.hr import HR
from app.agents.specialists.mail import MAIL
from app.agents.specialists.management import MANAGEMENT
from app.agents.specialists.meetings import MEETINGS
from app.agents.specialists.web import WEB
from app.agents.tools import ToolRegistry

ALL_SPECIALISTS = (WEB, COMPANY_INFO, HR, MAIL, MEETINGS, MANAGEMENT, CONVERSATIONS)


def build_registry() -> ToolRegistry:
    return ToolRegistry(ALL_SPECIALISTS)
