"""Connection preferences contain no passwords or service usernames."""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Preferences:
    country: str = ''
    protocol: str = 'udp'

    def validate(self):
        if self.protocol not in ('udp', 'tcp'):
            raise ValueError('Select UDP or TCP.')
        if not re.fullmatch(r"[A-Za-zÀ-ž .'-]{0,80}", self.country):
            raise ValueError('Enter a single country name, or leave it empty for automatic selection.')
        return self
