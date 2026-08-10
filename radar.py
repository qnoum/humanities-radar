#!/usr/bin/env python3

import html
import math
import re
import time
import xml.etree.ElementTree as ET

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse, urlunparse

import requests


# ============================================================
# CONFIGURATION
# ============================================================

PUBLIC = Path("public")
DATA = Path("data")
SOURCE_FILE = Path("sources/substacks.txt")
FEED_FILE = PUBLIC / "feed.xml"

LOOKBACK_DAYS = 30
MAX_ITEMS = 150

PUBLIC.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "HumanitiesPaperRadar/0.2 "
        "(personal research RSS project)"
    )
}

session = requests.Session()
session.headers.update(HEADERS)


# These are not the only possible scholarly hosts.
# They are used mainly to recognize that a link is scholarly.
SCHOLARLY_DOMAINS = {
    "academia.edu",
    "researchgate.net",
    "philarchive.org",
    "philpapers.org",
    "lingbuzz.net",
    "ssrn.com",
    "hcommons.org",
    "zenodo.org",
    "osf.io",
    "jstor.org",
    "muse.jhu.edu",
    "projectmuse.org",
    "cambridge.org",
    "academic.oup.com",
    "oxfordacademic.com",
    "tandfonline.com",
    "degruyter.com",
    "brill.com",
    "springer.com",
    "sciencedirect.com",
    "sagepub.com",
    "wiley.com",
    "journals.sagepub.com",
}


FIELDS = {
    "Philology / Linguistics": [
        "linguistics",
        "linguistic",
        "syntax",
        "semantics",
        "pragmatics",
        "phonology",
        "morphology",
        "phonetics",
        "philology",
        "sociolinguistics",
        "etymology",
        "semiotics",
        "saussure",
        "jakobson",
        "chomsky",
        "language history",
        "historical linguistics",
    ],

    "Literary Studies": [
        "literature",
        "literary",
        "poetry",
        "poetics",
        "narratology",
        "fiction",
        "drama",
        "theatre",
        "comparative literature",
        "world literature",
        "textual criticism",
        "book history",
        "close reading",
    ],

    "Literary Theory": [
        "literary theory",
        "critical theory",
        "deconstruction",
        "poststructuralism",
        "structuralism",
        "formalism",
        "hermeneutics",
        "narratology",
        "new historicism",
        "semiotics",
        "reader response",
        "marxist criticism",
        "psychoanalytic criticism",
    ],

    "Philosophy": [
        "philosophy",
        "philosophical",
        "epistemology",
        "metaphysics",
        "ontology",
        "ethics",
        "aesthetics",
        "phenomenology",
        "hermeneutics",
        "logic",
        "philosophy of language",
        "philosophy of mind",
        "wittgenstein",
        "heidegger",
        "kant",
        "hegel",
        "nietzsche",
        "deleuze",
        "derrida",
        "foucault",
        "spinoza",
        "aristotle",
        "plato",
    ],

    "History": [
        "history",
        "historical",
        "historiography",
        "medieval",
        "medieval history",
        "renaissance",
        "byzantine",
        "ottoman",
        "social history",
        "cultural history",
        "economic history",
        "political history",
        "microhistory",
        "archive",
        "archives",
        "manuscript",
        "paleography",
        "codicology",
        "early modern",
        "ancient history",
    ],

    "Intellectual History": [
        "intellectual history",
        "history of ideas",
        "history of thought",
        "history of philosophy",
        "history of science",
        "history of knowledge",
        "genealogy",
        "conceptual history",
        "cambridge school",
        "koselleck",
        "skinner",
        "history of concepts",
        "intellectual",
    ],
}


# Queries deliberately combine humanities terminology with
# scholarly-host terminology. This makes Reddit a discovery
# mechanism rather than merely a search for Academia.edu.
REDDIT_QUERIES = [
    '"academia.edu"',
    '"researchgate.net"',
    '"philarchive.org"',
    '"lingbuzz.net"',
    '"jstor.org" philosophy',
    '"jstor.org" history',
    '"jstor.org" literature',
    '"jstor.org" linguistics',
    '"working paper" philosophy',
    '"working paper" history',
    '"preprint" linguistics',
    '"preprint" literature',
    '"paper" "intellectual history"',
]


@dataclass
class Mention:
    platform: str
    title: str
    discussion_url: str
    target_url: str
    published: datetime
    points: int = 0
    comments: int = 0
    source_name: str = ""


# ============================================================
# URL / TEXT HELPERS
# ============================================================

