import unicodedata


def incoming_translation_route(text):
    """区分英/韩翻译、仅显示原文，以及不支持的文字。"""
    source = None
    for character in text:
        if character.isalpha():
            if character.isascii():
                if source is None:
                    source = 'en'
                continue
            name = unicodedata.name(character, '')
            if name.startswith(('HANGUL ', 'HALFWIDTH HANGUL ')):
                source = 'ko'
            elif not name.startswith(('CJK UNIFIED IDEOGRAPH-', 'CJK COMPATIBILITY IDEOGRAPH-')):
                return 'unsupported'
        elif (unicodedata.category(character).startswith('M') and character != '\u20e3'
              and not unicodedata.name(character, '').startswith('VARIATION SELECTOR-')):
            # 分解形式的外语重音同样不属于英文；保留表情的变体选择符和数字键帽。
            return 'unsupported'
    return source or 'original_only'


def incoming_source_language(text):
    """只接收英、韩文字母；汉字可混用，但不独立触发翻译。"""
    route = incoming_translation_route(text)
    return route if route in ('en', 'ko') else None


def should_translate(text):
    return incoming_source_language(text) is not None
