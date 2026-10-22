from dataclasses import dataclass,asdict
from .language_store import LanguageStore,text_field,decode_import


@dataclass(frozen=True)
class Phrase:
    id: str
    category: str
    source_text: str
    translations: dict
    reviewed: bool=False
    favorite: bool=False
    revision: int=1


def validate_phrase(value):
    if not isinstance(value,dict) or set(value)!=set(Phrase.__dataclass_fields__):raise ValueError('短句字段无效。')
    for key in ('id','category','source_text'):text_field(value[key],1000)
    if value['category'] not in ('对线','团战','机制','礼貌'):raise ValueError('短句分类无效。')
    if type(value['reviewed'])!=bool or type(value['favorite'])!=bool or type(value['revision'])!=int or value['revision']<1:raise ValueError('短句状态无效。')
    translations=value['translations']
    if not isinstance(translations,dict) or set(translations)-{'en','ko'} or not translations:raise ValueError('语言无效。')
    for text in translations.values():text_field(text,4000)
    return value


class PhraseBook:
    def __init__(self,path):self.store=LanguageStore(path)
    def list(self,category=None,query=''):
        items=[Phrase(**v) for v in self.store.list('phrases')]
        return sorted((v for v in items if (category is None or v.category==category) and query.casefold() in (v.source_text+' '+' '.join(v.translations.values())).casefold()),key=lambda p:(not p.favorite,p.category,p.id))
    def save(self,phrase):self.store.save_many('phrases',[validate_phrase(asdict(phrase))])
    def delete(self,phrase_id):self.store.delete('phrases',phrase_id)
    def import_json(self,data):
        values=decode_import(data,'phrases',validate_phrase);self.store.save_many('phrases',values);return len(values)
    def export_json(self,ids=None):return self.store.export('phrases',ids)
    def resolve(self,text,target):
        matches={v.translations[target] for v in self.list() if v.reviewed and v.source_text==text and target in v.translations}
        return next(iter(matches)) if len(matches)==1 else None
