"""本机自动云候选防护；由控制器的 condition 锁串行访问。"""
from collections import OrderedDict, deque
from dataclasses import dataclass
import hashlib
import time

from .usage import character_count


@dataclass
class _SenderWindow:
    updated_at: float
    entries: deque


class AutomaticGuard:
    def __init__(self, monotonic=None, sender_limit=3, sender_seconds=10,
                 character_limit=3000, character_seconds=60, max_senders=1024):
        self.monotonic = monotonic or time.monotonic
        self.sender_limit, self.sender_seconds = sender_limit, sender_seconds
        self.character_limit, self.character_seconds = character_limit, character_seconds
        self.max_senders = max_senders
        self.senders = OrderedDict()
        self.characters = deque()
        self.character_total = 0

    def clear(self):
        self.senders.clear()
        self.characters.clear()
        self.character_total = 0

    @staticmethod
    def _identity(channel, sender):
        return hashlib.sha256((channel + '\0' + sender).encode('utf-8')).digest()

    def _prune(self, now):
        while self.senders:
            oldest = next(iter(self.senders.values()))
            if oldest.updated_at > now - self.sender_seconds:
                break
            self.senders.popitem(last=False)
        while self.characters and self.characters[0][0] <= now - self.character_seconds:
            _, count = self.characters.popleft()
            self.character_total -= count

    def reason(self, channel, sender, text):
        return self.sender_reason(channel,sender) or self.character_reason(text)

    def sender_reason(self, channel, sender):
        now = self.monotonic()
        self._prune(now)
        window = self.senders.get(self._identity(channel, sender))
        entries = window.entries if window else ()
        while entries and entries[0] <= now - self.sender_seconds:
            entries.popleft()
        if len(entries) >= self.sender_limit:
            return 'sender_rate'
        return None

    def character_reason(self, text):
        self._prune(self.monotonic())
        if self.character_total + character_count(text) > self.character_limit:
            return 'global_characters'
        return None

    def record(self, channel, sender, text):
        self.record_sender(channel,sender)
        self.record_characters(text)

    def record_sender(self, channel, sender):
        now = self.monotonic()
        self._prune(now)
        key = self._identity(channel, sender)
        window = self.senders.setdefault(key, _SenderWindow(now, deque(maxlen=self.sender_limit)))
        entries = window.entries
        while entries and entries[0] <= now - self.sender_seconds:
            entries.popleft()
        entries.append(now)
        window.updated_at = now
        self.senders.move_to_end(key)
        while len(self.senders) > self.max_senders:
            self.senders.popitem(last=False)

    def record_characters(self, text):
        now = self.monotonic()
        self._prune(now)
        count = character_count(text)
        if count:
            self.characters.append((now, count))
            self.character_total += count
