"""Temporary bounded reader for the validated build-98348 chat history layout.

Session-specific root discovery is not solved by this prototype. It never writes
game memory. Only observed team/all channels are emitted; whispers are skipped.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics
import struct
import time

from chat_memory_probe import ReadOnlyGame, parse_display_chat


class HistoryInvalid(RuntimeError):
    pass


class HistoryReader:
    def __init__(self, memory, root, include_history=False, max_nodes=512, byte_budget=262144):
        if not 0x10000 <= root < 0x7fffffff0000 or root % 8:
            raise HistoryInvalid('Invalid history root')
        self.memory=memory; self.root=root; self.include_history=include_history
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
        if b'ColorBattleNetWhisper' not in prefix:
            channel=re.search(r'\[([^\[\]<>]{1,32})\]',prefix.decode('utf-8',errors='ignore'))
            if channel and channel[1] in ('团队','所有玩家'):
                payload=prefix if len(prefix)==length else self.read(node+24,length)
                try: parsed=parse_display_chat(payload.decode('utf-8',errors='strict'))
                except UnicodeDecodeError as error: raise HistoryInvalid('Incomplete UTF-8 chat string') from error
        stamp=(packed & 0xffffffff,hashlib.blake2s(payload).digest())
        if parsed: parsed['source_node']=hex(node)
        return {'older':older,'stamp':stamp,'message':parsed}

    def poll(self):
        self.poll_bytes=0
        roots=self.read(self.root,16)
        head,tail=struct.unpack('<QQ',roots)
        if self.is_end(head):
            if not self.is_end(tail): raise HistoryInvalid('Invalid empty-list sentinel')
            self.last_head=head; self.last_stamp=None; self.initialized=True
            return {'messages':[], 'gap':False, 'read_bytes':self.poll_bytes}
        first=self.entry(head)
        if self.initialized and head==self.last_head and first['stamp']==self.last_stamp:
            return {'messages':[], 'gap':False, 'read_bytes':self.poll_bytes}
        if not self.initialized and not self.include_history:
            if self.read(self.root,16)!=roots:
                return {'messages':[], 'gap':False, 'retry':True, 'read_bytes':self.poll_bytes}
            self.last_head=head; self.last_stamp=first['stamp']; self.initialized=True
            return {'messages':[], 'gap':False, 'read_bytes':self.poll_bytes}
        collected=[]; visited=set(); node=head
        found_anchor=not self.initialized or self.is_end(self.last_head)
        for _ in range(self.max_nodes):
            if self.is_end(node): break
            if node in visited or node % 8:
                raise HistoryInvalid('History link cycle or alignment is invalid')
            visited.add(node)
            entry=first if node==head else self.entry(node)
            if node==self.last_head and entry['stamp']==self.last_stamp:
                found_anchor=True; break
            if entry['message']: collected.append(entry['message'])
            node=entry['older']
        else:
            raise HistoryInvalid('History traversal exceeded the node budget')
        if self.read(self.root,16)!=roots:
            # Do not commit the cursor or emit a partially changed snapshot.
            return {'messages':[], 'gap':False, 'retry':True, 'read_bytes':self.poll_bytes}
        gap=self.initialized and not found_anchor
        self.last_head=head; self.last_stamp=first['stamp']; self.initialized=True
        return {'messages':list(reversed(collected)), 'gap':gap, 'read_bytes':self.poll_bytes}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid',type=int,required=True)
    parser.add_argument('--root',type=lambda value:int(value,0),help='Diagnostic override; omitted for structural calibration')
    parser.add_argument('--seconds',type=int,default=45)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    events=[]; gaps=0; times=[]; error=None
    started=time.perf_counter(); cpu=time.process_time()
    with ReadOnlyGame(args.pid) as game:
        if 'Base98348' not in game.path:
            raise HistoryInvalid('This temporary layout was verified only against build 98348')
        calibration=None
        root=args.root
        if root is None:
            from discover_chat_history import discover
            print(json.dumps({'status':'calibrating_history'}),flush=True)
            calibration=discover(game)
            root=int(calibration['root'],16)
        reader=HistoryReader(game,root,include_history=True)
        first=reader.poll(); events.extend(first['messages'])
        print(json.dumps({'ready':True,'initial_team_messages':len(events),'initial_read_bytes':reader.total_read_bytes}),flush=True)
        steady_started=time.perf_counter(); steady_cpu=time.process_time(); initial_bytes=reader.total_read_bytes
        deadline=time.perf_counter()+args.seconds
        try:
            while time.perf_counter()<deadline:
                due=time.perf_counter()+0.5
                before=time.perf_counter(); result=reader.poll(); times.append((time.perf_counter()-before)*1000)
                gaps+=int(result['gap'])
                for message in result['messages']:
                    events.append(message); print(json.dumps({'new_message':message},ensure_ascii=True),flush=True)
                time.sleep(max(0,due-time.perf_counter()))
        except HistoryInvalid as failure:
            error=str(failure)
        steady_wall=time.perf_counter()-steady_started
        steady_cpu_used=time.process_time()-steady_cpu
        report={'process_id':args.pid,'root':hex(root),'calibration':calibration,'initial_messages':len(first['messages']),
                'messages':events,'gaps':gaps,'error':error,'polls':len(times),
                'median_poll_ms':statistics.median(times) if times else None,
                'total_read_bytes':reader.total_read_bytes,'total_reads':reader.total_reads,
                'wall_seconds':time.perf_counter()-started,'process_cpu_seconds':time.process_time()-cpu,
                'steady_wall_seconds':steady_wall,'steady_process_cpu_seconds':steady_cpu_used,
                'steady_read_bytes':reader.total_read_bytes-initial_bytes,
                'limitations':['Session-specific root; cross-match and process-restart discovery not validated.',
                               'Only Chinese-client team/all message formats are enabled; whispers are excluded.',
                               'This is a throwaway acquisition probe, not a complete translator.']}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(args.output),'messages':len(events),'gaps':gaps,'error':error}),flush=True)
    if error: raise SystemExit(1)


if __name__=='__main__': main()
