"""Temporary structural calibration for build 98348; no fixed game addresses."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import struct
import time

from chat_history_reader import HistoryInvalid, HistoryReader
from chat_memory_probe import ReadOnlyGame, StreamingMatcher

PREFIX=b'<s val="ChatDisplayTimeStamp">'


def read_checked(memory,address,size):
    if not 0x10000<=address<0x7fffffff0000 or not 0<size<=4096:
        raise HistoryInvalid('Invalid candidate read')
    data=memory.read(address,size)
    if len(data)!=size: raise HistoryInvalid('Candidate is no longer readable')
    return data


def locate_root(memory,node):
    visited=set()
    for _ in range(512):
        if node in visited or node%8:
            raise HistoryInvalid('Candidate has a cycle or invalid alignment')
        visited.add(node)
        older,newer,packed=struct.unpack('<QQQ',read_checked(memory,node,24))
        length=(packed&0xffffffff)>>2
        if not len(PREFIX)<=length<=4096 or read_checked(memory,node+24,len(PREFIX))!=PREFIX:
            raise HistoryInvalid('Candidate is not an inline chat history string')
        if older&1:
            root=older&~1
            if root%8: raise HistoryInvalid('Invalid tagged sentinel')
            head,tail=struct.unpack('<QQ',read_checked(memory,root,16))
            if tail!=node:
                raise HistoryInvalid('Sentinel tail does not reference the oldest node')
            head_links=struct.unpack('<QQ',read_checked(memory,head,16))
            if head_links[1] not in (root,root|1):
                raise HistoryInvalid('Sentinel head reciprocity failed')
            return root
        links=struct.unpack('<QQ',read_checked(memory,older,16))
        if links[1]!=node:
            raise HistoryInvalid('Candidate links are not reciprocal')
        node=older
    raise HistoryInvalid('Candidate traversal exceeded its budget')


def discover(memory,max_seconds=60):
    matcher=StreamingMatcher(PREFIX)
    started=time.perf_counter(); cpu=time.process_time(); scanned=0; candidates=0; checked_roots=set()
    for base,size,protection in memory.regions():
        for offset in range(0,size,1024*1024):
            if time.perf_counter()-started>max_seconds:
                raise HistoryInvalid('Calibration time budget exhausted')
            data=memory.read(base+offset,min(1024*1024,size-offset));scanned+=len(data)
            for address in matcher.feed(base+offset,data):
                candidates+=1
                try:
                    root=locate_root(memory,address-24)
                    if root in checked_roots: continue
                    checked_roots.add(root)
                    snapshot=HistoryReader(memory,root,include_history=True).poll()
                    if not snapshot['messages'] or snapshot.get('retry'): continue
                except (HistoryInvalid,ValueError,struct.error):
                    continue
                return {'root':hex(root),'scanned_mib':scanned/1024/1024,'candidate_strings':candidates,
                        'wall_seconds':time.perf_counter()-started,'process_cpu_seconds':time.process_time()-cpu,
                        'validated_team_or_all_messages':len(snapshot['messages'])}
            # Initialization is bounded and throttled; polling never repeats this scan.
            due=scanned/(128*1024*1024)
            time.sleep(max(0,due-(time.perf_counter()-started)))
    raise HistoryInvalid('No validated in-game team/all chat history found')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid',type=int,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with ReadOnlyGame(args.pid) as game:
        if 'Base98348' not in game.path: raise HistoryInvalid('Unsupported game build')
        result=discover(game)
        result.update({'process_id':args.pid,'game_path':game.path,'game_build':98348,
                       'timestamp':datetime.now().astimezone().isoformat(),'kind':'temporary_structural_calibration',
                       'limitations':['Calibration was validated only on the currently running game session.',
                                      'Root lifetime across matches and process restarts still needs a live check.']})
    args.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)


if __name__=='__main__': main()
