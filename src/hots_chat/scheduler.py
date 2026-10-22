from collections import deque
import time


class PendingQueue:
    """由 controller 的 condition 锁保护；手动任务只抢占等待任务。"""
    def __init__(self,capacity=100,automatic_capacity=20,ttl=8,monotonic=None):
        self.capacity=capacity; self.automatic_capacity=automatic_capacity; self.items=deque()
        self.ttl=ttl; self.monotonic=monotonic or time.monotonic; self.deadlines={}

    def put(self,key,manual):
        displaced=None
        if not manual and sum(not item[1] for item in self.items)>=self.automatic_capacity:
            return False,None
        if len(self.items)>=self.capacity:
            if manual:
                displaced=next((item for item in reversed(self.items) if not item[1]),None)
                if displaced:
                    self.items.remove(displaced)
                    self.deadlines.pop(displaced[0],None)
            if not displaced: return False,None
        if manual:
            index=next((i for i,item in enumerate(self.items) if not item[1]),len(self.items))
            self.items.insert(index,(key,manual))
        else:
            self.items.append((key,manual))
            self.deadlines[key]=self.monotonic()+self.ttl
        return True,displaced[0] if displaced else None

    def promote(self,key):
        item=next((item for item in self.items if item[0]==key),None)
        if item and not item[1]:
            self.items.remove(item)
            self.deadlines.pop(key,None)
            self.put(key,True)

    def expire(self):
        now=self.monotonic(); expired=[]
        for item in list(self.items):
            key,manual=item
            if not manual and self.deadlines.get(key,now+self.ttl)<=now:
                self.items.remove(item); self.deadlines.pop(key,None); expired.append(key)
        return expired

    def pop(self):
        key=self.items.popleft()[0]
        self.deadlines.pop(key,None)
        return key

    def clear(self):
        self.items.clear(); self.deadlines.clear()
