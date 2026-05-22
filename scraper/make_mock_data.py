#!/usr/bin/env python3
"""
Generate dummy site data so you can preview the viewer WITHOUT scraping.
Useful for sanity-checking the UI / styling first.

USAGE
    python make_mock_data.py
    cd ../site && python -m http.server 8000
"""

import json
from pathlib import Path

OUT = Path(__file__).parent.parent / "site" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

TOC = [
    {
        "id": "intro",
        "title": "About Rocket Uniface",
        "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/intro.html",
        "children": [
            {
                "id": "arch",
                "title": "Uniface Application Architecture",
                "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/arch.html",
                "children": [],
            },
            {
                "id": "runtime",
                "title": "Uniface Runtime Environment",
                "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/runtime.html",
                "children": [],
            },
        ],
    },
    {
        "id": "procscript",
        "title": "ProcScript Reference",
        "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/procscript.html",
        "children": [
            {
                "id": "syntax",
                "title": "$syntax() Function",
                "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/syntax.html",
                "children": [],
            },
            {
                "id": "split",
                "title": "$split() and $scan() Functions",
                "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/split.html",
                "children": [],
            },
        ],
    },
    {
        "id": "db",
        "title": "Database Access",
        "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/db.html",
        "children": [],
    },
]

DOCS = {
    "intro": {
        "title": "About Rocket Uniface",
        "breadcrumbs": ["About Rocket Uniface"],
        "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/intro.html",
        "html": """
            <h1>About Rocket Uniface</h1>
            <p>Uniface is a low-code development and deployment platform for enterprise
            applications that can run in a wide range of runtime environments.</p>
            <h2>Key features</h2>
            <ul>
                <li>Platform and database independent</li>
                <li>Integration framework for Oracle, MSSQL, MySQL, IBM Db2</li>
                <li>Built-in support for web services, SMTP, LDAP</li>
            </ul>
            <p>Try <code>$syntax()</code> for validating input, or <code>$split()</code>
            to parse delimited strings.</p>
        """,
    },
    "arch": {
        "title": "Uniface Application Architecture",
        "breadcrumbs": ["About Rocket Uniface", "Uniface Application Architecture"],
        "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/arch.html",
        "html": """
            <h1>Uniface Application Architecture</h1>
            <p>A Uniface application is built from <b>components</b>: forms, services,
            server pages, and reports. Components are stored in the
            <i>repository</i> and compiled to runtime objects.</p>
            <h2>Three-tier model</h2>
            <p>The architecture follows a classic three-tier separation:
            presentation, business logic, and data access.</p>
        """,
    },
    "runtime": {
        "title": "Uniface Runtime Environment",
        "breadcrumbs": ["About Rocket Uniface", "Uniface Runtime Environment"],
        "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/runtime.html",
        "html": """
            <h1>Uniface Runtime Environment</h1>
            <p>The runtime consists of <code>urouter</code>, <code>userver</code>,
            and the resource manager. Configuration lives in <code>.asn</code> files.</p>
            <pre><code>[SETTINGS]
$putmess_logfile  D:\\logs\\uniface.log
$search_resources Resources_only</code></pre>
        """,
    },
    "procscript": {
        "title": "ProcScript Reference",
        "breadcrumbs": ["ProcScript Reference"],
        "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/procscript.html",
        "html": """
            <h1>ProcScript Reference</h1>
            <p>ProcScript is Uniface's proprietary scripting language used in triggers,
            entry/exit blocks, and operation definitions.</p>
            <h2>Common patterns</h2>
            <p>Use <code>$status</code> after every DB operation. Use <code>$ioprint</code>
            for debugging — but never in production code.</p>
        """,
    },
    "syntax": {
        "title": "$syntax() Function",
        "breadcrumbs": ["ProcScript Reference", "$syntax() Function"],
        "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/syntax.html",
        "html": """
            <h1>$syntax() Function</h1>
            <p>Validates a string against a Uniface syntax pattern. Useful for CLOB
            fields where regex is not available.</p>
            <pre><code>if ($syntax(v_email, "*@*.*") = 0)
    putmess "valid"
endif</code></pre>
        """,
    },
    "split": {
        "title": "$split() and $scan() Functions",
        "breadcrumbs": ["ProcScript Reference", "$split() and $scan() Functions"],
        "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/split.html",
        "html": """
            <h1>$split() and $scan() Functions</h1>
            <p>For email validation without <code>$regex</code>, combine
            <code>$split()</code> and <code>$scan()</code>:</p>
            <pre><code>v_parts = $split(v_email, "@")
if ($items(v_parts) != 2) ; invalid
endif</code></pre>
        """,
    },
    "db": {
        "title": "Database Access",
        "breadcrumbs": ["Database Access"],
        "url": "https://docs.rocketsoftware.com/de-DE/bundle/uniface_104/page/db.html",
        "html": """
            <h1>Database Access</h1>
            <p>Uniface accesses databases through <b>U-connectors</b>: Oracle (U3),
            MSSQL (U7), MySQL (U2), and others. Each connector translates
            Uniface's record-set operations into native SQL.</p>
            <h2>Connection settings</h2>
            <p>Define connections in the <code>[DRIVER_SETTINGS]</code> and
            <code>[PATHS]</code> sections of your ASN file.</p>
        """,
    },
}

SEARCH = []
for pid, doc in DOCS.items():
    # Strip HTML for the search corpus
    import re
    text = re.sub(r"<[^>]+>", " ", doc["html"])
    text = re.sub(r"\s+", " ", text).strip()
    SEARCH.append({
        "id": pid,
        "title": doc["title"],
        "breadcrumbs": " › ".join(doc["breadcrumbs"]),
        "text": text,
    })

(OUT / "toc.json").write_text(json.dumps(TOC, ensure_ascii=False, indent=2))
(OUT / "docs.json").write_text(json.dumps(DOCS, ensure_ascii=False, indent=2))
(OUT / "search-meta.json").write_text(json.dumps(SEARCH, ensure_ascii=False, indent=2))

print(f"✓ mock data written to {OUT}")
print(f"  → cd ../site && python -m http.server 8000")
