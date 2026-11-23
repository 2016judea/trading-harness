#!/usr/bin/env python3
"""Build the ChatGPT plugin ZIP (platform.openai.com/plugins) from chatgpt/listing.json.

    python3 scripts/build_chatgpt_plugin.py          # check against the live server, build the ZIP
    python3 scripts/build_chatgpt_plugin.py --live   # also run the five positive cases live

The package is root plugin.json (Agent Plugins schema, OpenAI fields under
extensions.com.openai), mcp.json and assets/. Pattern from small-business-intelligence-mcp
(ChatGPT v1.2.0, 2026-11-22). Nothing about the tools is typed here: the hint table in the
listing must match the live tools/list exactly, tool for tool, or the build stops, because a
listing that drifts from the server is what OpenAI's rescan rejects.

Also writes the hint-justification table into SUBMISSION.md between the
<!-- chatgpt-hints --> markers. Needs inkscape and ImageMagick for the icons.
Standard library otherwise.
"""
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
L = json.loads((ROOT / "chatgpt" / "listing.json").read_text())
PKG = ROOT / "chatgpt-plugin"
OUT = ROOT / "chatgpt-plugin.zip"
UA = "openai-mcp/1.0.0"  # list what ChatGPT is served, not what Claude is
PRICING = re.compile(r"\b(free|paid|fees?|pricing|buy|purchase|checkout|subscriptions?|"
                     r"no cost|nothing is charged|fixed-fee)\b", re.I)


