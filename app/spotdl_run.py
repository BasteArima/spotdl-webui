"""Запуск spotdl с одной правкой: уже скачанный трек пропускается ДО дозапроса
его метаданных у Spotify.

Почему: в `spotdl sync <ссылка>` у треков плейлиста нет жанров и числа дисков,
и spotdl (Downloader.search_and_download) сначала «переинициализирует» трек —
Song.from_url, ~3 запроса к Spotify — и только ПОТОМ видит, что файл уже есть,
и пропускает его. На автосинке 52 плейлистов это тысячи запросов за уже
скачанные треки: синк шёл почти сутки и упирался в лимиты Spotify.

Здесь до вызова оригинала проверяется то же самое, что spotdl проверяет после
переинициализации (путь по шаблону, тот же файл под другим именем, другие
форматы). Если файл есть — недостающие поля, на путь НЕ влияющие (жанры,
число дисков), заполняются пустыми, переинициализации нет, и spotdl сразу
пропускает трек со своим обычным «Skipping … (file already exists)».
Только для --overwrite skip: при metadata/force метаданные нужны.

Запуск: python app/spotdl_run.py <аргументы spotdl> — как сам spotdl.
"""
import sys


def _already_have(self, song) -> bool:
    from spotdl.utils.formatter import create_file_name
    out = create_file_name(
        song=song,
        template=self.settings["output"],
        file_extension=self.settings["format"],
        restrict=self.settings["restrict"],
        file_name_length=self.settings["max_filename_length"],
    )
    if out.exists():
        return True
    if any(p.exists() for p in self.known_songs.get(song.url, [])):
        return True
    if not self.settings["scan_for_songs"]:
        return any(out.with_suffix(f".{ext}").exists() for ext in self.scan_formats)
    return False


def _patch() -> None:
    from spotdl.download.downloader import Downloader
    original = Downloader.search_and_download

    def search_and_download(self, song):
        try:
            if (self.settings.get("overwrite") == "skip"
                    and not self.settings.get("fetch_albums")
                    and song.name and song.url
                    # поля, из которых строится путь, должны быть настоящими —
                    # иначе путь не совпадёт с тем, что дала бы переинициализация
                    and None not in (song.tracks_count, song.track_number,
                                     song.album_id, song.album_artist)
                    and (song.genres is None or song.disc_count is None)
                    and _already_have(self, song)):
                if song.genres is None:
                    song.genres = []
                if song.disc_count is None:
                    song.disc_count = song.disc_number or 1
        except Exception:  # noqa: BLE001 — правка не должна ломать spotdl: как без неё
            pass
        return original(self, song)

    Downloader.search_and_download = search_and_download


if __name__ == "__main__":
    _patch()
    from spotdl import console_entry_point
    sys.argv[0] = "spotdl"
    sys.exit(console_entry_point())
