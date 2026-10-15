"""Запуск spotdl с двумя правками.

1. Уже скачанный трек пропускается ДО дозапроса его метаданных у Spotify.

   В `spotdl sync <ссылка>` у треков плейлиста нет жанров и числа дисков, и
   spotdl сначала «переинициализирует» трек — Song.from_url, ~3 запроса к
   Spotify — и только ПОТОМ видит, что файл уже есть, и пропускает его. На
   автосинке 52 плейлистов это тысячи запросов за уже скачанные треки: синк шёл
   почти сутки и упирался в лимиты Spotify.

   Здесь до вызова оригинала проверяется то же самое, что spotdl проверяет после
   переинициализации (путь по шаблону, тот же файл под другим именем, другие
   форматы). Если файл есть — недостающие поля, на путь НЕ влияющие (жанры,
   число дисков), заполняются пустыми, переинициализации нет, и spotdl сразу
   пропускает трек со своим обычным «Skipping … (file already exists)».
   Только для --overwrite skip: при metadata/force метаданные нужны.

   Патчится async_search_and_download — через него идут загрузки в spotdl
   4.5+ (pool_download); в старых версиях — search_and_download.

2. С SPOTDL_WEBUI_RESULTS=1 по каждому треку печатается строка
   `@@webui-result {json}`: ссылка Spotify, путь файла (если он есть),
   ролик-источник, был ли файл до загрузки и ошибка. По ней синк через индекс
   (app/index_sync.py) узнаёт итог каждого трека, не разбирая лог и errors-файл.

Запуск: python app/spotdl_run.py <аргументы spotdl> — как сам spotdl.
"""
import inspect
import json
import os
import sys
from pathlib import Path

RESULT_PREFIX = "@@webui-result "


def _known_paths(self, song) -> list:
    """Файлы, которые spotdl счёл бы этим треком: путь по шаблону, тот же трек
    под другим именем (по ссылке в тегах), другие форматы."""
    from spotdl.utils.formatter import create_file_name
    out = create_file_name(
        song=song,
        template=self.settings["output"],
        file_extension=self.settings["format"],
        restrict=self.settings["restrict"],
        file_name_length=self.settings["max_filename_length"],
    )
    found = [out] if out.exists() else []
    found += [p for p in self.known_songs.get(song.url, []) if p.exists() and p not in found]
    if not self.settings["scan_for_songs"]:
        found += [p for p in (out.with_suffix(f".{ext}") for ext in self.scan_formats)
                  if p.exists() and p not in found]
    return found


def _path_known(song) -> bool:
    # поля, из которых строится путь, должны быть настоящими — иначе путь не
    # совпадёт с тем, что дала бы переинициализация
    return bool(song.name and song.url) and None not in (
        song.tracks_count, song.track_number, song.album_id, song.album_artist)


def _prepare(self, song) -> bool:
    """Правка 1. Возвращает True, если файл трека уже есть."""
    try:
        if not _path_known(song) or not _known_paths(self, song):
            return False
        if (self.settings.get("overwrite") == "skip" and not self.settings.get("fetch_albums")
                and (song.genres is None or song.disc_count is None)):
            if song.genres is None:
                song.genres = []
            if song.disc_count is None:
                song.disc_count = song.disc_number or 1
        return True
    except Exception:  # noqa: BLE001 — правка не должна ломать spotdl: как без неё
        return False


def _report(self, song, path, existed: bool) -> None:
    """Правка 2: итог трека одной строкой."""
    try:
        real = None
        if path is not None:
            p = Path(path)
            # при «пропуске» spotdl возвращает путь по шаблону, а файл может
            # лежать под другим именем или в другом формате
            real = p if p.exists() else next(iter(_known_paths(self, song)), None)
        error = None
        if real is None:
            mine = [e for e in self.errors if song.url and e.startswith(song.url + " - ")]
            error = (mine[-1][len(song.url) + 3:] if mine else
                     next((e for e in reversed(self.errors) if "reinitializing song" in e), None)
                     or "файл не появился")
        print(RESULT_PREFIX + json.dumps({
            "url": song.url, "path": str(Path(real).absolute()) if real else None,
            "existed": bool(existed and real), "download_url": song.download_url, "error": error,
        }), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[webui] не удалось сообщить итог трека: {exc}", flush=True)


def _patch() -> None:
    from spotdl.download.downloader import Downloader
    report = os.environ.get("SPOTDL_WEBUI_RESULTS") == "1"

    if inspect.iscoroutinefunction(getattr(Downloader, "async_search_and_download", None)):
        original = Downloader.async_search_and_download

        async def async_search_and_download(self, song):
            existed = _prepare(self, song)
            res_song, path = await original(self, song)
            if report:
                _report(self, res_song or song, path, existed)
            return res_song, path

        Downloader.async_search_and_download = async_search_and_download
    else:
        original_sync = Downloader.search_and_download

        def search_and_download(self, song):
            existed = _prepare(self, song)
            res_song, path = original_sync(self, song)
            if report:
                _report(self, res_song or song, path, existed)
            return res_song, path

        Downloader.search_and_download = search_and_download


if __name__ == "__main__":
    _patch()
    from spotdl import console_entry_point
    sys.argv[0] = "spotdl"
    sys.exit(console_entry_point())
