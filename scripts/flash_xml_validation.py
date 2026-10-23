"""Validate official Flash XML without modifying its original bytes."""
import re
import xml.etree.ElementTree as ET

ENTITY = re.compile(r'&(?:[A-Za-z_:][\w:.-]*|#[^;\s<&]*)[;]')


def validate_flash_xml(raw: str) -> int:
    output = []
    index = count = 0
    in_tag = False
    quote = None
    while index < len(raw):
        if not quote and raw.startswith(('<!--', '<![CDATA[', '<?'), index):
            ending = '-->' if raw.startswith('<!--', index) else ']]>' if raw.startswith('<![CDATA[', index) else '?>'
            end = raw.find(ending, index)
            if end < 0:
                raise ValueError('Unterminated XML comment, CDATA or instruction')
            output.append(raw[index:end + len(ending)])
            index = end + len(ending)
            continue
        if not quote and raw.startswith('<!', index):
            raise ValueError('DTD/entity declarations are forbidden in Flash XML')
        char = raw[index]
        if char == '&' and (not in_tag or quote):
            entity = ENTITY.match(raw, index)
            if entity:
                output.append(entity.group())
                index = entity.end()
                continue
            output.append('&amp;')
            count += 1
        else:
            output.append(char)
            if in_tag:
                if quote:
                    if char == quote:
                        quote = None
                elif char in ('"', "'"):
                    quote = char
                elif char == '>':
                    in_tag = False
            elif char == '<':
                in_tag = True
        index += 1
    ET.fromstring(''.join(output))
    return count
