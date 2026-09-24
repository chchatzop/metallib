# MetalLib -- a language tag as MusicBrainz / Picard write it: ISO 639-3 ("spa"), not a name
# ("Spanish") or a two-letter code ("ES") as rips often carry (user).
#
# Pure logic, no Picard imports.
#
# SPDX-License-Identifier: GPL-2.0-or-later

# name / ISO 639-1 -> ISO 639-3 (the terminology codes MusicBrainz uses)
_LANGUAGES = (
    ('eng', 'english', 'en'), ('spa', 'spanish', 'es'), ('por', 'portuguese', 'pt'), ('fra', 'french', 'fr'),
    ('deu', 'german', 'de'), ('ita', 'italian', 'it'), ('nld', 'dutch', 'nl'), ('swe', 'swedish', 'sv'),
    ('nor', 'norwegian', 'no'), ('nob', 'norwegian bokmal', 'nb'), ('nno', 'norwegian nynorsk', 'nn'),
    ('dan', 'danish', 'da'), ('fin', 'finnish', 'fi'), ('isl', 'icelandic', 'is'), ('fao', 'faroese', 'fo'),
    ('rus', 'russian', 'ru'), ('ukr', 'ukrainian', 'uk'), ('bel', 'belarusian', 'be'), ('pol', 'polish', 'pl'),
    ('ces', 'czech', 'cs'), ('slk', 'slovak', 'sk'), ('hun', 'hungarian', 'hu'), ('ron', 'romanian', 'ro'),
    ('bul', 'bulgarian', 'bg'), ('srp', 'serbian', 'sr'), ('hrv', 'croatian', 'hr'), ('bos', 'bosnian', 'bs'),
    ('slv', 'slovenian', 'sl'), ('mkd', 'macedonian', 'mk'), ('sqi', 'albanian', 'sq'), ('ell', 'greek', 'el'),
    ('tur', 'turkish', 'tr'), ('heb', 'hebrew', 'he'), ('ara', 'arabic', 'ar'), ('fas', 'persian', 'fa'),
    ('jpn', 'japanese', 'ja'), ('kor', 'korean', 'ko'), ('zho', 'chinese', 'zh'), ('lat', 'latin', 'la'),
    ('cym', 'welsh', 'cy'), ('gle', 'irish', 'ga'), ('gla', 'scottish gaelic', 'gd'), ('est', 'estonian', 'et'),
    ('lav', 'latvian', 'lv'), ('lit', 'lithuanian', 'lt'), ('eus', 'basque', 'eu'), ('cat', 'catalan', 'ca'),
    ('glg', 'galician', 'gl'), ('afr', 'afrikaans', 'af'), ('ind', 'indonesian', 'id'), ('msa', 'malay', 'ms'),
    ('tha', 'thai', 'th'), ('vie', 'vietnamese', 'vi'), ('hin', 'hindi', 'hi'), ('mon', 'mongolian', 'mn'),
    ('tgl', 'tagalog', 'tl'), ('epo', 'esperanto', 'eo'), ('kaz', 'kazakh', 'kk'), ('kat', 'georgian', 'ka'),
    ('hye', 'armenian', 'hy'), ('aze', 'azerbaijani', 'az'), ('sme', 'northern sami', 'se'),
    ('san', 'sanskrit', 'sa'), ('grc', 'ancient greek', ''), ('non', 'old norse', ''), ('ang', 'old english', ''),
    ('enm', 'middle english', ''), ('akk', 'akkadian', ''), ('sux', 'sumerian', ''), ('egy', 'egyptian', ''),
    ('zxx', 'no linguistic content', ''), ('mul', 'multiple languages', ''),
)
_BY_NAME = {}
for _code, _name, _two in _LANGUAGES:
    _BY_NAME[_name] = _code
    _BY_NAME[_code] = _code
    if _two:
        _BY_NAME[_two] = _code
_BY_NAME.update({'instrumental': 'zxx', 'none': 'zxx', 'multiple': 'mul', 'farsi': 'fas', 'castilian': 'spa',
                 'ger': 'deu', 'fre': 'fra', 'dut': 'nld', 'gre': 'ell', 'chi': 'zho', 'cze': 'ces',
                 'rum': 'ron', 'ice': 'isl', 'per': 'fas', 'alb': 'sqi', 'arm': 'hye', 'geo': 'kat',
                 'baq': 'eus', 'mac': 'mkd', 'may': 'msa', 'wel': 'cym', 'slo': 'slk'})


def iso639_3(value):
    """"Spanish" / "ES" / "es" / "spa" -> "spa"; '' when not recognised (then the tag is left as is)."""
    return _BY_NAME.get((value or '').strip().lower(), '')
