#!/usr/bin/env python3

import html
import math
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests


PUBLIC = Path("public")
DATA = Path("data")
FEED_FILE = PUBLIC / "feed.xml"

PUBLIC.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "HumanitiesPaperRadar/0.1"
}

session = requests.Session()
session.headers.update(HEADERS)


ACADEMIC_DOMAINS = {
    "academia.edu",
    "researchgate.net",
    "lingbuzz.net",
    "philarchive.org",
    "ssrn.com",
    "hcommons.org",
    "zenodo.org",
    "osf.io",
    "jstor.org",
    "cambridge.org",
    "academic.oup.com",
    "oxfordacademic.com",
    "tandfonline.com",
    "degruyter.com",
    "brill.com",
    "muse.jhu.edu",
    "projectmuse.org",
    "philpapers.org",
}


FIELDS = {
    "Linguistics / Philology": [
        "linguistics", "linguistic", "syntax", "semantics",
        "pragmatics", "phonology", "morphology", "phonetics",
        "philology", "sociolinguistics", "etymology",
        "semiotics", "saussure", "chomsky"
    ],

    "Literary Studies": [
        "literature", "literary", "poetry", "poetics",
        "narratology", "fiction", "drama", "theatre",
        "comparative literature", "world literature",
        "textual criticism"
    ],

    "Literary Theory": [
        "literary theory", "critical theory", "deconstruction",
        "poststructuralism", "structuralism", "formalism",
        "hermeneutics", "narratology", "new historicism",
        "semiotics"
    ],

    "Philosophy": [
        "philosophy", "philosophical", "epistemology",
        "metaphysics", "ontology", "ethics", "aesthetics",
        "phenomenology", "hermeneutics", "logic",
        "philosophy of language", "philosophy of mind",
        "wittgenstein", "heidegger", "kant", "hegel",
        "nietzsche", "deleuze", "derrida", "foucault"
    ],

    "History": [
        "history", "historical", "historiography", "medieval",
        "renaissance", "byzantine", "ottoman", "social history",
        "cultural history", "economic history",
        "political history", "microhistory", "archive",
        "archives", "manuscript", "paleography", "codicology"
    ],

    "Intellectual History": [
        "intellectual history", "history of ideas",
        "history of thought", "history of philosophy",
        "history of science", "history of knowledge",
        "genealogy", "conceptual history",
        "cambridge school", "koselleck", "skinner"
    ],
}


WATCH_DOMAINS = [
    "academia.edu",
    "researchgate.net",
    "lingbuzz.net",
    "philarchive.org",
    "ssrn.com",
    "hcommons.org",
    "zenodo.org",
    "osf.io",
    "jstor.org",
    "cambridge.org",
    "academic.oup.com",
    "oxfordacademic.com",
    "tandfonline.com",
    "degruyter.com",
    "brill.com",
]


@dataclass
class Mention:
    platform: str
    title: str
    discussion_url: str
    target_url: str
    points: int
    comments: int
    published: datetime


def canonical_url(url):

    if not url:
        return ""

    try:
        p = urlparse(url)

        query = [
            (k, v)
            for k, v in parse_qsl(p.query)
            if not k.lower().startswith(("utm_", "fbclid", "gclid"))
        ]

        return urlunparse((
            p.scheme.lower(),
            p.netloc.lower(),
            p.path.rstrip("/"),
            "",
            urlencode(query),
            ""
        ))

    except Exception:
        return url


def host(url):

    try:
        h = urlparse(url).netloc.lower()

        if h.startswith("www."):
            h = h[4:]

        return h

    except Exception:
        return ""


def academic_domain(url):

    h = host(url)

    return any(
        h == d or h.endswith("." + d)
        for d in ACADEMIC_DOMAINS
    )


def fields_for(text):

    text = text.lower()
    result = []

    for field, words in FIELDS.items():

        if any(word in text for word in words):
            result.append(field)

    return result


def collect_hacker_news():

    results = []

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    for domain in WATCH_DOMAINS:

        try:

            params = {
                "query": domain,
                "tags": "story",
                "hitsPerPage": 100,
                "numericFilters":
                    "created_at_i>{}".format(
                        int(cutoff.timestamp())
                    )
            }

            response = session.get(
                "https://hn.algolia.com/api/v1/search_by_date",
                params=params,
                timeout=30
            )

            response.raise_for_status()

            stories = response.json().get("hits", [])

            for story in stories:

                title = story.get("title", "")
                url = canonical_url(story.get("url", ""))

                if not url:
                    continue

                fields = fields_for(
                    title + " " + url
                )

                if not fields:
                    continue

                results.append(
                    Mention(
                        platform="Hacker News",
                        title=title,
                        discussion_url=
                            "https://news.ycombinator.com/item?id="
                            + str(story.get("objectID")),

                        target_url=url,

                        points=int(
                            story.get("points") or 0
                        ),

                        comments=int(
                            story.get("num_comments") or 0
                        ),

                        published=datetime.fromtimestamp(
                            story["created_at_i"],
                            timezone.utc
                        )
                    )
                )

        except Exception as e:

            print(
                "HN search failed:",
                domain,
                type(e).__name__,
                e
            )

        time.sleep(0.3)

    return results