def canonical_url(url):
    if not url:
        return ""

    try:
        p = urlparse(url)

        host = p.netloc.lower()

        if host.startswith("www."):
            host = host[4:]

        path = p.path.rstrip("/")

        # Remove obvious tracking parameters.
        query = ""

        return urlunparse((
            "https",
            host,
            path,
            "",
            query,
            "",
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


def is_scholarly_host(url):
    h = host(url)

    return any(
        h == d or h.endswith("." + d)
        for d in SCHOLARLY_DOMAINS
    )


def fields_for(text):
    text = text.lower()

    result = []

    for field, words in FIELDS.items():

        if any(word in text for word in words):
            result.append(field)

    return result


def clean_text(text):
    if not text:
        return ""

    text = re.sub(r"<[^>]+>", " ", text)

    text = html.unescape(text)

    return re.sub(r"\s+", " ", text).strip()


def parse_date(value):
    if not value:
        return datetime.now(timezone.utc)

    value = value.strip()

    # RSS / Atom date formats vary.
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        pass

    try:
        value = value.replace("Z", "+00:00")

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        return datetime.now(timezone.utc)


# ============================================================
# SUBSTACK
# ============================================================

def load_substack_sources():

    if not SOURCE_FILE.exists():
        print("No Substack source file found.")

        return []

    sources = []

    for line in SOURCE_FILE.read_text(
        encoding="utf-8"
    ).splitlines():

        line = line.strip()

        if not line or line.startswith("#"):
            continue

        parts = [
            p.strip()
            for p in line.split("|")
        ]

        if len(parts) < 3:
            print(
                "Ignoring malformed Substack line:",
                line
            )

            continue

        url, field, signal = parts[:3]

        sources.append({
            "url": url,
            "field": field,
            "signal": signal,
        })

    return sources


def extract_links(text):

    if not text:
        return []

    return re.findall(
        r'https?://[^\s"<>\']+',
        text
    )


def collect_substacks():

    mentions = []

    sources = load_substack_sources()

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=LOOKBACK_DAYS)
    )

    print(
        "Substack sources:",
        len(sources)
    )

    for source in sources:

        url = source["url"]

        try:

            response = session.get(
                url,
                timeout=25
            )

            response.raise_for_status()

            root = ET.fromstring(
                response.content
            )

            items = []

            # RSS
            items.extend(
                root.findall(".//item")
            )

            # Atom
            items.extend(
                root.findall(
                    ".//{http://www.w3.org/2005/Atom}entry"
                )
            )

            for item in items:

                title = ""
                link = ""
                description = ""
                date_value = ""

                # RSS
                element = item.find("title")

                if element is not None:
                    title = element.text or ""

                element = item.find("link")

                if element is not None:
                    link = element.text or ""

                element = item.find("description")

                if element is not None:
                    description = element.text or ""

                element = item.find("pubDate")

                if element is not None:
                    date_value = element.text or ""

                # Atom fallback
                if not title:

                    element = item.find(
                        "{http://www.w3.org/2005/Atom}title"
                    )

                    if element is not None:
                        title = element.text or ""

                if not link:

                    element = item.find(
                        "{http://www.w3.org/2005/Atom}link"
                    )

                    if element is not None:
                        link = element.attrib.get(
                            "href",
                            ""
                        )

                if not date_value:

                    element = item.find(
                        "{http://www.w3.org/2005/Atom}updated"
                    )

                    if element is not None:
                        date_value = element.text or ""

                published = parse_date(
                    date_value
                )

                if published < cutoff:
                    continue

                body = clean_text(
                    title + " " + description
                )

                links = extract_links(
                    description
                )

                # The Substack post itself can be useful,
                # but we particularly care about scholarly
                # links mentioned inside it.
                scholarly_links = [
                    canonical_url(x)
                    for x in links
                    if is_scholarly_host(x)
                ]

                if scholarly_links:

                    for target in scholarly_links:

                        mentions.append(
                            Mention(
                                platform="Substack",
                                title=title,
                                discussion_url=link,
                                target_url=target,
                                published=published,
                                source_name=url,
                            )
                        )

                else:

                    # Also keep the post if its title/body
                    # itself strongly indicates scholarship.
                    if fields_for(body):

                        mentions.append(
                            Mention(
                                platform="Substack",
                                title=title,
                                discussion_url=link,
                                target_url=link,
                                published=published,
                                source_name=url,
                            )
                        )

        except Exception as e:

            # One dead or private Substack must never
            # kill the entire radar.
            print(
                "Substack failed:",
                url,
                type(e).__name__,
                e
            )

        time.sleep(0.25)

    return mentions


# ============================================================
# REDDIT
# ============================================================

