from collections import OrderedDict


class BoundedTranslationCache:
    def __init__(self,max_items=1000,max_bytes=4194304):
        self.max_items,self.max_bytes=max_items,max_bytes
        self.items=OrderedDict()
        self.byte_size=0

    def get(self,key):
        if key not in self.items: return None
        value,size=self.items[key]; self.items.move_to_end(key)
        return value

    def put(self,key,value):
        size=len(str(key).encode('utf-8'))+len(str(value).encode('utf-8'))
        if key in self.items:
            self.byte_size-=self.items.pop(key)[1]
        if size>self.max_bytes: return
        self.items[key]=(value,size); self.byte_size+=size
        while len(self.items)>self.max_items or self.byte_size>self.max_bytes:
            _,(_,removed)=self.items.popitem(last=False); self.byte_size-=removed