def calculate_score(mentions):

    points = sum(
        max(0, x.points)
        for x in mentions
    )

    comments = sum(
        max(0, x.comments)
        for x in mentions
    )

    platforms = len(
        set(x.platform for x in mentions)
    )

    independent_mentions = len(mentions)

    score = (

        8 * math.log1p(points)

        + 12 * math.log1p(comments)

        + 10 * platforms

        + 8 * max(
            0,
            independent_mentions - 1
        )
    )

    return round(score)


def create_feed(mentions):

    grouped = defaultdict(list)

    for mention in mentions:

        grouped[
            canonical_url(
                mention.target_url
            )
        ].append(mention)

    ranked = []

    for url, group in grouped.items():

        text = " ".join(
            x.title + " " + url
            for x in group
        )

        fields = fields_for(text)

        if not fields:
            continue

        score = calculate_score(group)

        ranked.append(
            (
                score,
                url,
                group,
                fields
            )
        )

    ranked.sort(
        key=lambda x: x[0],
        reverse=True
    )

    ranked = ranked[:100]

    rss = ET.Element(
        "rss",
        {"version": "2.0"}
    )

    channel = ET.SubElement(
        rss,
        "channel"
    )

    ET.SubElement(
        channel,
        "title"
    ).text = "Humanities Paper Radar"

    ET.SubElement(
        channel,
        "link"
    ).text = "https://news.ycombinator.com/"

    ET.SubElement(
        channel,
        "description"
    ).text = (
        "Humanities papers discovered through "
        "external discussion. The score is an "
        "experimental attention signal."
    )

    ET.SubElement(
        channel,
        "lastBuildDate"
    ).text = format_datetime(
        datetime.now(timezone.utc)
    )

    for score, url, group, fields in ranked:

        item = ET.SubElement(
            channel,
            "item"
        )

        title = group[0].title

        ET.SubElement(
            item,
            "title"
        ).text = (
            f"🔥 {score} — {title}"
        )

        ET.SubElement(
            item,
            "link"
        ).text = url

        ET.SubElement(
            item,
            "guid",
            {"isPermaLink": "true"}
        ).text = url

        newest = max(
            x.published
            for x in group
        )

        ET.SubElement(
            item,
            "pubDate"
        ).text = format_datetime(
            newest
        )

        for field in fields:

            ET.SubElement(
                item,
                "category"
            ).text = field

        total_points = sum(
            x.points for x in group
        )

        total_comments = sum(
            x.comments for x in group
        )

        description = (

            "<p><b>Fields:</b> "
            + html.escape(
                ", ".join(fields)
            )
            + "</p>"

            "<p><b>Attention signal:</b> "
            + str(score)
            + "</p>"

            "<p><b>External mentions:</b> "
            + str(len(group))
            + " · <b>Comments:</b> "
            + str(total_comments)
            + " · <b>Points:</b> "
            + str(total_points)
            + "</p>"

            "<p><b>Discussion:</b></p><ul>"

        )

        for mention in group:

            description += (

                "<li>"
                + html.escape(
                    mention.platform
                )
                + " — "

                + '<a href="'
                + html.escape(
                    mention.discussion_url
                )
                + '">discussion</a>'

                + " ("
                + str(mention.points)
                + " points, "
                + str(mention.comments)
                + " comments)"
                + "</li>"
            )

        description += "</ul>"

        ET.SubElement(
            item,
            "description"
        ).text = description

    ET.indent(
        rss,
        space="  "
    )

    PUBLIC.mkdir(
        parents=True,
        exist_ok=True
    )

    ET.ElementTree(
        rss
    ).write(
        FEED_FILE,
        encoding="utf-8",
        xml_declaration=True
    )


def main():

    print(
        "Humanities Paper Radar starting..."
    )

    mentions = collect_hacker_news()

    print(
        "Hacker News mentions:",
        len(mentions)
    )

    create_feed(mentions)

    print(
        "RSS feed created:",
        FEED_FILE
    )


if __name__ == "__main__":
    main()