def collect_reddit():

    mentions = []

    print(
        "Attempting Reddit RSS discovery..."
    )

    for query in REDDIT_QUERIES:

        url = (
            "https://www.reddit.com/search.rss"
            "?q="
            + quote_plus(query)
            + "&sort=new&t=month"
        )

        try:

            response = session.get(
                url,
                timeout=25,
                headers={
                    **HEADERS,
                    "Accept": "application/rss+xml,"
                              "application/xml,text/xml"
                }
            )

            if response.status_code in (
                401,
                403,
                429
            ):

                print(
                    "Reddit refused query:",
                    query,
                    response.status_code
                )

                continue

            response.raise_for_status()

            root = ET.fromstring(
                response.content
            )

            entries = root.findall(
                ".//{http://www.w3.org/2005/Atom}entry"
            )

            for entry in entries:

                title_element = entry.find(
                    "{http://www.w3.org/2005/Atom}title"
                )

                title = (
                    title_element.text
                    if title_element is not None
                    else ""
                )

                link_element = entry.find(
                    "{http://www.w3.org/2005/Atom}link"
                )

                discussion_url = ""

                if link_element is not None:
                    discussion_url = (
                        link_element.attrib.get(
                            "href",
                            ""
                        )
                    )

                content_element = entry.find(
                    "{http://www.w3.org/2005/Atom}content"
                )

                content = (
                    content_element.text
                    if content_element is not None
                    else ""
                )

                target_links = extract_links(
                    content or ""
                )

                # Reddit RSS doesn't reliably expose
                # engagement counts, so don't invent them.
                #
                # Instead, use Reddit as a discovery /
                # independent-mention signal.
                for target in target_links:

                    if (
                        is_scholarly_host(target)
                        or fields_for(
                            title + " " + target
                        )
                    ):

                        mentions.append(
                            Mention(
                                platform="Reddit",
                                title=clean_text(title),
                                discussion_url=
                                    discussion_url,
                                target_url=
                                    canonical_url(target),
                                published=
                                    datetime.now(
                                        timezone.utc
                                    ),
                            )
                        )

                        break

        except Exception as e:

            print(
                "Reddit query failed:",
                query,
                type(e).__name__,
                e
            )

        time.sleep(1.0)

    return mentions


# ============================================================
# HACKER NEWS
# ============================================================

def collect_hacker_news():

    results = []

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=LOOKBACK_DAYS)
    )

    queries = [
        "academia.edu",
        "researchgate.net",
        "philarchive.org",
        "lingbuzz.net",
        "jstor.org",
        "philpapers.org",
        "ssrn.com",
        "zenodo.org",
        "osf.io",
        "literature",
        "linguistics",
        "philosophy",
        "history",
        "literary theory",
        "intellectual history",
    ]

    for query in queries:

        try:

            params = {
                "query": query,
                "tags": "story",
                "hitsPerPage": 100,
                "numericFilters":
                    "created_at_i>"
                    + str(
                        int(
                            cutoff.timestamp()
                        )
                    ),
            }

            response = session.get(
                "https://hn.algolia.com/api/v1/"
                "search_by_date",
                params=params,
                timeout=30,
            )

            response.raise_for_status()

            stories = response.json().get(
                "hits",
                []
            )

            for story in stories:

                title = story.get(
                    "title",
                    ""
                )

                url = canonical_url(
                    story.get(
                        "url",
                        ""
                    )
                )

                if not url:
                    continue

                combined = (
                    title + " " + url
                )

                if not (
                    is_scholarly_host(url)
                    or fields_for(combined)
                ):
                    continue

                results.append(
                    Mention(
                        platform="Hacker News",
                        title=title,
                        discussion_url=(
                            "https://news.ycombinator.com/"
                            "item?id="
                            + str(
                                story.get(
                                    "objectID"
                                )
                            )
                        ),
                        target_url=url,
                        points=int(
                            story.get(
                                "points"
                            ) or 0
                        ),
                        comments=int(
                            story.get(
                                "num_comments"
                            ) or 0
                        ),
                        published=datetime.fromtimestamp(
                            story[
                                "created_at_i"
                            ],
                            timezone.utc,
                        ),
                    )
                )

        except Exception as e:

            print(
                "HN search failed:",
                query,
                type(e).__name__,
                e
            )

        time.sleep(0.2)

    return results


# ============================================================
# RANKING
# ============================================================

def calculate_score(group):

    hn_points = sum(
        x.points
        for x in group
        if x.platform == "Hacker News"
    )

    hn_comments = sum(
        x.comments
        for x in group
        if x.platform == "Hacker News"
    )

    hn_mentions = sum(
        1
        for x in group
        if x.platform == "Hacker News"
    )

    reddit_mentions = sum(
        1
        for x in group
        if x.platform == "Reddit"
    )

    substack_sources = len({
        x.source_name
        for x in group
        if (
            x.platform == "Substack"
            and x.source_name
        )
    })

    platforms = len({
        x.platform
        for x in group
    })

    # HN is currently the only source where we have
    # reliable numeric engagement.
    #
    # Reddit and Substack contribute independent-
    # discovery / curation signals.
    score = (

        12 * math.log1p(
            hn_points
        )

        + 18 * math.log1p(
            hn_comments
        )

        + 8 * hn_mentions

        + 12 * reddit_mentions

        + 18 * substack_sources

        + 10 * max(
            0,
            platforms - 1
        )
    )

    return round(score)


