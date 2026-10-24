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


@dataclass(frozen=True)
class PhraseSuggestion:
    phrase: Phrase
    local_available: bool


def suggest_phrases(phrases, text, target, limit=5):
    """输入条只查中文；空输入展示收藏，是否可本机直用遵守唯一译文规则。"""
    limit=max(0,min(5,limit))
    if not limit:return ()
    phrases=tuple(phrases)
    query=text.strip().casefold()
    translations={}
    for phrase in phrases:
        if phrase.reviewed and target in phrase.translations:
            translations.setdefault(phrase.source_text,set()).add(phrase.translations[target])
    matches=[phrase for phrase in phrases
             if (query in phrase.source_text.casefold() if query else phrase.favorite)]
    matches.sort(key=lambda phrase:(not phrase.favorite,
                                    not phrase.source_text.casefold().startswith(query),
                                    phrase.category,phrase.id))
    suggestions=[]
    seen=set()
    for phrase in matches:
        if phrase.source_text in seen:continue
        seen.add(phrase.source_text)
        available=(phrase.reviewed and phrase.source_text==phrase.source_text.strip()
                   and target in phrase.translations
                   and len(translations.get(phrase.source_text,()))==1)
        suggestions.append(PhraseSuggestion(phrase,available))
        if len(suggestions)>=limit:break
    return tuple(suggestions)


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
