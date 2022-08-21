import logging
import re

import httpx
import bs4

from .datatypes import Material, Videostore
from . import session, urls

async def get_material(year: int) -> dict[str, Material]:
    """
    Get the material available for a given year.
    """
    response = await session.get(
        httpx.URL(
            urls.did/"pls/portal30/sviluppo.materiale.elenco",
            params={"a": year, "t": "M"},
        ),
    )

    response.raise_for_status()
    if response.content == b'Access denied!\n':
        logging.error(response.url, "ACCESS DENIED")
    assert response.content != b'Access denied!\n'

    page = bs4.BeautifulSoup(response.content, "html.parser")
    material = {}

    data_regex = re.compile(r"'(\d+)','(\w+)','\d+','(\d+)'")
    for raw_subject in page.find_all("a", {"class": "policorpolink"}):
        material_name = raw_subject.text
        aa, typ, mat = data_regex.search(raw_subject["href"]).groups()
        material[material_name] = Material(aa, material_name, typ, mat)

    return material

async def get_videostores(year: int) -> dict[str, Videostore]:
    """
    Get the videostores available for a given year.
    """
    response = await session.get(
        httpx.URL(
            urls.did/"pls/portal30/sviluppo.materiale.elenco",
            params={"a": year, "t": "E"},
        ),
    )

    response.raise_for_status()
    if response.content == b'Access denied!\n':
        logging.error(response.url, "ACCESS DENIED")
    assert response.content != b'Access denied!\n'

    page = bs4.BeautifulSoup(response.content, "html.parser")
    videostores = {}

    data_regex = re.compile(r"sviluppo\.videolezioni\.vis\?cor=(\d+)")
    raw_videostores = page.find_all("a", {"onclick": re.compile(r"showDivVideoteca\('\w+'\)")})
    videolessons_group = page.find_all("div", {"class": "policorpo"})
    for videostore, raw_videolessons in zip(raw_videostores, videolessons_group):

        videostore_name = videostore.text.strip()
        videolessons = {}

        for videolesson in raw_videolessons.find_all("a", {"class": "policorpolink"}):
            if not data_regex.match(videolesson["href"]):
                logging.info(
                    "Skipping %s - %s because it's not supported yet.",
                    videostore.text, videolesson.text
                )
                continue

            videolesson_name = videolesson.text.strip()
            cor, = data_regex.search(videolesson["href"]).groups()
            videolessons[videolesson_name] = \
                Videostore(year, videostore_name, videolesson_name, cor)

        videostores[videostore_name] = videolessons

    return videostores