# ============================================================
# RSS CREATION
# ============================================================

def create_feed(all_mentions):

    grouped = defaultdict(list)

    for mention in all_mentions:

        url = canonical_url(
            mention.target_url
        )

        if url:
            grouped[url].append(
                mention
            )

    ranked = []

    for url, group in grouped.items():

        text = " ".join(
            x.title + " " + url
            for x in group
        )

        fields = fields_for(text)

        if not fields:
            continue

        score = calculate_score(
            group
        )

        ranked.append(
            (
                score,
                url,
                group,
                fields,
            )
        )

    ranked.sort(
        key=lambda x: x[0],
        reverse=True
    )

    ranked = ranked[:MAX_ITEMS]

    rss = ET.Element(
        "rss",
        {
            "version": "2.0"
        }
    )

    channel = ET.SubElement(
        rss,
        "channel"
    )

    ET.SubElement(
        channel,
        "title"
    ).text = (
        "Humanities Paper Radar"
    )

    ET.SubElement(
        channel,
        "link"
    ).text = (
        "https://github.com/"
    )

    ET.SubElement(
        channel,
        "description"
    ).text = (
        "Humanities research discovered "
        "through Hacker News, Reddit and "
        "curated Substack publications."
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

        first = group[0]

        title = first.title

        hn_points = sum(
            x.points
            for x in group
            if x.platform == "Hacker News"
        )

        hn_comments = sum(
            x.comments
            for x in group
            if x.platform == "Hacker News"
        )

        hn_count = sum(
            1
            for x in group
            if x.platform == "Hacker News"
        )

        reddit_count = sum(
            1
            for x in group
            if x.platform == "Reddit"
        )

        substack_sources = sorted({
            x.source_name
            for x in group
            if (
                x.platform == "Substack"
                and x.source_name
            )
        })

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
            {
                "isPermaLink": "true"
            }
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

        # Human-readable signal report.
        description = (
            "<p><b>Fields:</b> "
            + html.escape(
                ", ".join(fields)
            )
            + "</p>"
        )

        description += (
            "<p><b>Radar score:</b> "
            + str(score)
            + "</p>"
        )

        description += (
            "<p><b>Hacker News:</b> "
            + str(hn_count)
            + " mention(s), "
            + str(hn_points)
            + " points, "
            + str(hn_comments)
            + " comments</p>"
        )

        description += (
            "<p><b>Reddit:</b> "
            + str(reddit_count)
            + " independent mention(s)"
        )

        if reddit_count:

            description += (
                " (engagement count unavailable "
                "from the public discovery endpoint)"
            )

        description += "</p>"

        description += (
            "<p><b>Substack:</b> "
            + str(
                len(substack_sources)
            )
            + " monitored publication(s)"
            "</p>"
        )

        if substack_sources:

            description += (
                "<ul>"
            )

            for source in substack_sources:

                description += (
                    "<li>"
                    + html.escape(source)
                    + "</li>"
                )

            description += (
                "</ul>"
            )

        description += (
            "<p><b>Discussions:</b></p>"
            "<ul>"
        )

        seen_discussions = set()

        for mention in group:

            if (
                not mention.discussion_url
                or mention.discussion_url
                in seen_discussions
            ):
                continue

            seen_discussions.add(
                mention.discussion_url
            )

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
                + '">discussion/source</a>'
            )

            if mention.platform == "Hacker News":

                description += (
                    " ("
                    + str(mention.points)
                    + " points, "
                    + str(mention.comments)
                    + " comments)"
                )

            description += (
                "</li>"
            )

        description += (
            "</ul>"
        )

        ET.SubElement(
            item,
            "description"
        ).text = description

    ET.indent(
        rss,
        space="  "
    )

    ET.ElementTree(
        rss
    ).write(
        FEED_FILE,
        encoding="utf-8",
        xml_declaration=True
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "======================================"
    )

    print(
        "Humanities Paper Radar v0.2"
    )

    print(
        "======================================"
    )

    hn = collect_hacker_news()

    print(
        "HN mentions:",
        len(hn)
    )

    reddit = collect_reddit()

    print(
        "Reddit mentions:",
        len(reddit)
    )

    substacks = collect_substacks()

    print(
        "Substack mentions:",
        len(substacks)
    )

    all_mentions = (
        hn
        + reddit
        + substacks
    )

    print(
        "Total mentions:",
        len(all_mentions)
    )

    create_feed(
        all_mentions
    )

    print(
        "RSS feed created:",
        FEED_FILE
    )


if __name__ == "__main__":
    main()
