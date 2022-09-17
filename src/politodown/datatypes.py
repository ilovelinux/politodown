import sre_compile
from typing import Optional, Callable, AsyncIterator, Union, Dict, List
import datetime
import asyncio
import logging
import pathlib
import time
import os
import re

import aiofiles.os
import aiofiles
import httpx
import bs4

from . import session, urls


def get_valid_filename(filename: str) -> str:
    """
    Replace invalid filename characters with an underscore
    """
    return re.sub(r'[^-\s\w.]', '_', filename)


def get_relative_path(element: Union["Material", "Videostore", "Assignment", "Folder", "File"]) -> str:
    """
    Get relative path of the element using each element's name
    as file/folder name and its parent as parent's directory.
    """
    if isinstance(element, (Material, Videostore)):
        return os.path.join(str(element.year), get_valid_filename(element.name))

    return os.path.join(
        get_relative_path(element.parent), get_valid_filename(element.name))


class Material:
    """
    Material instances are obtained from
    politodown.get_material(year: int)
    """
    def __init__(self, year: int, name: str, typ: str, mat: int):
        self.year = year
        self.name = name
        self._incarichi = httpx.URL(
            urls.did/"/pls/portal30/sviluppo.materiale.incarichi",
            params={"mat": mat, "aa": year, "typ": typ},
        )
        self._assignments = {}

    async def assignments(
        self,
        force_update: bool = False
    ) -> Dict[str, "Assignment"]:
        """
        Get assignments and cache the response.

        Cache will be overwrite only if `force_update` is `True`
        """
        if self._assignments and not force_update:
            return self._assignments

        self._assignments = {}
        response = await session.get(self._incarichi)
        page = bs4.BeautifulSoup(response.content, "html.parser")
        data_regex = re.compile(r"'(\d+)','(\d+)','(\d+)'")
        for raw_assignment in page.find_all("a", {"class": "policorpo"}):
            assignment_name = raw_assignment.text
            inc, nod, doc = data_regex.search(raw_assignment["href"]).groups()
            assignment = Assignment(self, assignment_name, inc, nod, doc)
            self._assignments[assignment_name] = assignment

        return self._assignments


class Videostore:
    """
    Material instances are obtained from
    politodown.get_videostores(year: int).
    """
    def __init__(self, year: int, category: str, name: str, cor: int):
        self.year = year
        self.name = name
        self.category = category
        self.vis = httpx.URL(
            urls.did/"pls/portal30/sviluppo.videolezioni.vis",
            params={"cor": cor},
        )
        self._videolessons = {}

    async def videolessons(
        self,
        force_update: bool = False
    ) -> dict[str, "File"]:
        """
        Get videolessons and cache the response.

        Cache will be overwrite only if `force_update` is `True`
        """
        if self._videolessons and not force_update:
            return self._videolessons

        coros = await self._get_videolessons()
        self._videolessons = {
            videolesson.properties["name"]: videolesson
            for videolesson in await asyncio.gather(*coros)
        }

        return self._videolessons

    async def _get_videolessons(self):
        response = await session.get(self.vis)
        page = bs4.BeautifulSoup(response.content, "html.parser")

        lessons = page.find_all("a", {"style": "color:#003576;"})
        dates = page.find_all("span", {"class": "small"})
        lessons_arguments = page.find_all("li", {"class": "argomentiEspansi"})

        coros = []

        for lesson, date, arguments in zip(lessons, dates, lessons_arguments):
            # Name
            name = lesson.text

            # Date
            raw_date = date.text[4:]  # date = "del dd/mm/YYYY"
            date = datetime.datetime.strptime(raw_date, "%d/%m/%Y")

            # Arguments
            arguments = [
                argument.text
                for argument in arguments.find_all("a", {"class": "argoLink"})
            ]

            # Open the videolesson page to extract infos about the video file
            url = urls.did/"pls/portal30/"/lesson['href']

            coros.append(self._get_videolesson_info(url, name, date, arguments))

        return coros

    async def _get_videolesson_info(
        self,
        url: "urls.BaseUrl",
        name: str,
        date: datetime.datetime,
        arguments: List[str]
    ) -> "File":
        async with session.stream("GET", url) as stream:
            page = bs4.BeautifulSoup(await stream.aread(), "html.parser")

            videohref = urls.did/page.find("a", text="Video")["href"]

            # Videolesson's infos are stored in the `tmpTitle` variable in js
            # This variable contains a multiline HTML string
            raw_info_script = page.find("script", text=re.compile("tmpTitle"))
            for i, line in enumerate(raw_info_script.string.split("\n")):
                if "tmpTitle = " in line:
                    # We found the beginning of the tmpTitle variable.
                    # It's 7 lines long, we need to parse the HTML
                    # string with bs4. Ugly but working.
                    videoinfo = bs4.BeautifulSoup(
                        "".join(x[23:-1] for x in raw_info_script.text.split("\n")[i:i+7]),
                        "html.parser",
                    )
                    break
            else:
                # TODO: if this happens, the next step
                #       will make the library crash
                logging.error(url, "No videolesson info found")


            filename = videoinfo.find("td", text=" File").nextSibling.text

            properties = {
                "name": name,
                "date": date,
                "arguments": arguments,
                **{
                    name.text.strip().lower(): value.text
                    for name, value in [
                        info.find_all("td")
                        for info in videoinfo.find_all("tr")
                    ]
                },
            }

            return File(self, filename, videohref, properties=properties)