def rpc(method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    req = urllib.request.Request(L["mcp_url"], body, {
        "content-type": "application/json", "accept": "application/json, text/event-stream",
        "user-agent": UA, "x-test": "1"})
    raw = urllib.request.urlopen(req, timeout=90).read().decode()
    if raw.lstrip().startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
        raw = [ln[5:] for ln in raw.splitlines() if ln.startswith("data:")][-1]
    msg = json.loads(raw)
    if "error" in msg:
        raise RuntimeError(msg["error"])
    return msg["result"]


def fail(msg):
    sys.exit(f"build_chatgpt_plugin: {msg}")


def main():
    live = "--live" in sys.argv
    tools = {t["name"]: t for t in rpc("tools/list")["tools"]}
    hints = L["hints"]
    if set(tools) != set(hints):
        fail(f"hint table and live tools differ: {sorted(set(tools) ^ set(hints))}")
    for name, h in hints.items():
        a = tools[name].get("annotations") or {}
        got = (a.get("readOnlyHint"), a.get("destructiveHint"), a.get("openWorldHint"))
        want = (h["readOnly"], h["destructive"], h["openWorld"])
        if got != want:
            fail(f"{name}: live hints {got} but listing says {want}")
        if not tools[name].get("title") and not a.get("title"):
            fail(f"{name}: no title")

    i = L["interface"]
    if len(i["displayName"]) > 30 or len(i["shortDescription"]) > 30:
        fail("displayName or shortDescription over 30 characters")
    if len(i["defaultPrompt"]) > 3:
        fail("more than 3 default prompts")
    for label, text in [("description", L["description"]), ("shortDescription", i["shortDescription"]),
                        ("release_notes", L["release_notes"])] + [("defaultPrompt", p) for p in i["defaultPrompt"]]:
        m = PRICING.search(re.sub(r"free cash flow|public domain", "", text, flags=re.I))
        if m:
            fail(f"{label} carries pricing language: {m.group(0)!r}")
    if re.search(r"github\.com|\bMIT\b|open[- ]source", json.dumps(L), re.I):
        fail("listing links the repo or names a licence")
    pos, neg = L["test_cases"]["positive"], L["test_cases"]["negative"]
    if len(pos) != 5 or len(neg) != 3:
        fail(f"need exactly 5 positive and 3 negative cases, have {len(pos)} and {len(neg)}")
    for c in pos:
        for t in c["tools_triggered"]:
            if t not in tools:
                fail(f"case {c['prompt'][:40]!r} names unknown tool {t}")
        if c["call"]["tool"] not in c["tools_triggered"]:
            fail(f"case {c['prompt'][:40]!r}: call tool not in tools_triggered")

    if live:
        for c in pos:
            r = rpc("tools/call", {"name": c["call"]["tool"], "arguments": c["call"]["arguments"]})
            text = " ".join(x.get("text", "") for x in r.get("content", []) if x.get("type") == "text")
            kinds = sorted({x.get("type") for x in r.get("content", [])})
            bad = r.get("isError") or not r.get("content")
            print(f"  {'FAIL' if bad else 'PASS'}  {c['call']['tool']:<34} {kinds} {text[:110]!r}")
            if bad:
                fail("a positive case failed live")
            for need in c.get("expect_in_result", []):
                if need.lower() not in (text + json.dumps(r.get("structuredContent", {}))).lower():
                    fail(f"{c['call']['tool']}: result lacks {need!r}")

    # Icons: the logo on a solid square (no transparent corners), composer icons mono.
    (PKG / "assets").mkdir(parents=True, exist_ok=True)
    logo_src = ROOT / L["logo_src"]
    bg = subprocess.run(["magick", str(logo_src), "-format", "%[pixel:p{256,12}]", "info:"],
                        capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["magick", str(logo_src), "-background", bg, "-alpha", "remove", "-alpha", "off",
                    "-resize", "512x512", str(PKG / "assets" / "logo.png")], check=True)
    svg = (ROOT / L["composer_svg"]).read_text()
    for out, colour in [("composer-icon.png", "#111111"), ("composer-icon-dark.png", "#ffffff")]:
        tmp = PKG / "assets" / "_c.svg"
        tmp.write_text(svg.replace("currentColor", colour))
        subprocess.run(["inkscape", str(tmp), "--export-type=png", f"--export-filename={PKG / 'assets' / out}",
                        "-w", "128", "-h", "128"], check=True, capture_output=True)
        tmp.unlink()

    def case(c, positive):
        out = {"description": c["description"], "prompt": c["prompt"]}
        if positive:
            out["tools_triggered"] = c["tools_triggered"]
            out["expected_behavior"] = c["expected_behavior"]
        else:
            out["expected_behavior"] = c["expected_behavior"]
        return out

    manifest = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": L["name"],
        "version": L["version"],
        "description": L["description"],
        "author": {"name": "Brick and Mortar AI LLC", "url": i["websiteURL"]},
        "homepage": i["websiteURL"],
        "license": "LicenseRef-All-Rights-Reserved",
        "keywords": L["keywords"],
        "extensions": {"com.openai": {
            "interface": {
                "displayName": i["displayName"],
                "shortDescription": i["shortDescription"],
                "longDescription": L["description"],
                "developerName": "Brick and Mortar AI LLC",
                "category": i["category"],
                "capabilities": [],
                "websiteURL": i["websiteURL"],
                "supportURL": i["supportURL"],
                "privacyPolicyURL": i["privacyPolicyURL"],
                "termsOfServiceURL": i["termsOfServiceURL"],
                "defaultPrompt": i["defaultPrompt"],
                "logo": "./assets/logo.png",
                "composerIcon": "./assets/composer-icon.png",
                "composerIconDark": "./assets/composer-icon-dark.png",
            },
            "review": {
                "test_cases": {"positive": [case(c, True) for c in pos],
                               "negative": [case(c, False) for c in neg]},
                "demo_recording_url": L["demo_recording_url"],
                "commerce": False,
                "commerce_description": "No purchase or payment happens in ChatGPT, and no tool "
                                        "offers, prices or links to one.",
            },
            "publication": {"countries": ["US"], "release_notes": L["release_notes"]},
        }},
    }
    (PKG / "plugin.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (PKG / "mcp.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {L["server_key"]: {"type": "streamable-http", "url": L["mcp_url"]}},
    }, indent=2) + "\n")

    OUT.unlink(missing_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for f in ["plugin.json", "mcp.json"] + [f"assets/{p.name}" for p in sorted((PKG / "assets").iterdir())]:
            z.write(PKG / f, f)

    # The hint table, regenerated into SUBMISSION.md.
    rows = ["| Tool | readOnly | destructive | openWorld | Why |", "|---|---|---|---|---|"]
    for n, h in hints.items():
        rows.append(f"| `{n}` | {h['readOnly']} | {h['destructive']} | {h['openWorld']} | {h['why']} |")
    block = ("<!-- chatgpt-hints (generated by scripts/build_chatgpt_plugin.py; edit chatgpt/listing.json) -->\n"
             + "\n".join(rows) + "\n<!-- /chatgpt-hints -->")
    sub = ROOT / "SUBMISSION.md"
    s = sub.read_text()
    if "<!-- chatgpt-hints" in s:
        s = re.sub(r"<!-- chatgpt-hints.*?<!-- /chatgpt-hints -->", lambda _: block, s, flags=re.S)
    else:
        s = s.rstrip() + f"\n\n## ChatGPT listing v{L['version']} ({L['dated']})\n\n" \
            f"ZIP: `chatgpt-plugin.zip`, built by `python3 scripts/build_chatgpt_plugin.py --live` from " \
            f"`chatgpt/listing.json` (5 positive and 3 negative cases, checked against the live server). " \
            f"Demo recording: {L['demo_recording_url']}. Domain check: `/.well-known/openai-apps-challenge` " \
            f"serves Vercel/Worker env `OPENAI_APPS_CHALLENGE` (404 until set).\n\n{block}\n"
    sub.write_text(s)

    print(f"{OUT.name}: {len(tools)} tools, v{L['version']}, {L['mcp_url']}")
    for z in zipfile.ZipFile(OUT).infolist():
        print(f"  {z.file_size:>7}  {z.filename}")


if __name__ == "__main__":
    main()
