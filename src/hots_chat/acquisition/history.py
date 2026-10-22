import hashlib
import struct
from dataclasses import dataclass
from .parsing import has_allowed_chat_prefix, parse_display_chat, ParsedChat

@dataclass(frozen=True)
class PollResult:
    messages: list[ParsedChat]
    gap: bool
    retry: bool
    read_bytes: int

class HistoryInvalid(RuntimeError):
    pass


class HistoryCleared(HistoryInvalid):
    pass


class HistoryReader:
    def __init__(self, memory, root, include_history=False, max_nodes=512, byte_budget=262144,
                 initial_tail=0, channels=None):
        if not 0x10000 <= root < 0x7fffffff0000 or root % 8:
            raise HistoryInvalid('Invalid history root')
        self.memory=memory; self.root=root; self.include_history=include_history
        self.initial_tail=max(0,int(initial_tail))
        self.channels=frozenset(channels) if channels is not None else None
        self.max_nodes=max_nodes; self.byte_budget=byte_budget
        self.last_head=None; self.last_stamp=None; self.initialized=False
        self.total_read_bytes=0; self.total_reads=0; self.poll_bytes=0

    def read(self, address, count):
        if not 0x10000 <= address < 0x7fffffff0000 or count < 0 or self.poll_bytes+count > self.byte_budget:
            raise HistoryInvalid('Read address or per-poll budget is invalid')
        self.poll_bytes+=count; self.total_read_bytes+=count; self.total_reads+=1
        data=self.memory.read(address,count)
        if len(data)!=count:
            raise HistoryInvalid('Chat data is no longer readable')
        return data

    def is_end(self, address):
        return address in (self.root, self.root | 1)

    def entry(self,node):
        if node%8: raise HistoryInvalid('History node alignment is invalid')
        older,newer,packed=struct.unpack('<QQQ',self.read(node,24))
        length=(packed & 0xffffffff)>>2
        if not 0<length<=4096:
            raise HistoryInvalid('Unsupported chat string layout')
        prefix=self.read(node+24,min(length,96))
        if not prefix.startswith(b'<s val="ChatDisplayTimeStamp">'):
            raise HistoryInvalid('History node is not a timestamped chat line')
        payload=prefix; parsed=None
        if has_allowed_chat_prefix(prefix.decode('utf-8', errors='ignore')):
            payload=prefix if len(prefix)==length else self.read(node+24,length)
            try: parsed=parse_display_chat(payload.decode('utf-8',errors='strict'))
            except UnicodeDecodeError as error: raise HistoryInvalid('Incomplete UTF-8 chat string') from error
        stamp=(packed & 0xffffffff,hashlib.blake2s(payload).digest())
        return {'older':older,'stamp':stamp,'message':parsed}

    def _poll(self):
        self.poll_bytes=0
        roots=self.read(self.root,16)
        head,tail=struct.unpack('<QQ',roots)
        if self.is_end(head):
            if not self.is_end(tail): raise HistoryInvalid('Invalid empty-list sentinel')
            if self.initialized and not self.is_end(self.last_head):
                raise HistoryCleared('Chat history was cleared; relocate the active history')
            self.last_head=head; self.last_stamp=None; self.initialized=True
            return {'messages':[], 'gap':False, 'read_bytes':self.poll_bytes}
        first=self.entry(head)
        if self.initialized and head==self.last_head and first['stamp']==self.last_stamp:
            return {'messages':[], 'gap':False, 'read_bytes':self.poll_bytes}
        if not self.initialized and not self.include_history and not self.initial_tail:
            if self.read(self.root,16)!=roots:
                return {'messages':[], 'gap':False, 'retry':True, 'read_bytes':self.poll_bytes}
            self.last_head=head; self.last_stamp=first['stamp']; self.initialized=True
            return {'messages':[], 'gap':False, 'read_bytes':self.poll_bytes}
        collected=[]; visited=set(); node=head
        initial_limit=self.initial_tail if not self.initialized and not self.include_history else 0
        found_anchor=not self.initialized or self.is_end(self.last_head)
        for _ in range(self.max_nodes):
            if self.is_end(node): break
            if node in visited or node % 8:
                raise HistoryInvalid('History link cycle or alignment is invalid')
            visited.add(node)
            entry=first if node==head else self.entry(node)
            if node==self.last_head and entry['stamp']==self.last_stamp:
                found_anchor=True; break
            if entry['message'] and (self.channels is None or entry['message'].channel in self.channels):
                collected.append(entry['message'])
                if initial_limit and len(collected)>=initial_limit:break
            node=entry['older']
        else:
            raise HistoryInvalid('History traversal exceeded the node budget')
        if self.read(self.root,16)!=roots:
            # Do not commit the cursor or emit a partially changed snapshot.
            return {'messages':[], 'gap':False, 'retry':True, 'read_bytes':self.poll_bytes}
        gap=self.initialized and not found_anchor
        self.last_head=head; self.last_stamp=first['stamp']; self.initialized=True
        return {'messages':list(reversed(collected)), 'gap':gap, 'read_bytes':self.poll_bytes}


    def poll(self):
        value=self._poll()
        return PollResult(value["messages"],value["gap"],value.get("retry",False),value["read_bytes"])