class Videostore_old(Videostore):
    def __init__(
        self,
        year: int,
        category: str,
        name: str,
        inc: int,
        utente: str, 
        data: str,
        token: str
    ):

        self.year = year
        self.name = name
        self.category = category
        self.vis = httpx.URL(
            urls.elearn/"gadgets/video/template_video.php",
            params = {
                'inc': inc,
                'utente': utente,
                'data': data,
                'token': token,
            }

        )
        self._videolessons = {}

    async def videolessons(
        self,
        force_update: bool = False
    ) -> dict[str, "File"]:
        """
        Get videolessons and cache the response.

        Cache will be overwrite only if `force_update` is `True`
        """
        if self._videolessons and not force_update:
            return self._videolessons

        coros = await self._get_videolessons()
        self._videolessons = {
            videolesson.properties["name"]: videolesson
            for videolesson in await asyncio.gather(*coros)
        }

        return self._videolessons

    async def _get_videolessons(self):
        response = await session.get(self.vis)
        page = bs4.BeautifulSoup(response.content, "html.parser")

        summary = page.find_all("ul", {"class": "lezioni"})[0]
        lessons = summary.find_all("a")
        dates = summary.find_all("span", {"class": "small"})
        lessons_arguments = summary.find_all("li", {"class": "argEspansi1"})

        coros = []

        for lesson, date, arguments in zip(lessons, dates, lessons_arguments):
            # Name
            name = lesson.text

            # Date
            raw_date = date.text[4:]  # date = "del YYYY-mm-dd"
            date = datetime.datetime.strptime(raw_date, "%Y-%m-%d")

            # Arguments
            arguments = [
                argument.text
                for argument in arguments.find_all("a", {"class": "argoLink"})
            ]

            # Open the videolesson page to extract infos about the video file
            url = urls.elearn/"gadgets/video/"/lesson['href']

            coros.append(self._get_videolesson_info(url, name, date, arguments))

        return coros

    async def _get_videolesson_info(
        self,
        url: urls.BaseURL,
        name: str,
        date: datetime.datetime,
        arguments: List[str]
    ) -> "File":
        async with session.stream("GET", url) as stream:
            page = bs4.BeautifulSoup(await stream.aread(), "html.parser")

            videohref = urls.elearn/"gadgets/video/"/page.find("a", text="Video")["href"]

            videoinfo = page.find_all('div', {'id':'tooltip1'})
            filename = videoinfo.find_all('td', {'class':'value'})[0]

            properties = {
                "name": name,
                "date": date,
                "arguments": arguments,
                **{
                    name.text.strip().lower(): value.text
                    for name, value in [
                        info.find_all("td")
                        for info in videoinfo.find_all("tr")
                    ]
                },
            }

            return File(self, filename, videohref, properties=properties)



class Folder:
    """
    Represents a folder.
    """

    def __init__(
        self,
        parent: Union["Folder", "Assignment", Videostore],
        name: str,
        inc: int,
        nod: int,
        doc: int
    ):
        self.name = name
        self.parent = parent
        self._nextlevel = httpx.URL(
            urls.did/"pls/portal30/sviluppo.materiale.next_level",
            params={"inc": inc, "nod": nod, "doc": doc},
        )
        self._childs = None

    async def childs(
        self, force_update: bool = False
    ) -> dict[str, Union["File", "Folder"]]:
        """
        Get directory content and cache the response.

        Cache will be overwrite only if `force_update` is `True`.
        """
        if self._childs and not force_update:
            return self._childs

        self._childs = {}

        response = await session.get(self._nextlevel)
        response.raise_for_status()

        page = bs4.BeautifulSoup(response.content, "html.parser")

        folder_regex = re.compile(r"'(\d+)','(\d+)','(\d+)'")
        file_regex = re.compile(r"(\d+)$")

        # File type could not be defined
        info_regex = re.compile(r"(\w*) *\[([\w ]+)\]")

        for raw_element in page.find_all("a"):
            name = raw_element.text
            href = raw_element["href"]
            folder_match = folder_regex.search(href)
            if folder_match:  # Then it's a folder
                inc, nod, doc = folder_match.groups()
                element = Folder(self, name, inc, nod, doc)
            else:
                file_match = file_regex.search(href)
                if not file_match:
                    continue
                nod = file_match.group()
                link = httpx.URL(
                    urls.did/"pls/portal30/sviluppo.materiale.download",
                    params={"nod": nod},
                )

                info = raw_element.nextSibling.nextSibling.nextSibling
                extension, size = info_regex.search(info).groups()
                properties = {"extension": extension, "size": size}

                element = File(self, name, link, properties=properties)

            self._childs[name] = element

        return self._childs

    async def files(
        self, recursive: bool = False, force_update: bool = False
    ) -> AsyncIterator["File"]:
        """
        Asyncronously get folder's files.

        if `recursive=True`, all the subfolders's files will be
        yielded, but only after the files in the main folder.
        """
        childs = await self.childs(force_update)
        for file in filter(lambda child: isinstance(child, File), childs.values()):
            yield file

        if recursive:
            folders = filter(lambda child: isinstance(child, Folder), childs.values())
            for folder in folders:
                async for file in folder.files(True, force_update):
                    yield file


