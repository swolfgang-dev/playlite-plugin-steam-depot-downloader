#!/usr/bin/env python3
"""Register Liberation substitutes under the family names WPF expects.

Run as the guest desktop user with WINEPREFIX set and an X display available.
Wine's registry substitutions alone do not resolve WPF's font fallback.
"""
import os
from pathlib import Path
import shutil
import subprocess

from fontTools.ttLib import TTFont


def main():
    prefix = Path(os.environ['WINEPREFIX'])
    target = prefix / 'drive_c/windows/Fonts'
    target.mkdir(parents=True, exist_ok=True)
    styles = {'Regular': 'Regular', 'Bold': 'Bold', 'Italic': 'Italic',
              'Bold Italic': 'BoldItalic'}
    for family in ('Segoe UI', 'Arial', 'Tahoma'):
        for style, suffix in styles.items():
            font = TTFont('/usr/share/fonts/truetype/liberation/LiberationSans-' + suffix + '.ttf')
            names = {1: family, 2: style, 3: 'Playlite-Liberation-' + family + '-' + style,
                     4: family + ' ' + style, 6: (family + '-' + style).replace(' ', ''),
                     16: family, 17: style}
            for record in font['name'].names:
                if record.nameID in names:
                    record.string = names[record.nameID].encode(record.getEncoding())
            filename = (family + '-' + style).replace(' ', '') + '.ttf'
            font.save(target / filename)
            font.close()
            subprocess.run(['wine', 'reg', 'add',
                            r'HKLM\Software\Microsoft\Windows NT\CurrentVersion\Fonts',
                            '/v', family + ' ' + style + ' (TrueType)',
                            '/t', 'REG_SZ', '/d', filename, '/f'], check=True)
    # Preserve the freely licensed source font's copyright and license.
    shutil.copyfile('/usr/share/doc/fonts-liberation/copyright',
                    target / 'Playlite-Liberation-LICENSE.txt')


if __name__ == '__main__':
    main()
