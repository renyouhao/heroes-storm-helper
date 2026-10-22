from collections import deque


class LocalContext:
    def __init__(self):self.generation=0;self.items=deque(maxlen=3)
    def add(self,event):
        if event.generation!=self.generation:self.clear(event.generation)
        self.items.append(event)
    def recent(self,limit=3):return list(self.items)[-max(0,min(3,limit)):] if limit>0 else []
    def clear(self,generation):self.generation=generation;self.items.clear()