class Assignment(Folder):
    """
    Assignments works exactly like a folder. This class has
    been created only to discern assignments to folders.
    """


class File:
    """
    Represents a file

    After the first time save() has been called, properties
    `filename`, `size`, `date` and `etag` will be set.
    """

    def __init__(
        self,
        parent: Union[Folder, Assignment, Videostore],
        name: str,
        link: str,
        *,
        properties: Optional[Dict[str, any]] = None,
    ):
        self.parent = parent
        self.name = name
        self.download = link
        self.properties = {} if properties is None else properties

    @property
    def filename(self):
        """
        The filename the server returns.

        It's the value of the `Content-Disposition`
        in the response's header.
        """
        if not hasattr(self, "_filename"):
            raise ValueError("You must get info first.")
        return self._filename

    @property
    def size(self):
        """
        The file size.
        """
        if not hasattr(self, "_size"):
            raise ValueError("You must get info first.")
        return self._size

    @property
    def date(self) -> datetime.datetime:
        """
        File creation date, according to the server.
        """
        if not hasattr(self, "_date"):
            raise ValueError("You must get info first.")
        return self._date

    @property
    def etag(self) -> str:
        """
        File identifier of the resource.
        """
        if not hasattr(self, "_etag"):
            raise ValueError("You must get info first.")
        return self._etag

    async def save(
        self,
        path: Union[pathlib.Path, str],
        filename_generator: Callable[["File"], str],
        overwrite: bool = False,
    ) -> bool:
        """
        Save file in `path` using the value returned by
        `filename_generator(self, response)` as filename.

        The function `filename_generator(self, response)`
        can take advantages of the instance's properties
        `filename`, `size`, `date` and `etag` which will
        be setted right before the function call.

        if `filename_generator(self, response)` returns
        an empty string, the file will not be downloaded.

        If the file already exists and they have the
        same size, and the same creation date given by
        the server, the file will not be overwritten,
        unless `overwrite` is `True`.
        """
        path = pathlib.Path(path)
        async with session.stream("GET", self.download) as response:
            if response.status_code == 403:
                logging.error("%s: file not found", self.download)
                raise FileNotFound("File not found")
            response.raise_for_status()

            self._filename, = re.match(
                r'^.*filename="(.+)"$',
                response.headers.get(
                    "Content-Disposition",
                    f'filename="{self.name}"',
                ),
            ).groups()
            self._size = int(response.headers["Content-Length"])
            self._date = datetime.datetime.strptime(
                response.headers["Last-Modified"], "%a, %d %b %Y %H:%M:%S %Z")
            self._etag = response.headers["ETag"]

            filename = get_valid_filename(filename_generator(self))
            if filename == "" or filename.isspace():
                return

            filepath = path/filename

            mtime = time.mktime(self.date.timetuple())
            if not overwrite and await aiofiles.os.path.exists(filepath) and \
               await aiofiles.os.path.getsize(filepath) == self.size and \
               await aiofiles.os.path.getmtime(filepath) == mtime:
                # Since a file already exists with the same size and the same
                # creation time, and `overwrite` is `False`, do nothing.
                yield -1
                return

            tmpfilepath = f"{filepath}.tmp"

            async with aiofiles.open(tmpfilepath, "wb") as outfile:
                async for chunk in response.aiter_bytes():
                    yield await outfile.write(chunk)

            mtime = time.mktime(self.date.timetuple())
            await aiofiles.os.rename(tmpfilepath, filepath)
            os.utime(filepath, (mtime, mtime))


class FileNotFound(Exception):
    pass
