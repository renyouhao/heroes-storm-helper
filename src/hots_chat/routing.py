import unicodedata


def incoming_source_language(text):
    """只接收英、韩文字母；汉字可混用，但不独立触发翻译。"""
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
                return None
        elif (unicodedata.category(character).startswith('M') and character != '\u20e3'
              and not unicodedata.name(character, '').startswith('VARIATION SELECTOR-')):
            # 分解形式的外语重音同样不属于英文；保留表情的变体选择符和数字键帽。
            return None
    return source


def should_translate(text):
    return incoming_source_language(text) is not None
