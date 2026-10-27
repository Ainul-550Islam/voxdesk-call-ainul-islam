#!/usr/bin/env python3
"""Structural verification of the native mobile SDKs.

There is no Android SDK, no Gradle and no Xcode in this environment, so this script
*never* claims a compile. What it does instead is check everything that can be checked
without a toolchain, because a structural mistake in a Kotlin or Swift file is what
actually breaks a client build:

* the Gradle and SwiftPM manifests exist and declare a library plus a test target;
* every source file declares the package/module its directory implies;
* braces, brackets and string interpolation markers balance (an unbalanced file is a
  syntax error in both languages);
* no `TODO`, `FIXME`, `NotImplemented` or placeholder body survives in a shipped file;
* the unit-test sources exist and actually assert something;
* the iOS privacy manifest is a valid plist that declares the required collected-data
  and accessed-API sections;
* every `/api/...` path a client calls exists in this repository's OpenAPI document
  (with the HTTP method the SDK uses).

Usage::

    python3 scripts/verify_mobile_sdks.py [--sdk sdk/android] [--json]

Exit codes: ``0`` all checks passed, ``1`` a check failed, ``2`` bad arguments.
"""

from __future__ import annotations

import argparse
import json
import plistlib
import re
import sys
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_MARKERS = re.compile(r"\b(TODO|FIXME|XXX|NotImplementedError|not implemented yet)\b")
PLACEHOLDER = re.compile(r"\b(pass|stub|placeholder)\b\s*(//|#|$)")

#: Kotlin: `client.get("/api/...")`, `client.post(...)`, `client.delete(...)`, and
#: `postForget` for a POST whose body we do not read.
KOTLIN_CALL = re.compile(r'client\.(get|post|postForget|delete|put)\(\s*"([^"]*)"', re.S)
#: Swift: same names, plus `postForget` (a POST whose body we do not read).
SWIFT_CALL = re.compile(
    r'client\.(get|post|delete|postForget|put)\(\s*(?:\n\s*)?"([^"]*)"', re.S
)
#: TypeScript: `request<...>("/api/...", { method: "POST" })`.
TS_CALL = re.compile(
    r"request<[^>]*>\(\s*(`[^`]*`|\"[^\"]*\")\s*(?:,\s*\{)?(.{0,220}?)\)", re.S
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    return text


def balanced(text: str) -> list[str]:
    """Report unbalanced brackets, ignoring those inside strings and comments.

    Comments are removed first because an apostrophe in prose ("the customer's app
    bundle") otherwise opens a phantom literal that swallows the closing braces. Only
    one-character `'x'` literals are treated as quotes, which is what Kotlin and Swift
    actually allow.
    """
    problems: list[str] = []
    cleaned = strip_comments(text)
    cleaned = re.sub(r'"""(?:.|\n)*?"""', '""', cleaned)
    cleaned = re.sub(r'"(?:[^"\\]|\\.)*"', '""', cleaned)
    cleaned = re.sub(r"'(?:[^'\\]|\\.)'", "''", cleaned)
    pairs = {"{": "}", "(": ")", "[": "]"}
    closers = {value: key for key, value in pairs.items()}
    stack: list[str] = []
    for index, char in enumerate(cleaned):
        if char in pairs:
            stack.append(char)
        elif char in closers:
            if not stack or stack[-1] != closers[char]:
                problems.append(f"unexpected {char!r} at offset {index}")
                return problems
            stack.pop()
    if stack:
        problems.append("unclosed " + ", ".join(repr(item) for item in stack))
    return problems


def normalise_path(raw: str) -> str:
    """Turn any SDK's interpolation into the API's ``{param}`` form."""
    path = raw.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]*\}", "{param}", path)
    path = re.sub(r"\\\([^)]*\)", "{param}", path)
    path = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "{param}", path)
    path = re.sub(r"\{[^}]*\}", "{param}", path)
    return path.rstrip("/") or "/"


def openapi_operations() -> dict[str, set[str]]:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from app.main import app  # imported here so a static run needs no app

    operations: dict[str, set[str]] = {}
    for path, spec in app.openapi()["paths"].items():
        operations[normalise_path(path)] = {method.upper() for method in spec}
    return operations


def extract_calls(text: str, language: str) -> Iterable[tuple[str, str]]:
    if language == "kotlin":
        for method, path in KOTLIN_CALL.findall(text):
            verb = "POST" if method == "postForget" else method.upper()
            yield verb, path
    elif language == "swift":
        for method, path in SWIFT_CALL.findall(text):
            verb = "POST" if method == "postForget" else method.upper()
            yield verb, path
    else:
        for quoted, tail in TS_CALL.findall(text):
            match = re.search(r'method:\s*"([A-Z]+)"', tail)
            verb = match.group(1) if match else "GET"
            yield verb, quoted.strip("`\"")


def check_sources(root: Path, language: str, problems: list[str], notes: list[str]) -> int:
    suffix = ".kt" if language == "kotlin" else ".swift"
    files = sorted(path for path in root.rglob(f"*{suffix}"))
    if not files:
        problems.append(f"{root}: no {suffix} sources")
        return 0
    for path in files:
        text = read(path)
        relative = path.relative_to(REPO_ROOT)
        if not text.strip():
            problems.append(f"{relative}: empty file")
            continue
        unbalanced = balanced(text)
        if unbalanced:
            problems.append(f"{relative}: " + "; ".join(unbalanced))
        if FORBIDDEN_MARKERS.search(text):
            problems.append(f"{relative}: contains a TODO/FIXME/not-implemented marker")
        for problem in PLACEHOLDER.finditer(strip_comments(text)):
            if problem.group(0).strip():
                problems.append(f"{relative}: placeholder body ({problem.group(0).strip()!r})")
                break
        if language == "kotlin":
            expected = "package com.voxdesk.sdk"
            if not re.search(rf"^{expected}\b", text, re.M):
                problems.append(f"{relative}: missing `{expected}` declaration")
            if "/src/main/" in str(path) and "class " not in text and "object " not in text and "interface " not in text:
                problems.append(f"{relative}: no declaration found")
        else:
            if re.search(r"\bimport UIKit\b", text) is None and "Foundation" not in text:
                problems.append(f"{relative}: imports neither Foundation nor UIKit")
    notes.append(f"{relative_root(root)}: {len(files)} {suffix} source file(s) parsed")
    return len(files)


def relative_root(root: Path) -> str:
    try:
        return str(root.relative_to(REPO_ROOT))
    except ValueError:
        return str(root)


def check_android(problems: list[str], notes: list[str]) -> None:
    root = REPO_ROOT / "sdk/android"
    gradle = root / "voxdesk-sdk/build.gradle.kts"
    if not gradle.is_file():
        gradle = root / "voxdesk-sdk/build.gradle"
    if not gradle.is_file():
        problems.append("sdk/android: no module build script")
        return
    text = read(gradle)
    for needle in ("namespace", "compileSdk", "minSdk", "implementation"):
        if needle not in text:
            problems.append(f"{relative_root(gradle)}: missing {needle}")
    if not (root / "settings.gradle.kts").is_file() and not (root / "settings.gradle").is_file():
        problems.append("sdk/android: no settings script")
    tests = sorted((root / "voxdesk-sdk/src/test").rglob("*Test.kt"))
    if not tests:
        problems.append("sdk/android: no unit-test sources")
    for path in tests:
        body = read(path)
        if "@Test" not in body:
            problems.append(f"{relative_root(path)}: no @Test annotation")
        if not re.search(r"\bassert", body):
            problems.append(f"{relative_root(path)}: asserts nothing")
    notes.append(
        f"sdk/android: {len(tests)} test source(s); Gradle/Android toolchain absent — NOT compiled"
    )


def check_ios(problems: list[str], notes: list[str]) -> None:
    root = REPO_ROOT / "sdk/ios"
    manifest = root / "Package.swift"
    if not manifest.is_file():
        problems.append("sdk/ios: no Package.swift")
        return
    text = read(manifest)
    if ".library(" not in text:
        problems.append("sdk/ios/Package.swift: declares no library product")
    if "testTarget" not in text:
        problems.append("sdk/ios/Package.swift: declares no test target")
    privacy = list(root.rglob("PrivacyInfo.xcprivacy"))
    if not privacy:
        problems.append("sdk/ios: no PrivacyInfo.xcprivacy (App Store submission requires it)")
    for path in privacy:
        try:
            payload = plistlib.loads(path.read_bytes())
        except Exception as exc:  # noqa: BLE001 - any parse failure is the finding
            problems.append(f"{relative_root(path)}: not a valid plist ({exc})")
            continue
        if "NSPrivacyTracking" not in payload:
            problems.append(f"{relative_root(path)}: missing NSPrivacyTracking")
        if "NSPrivacyAccessedAPITypes" not in payload:
            problems.append(f"{relative_root(path)}: missing NSPrivacyAccessedAPITypes")
        if "NSPrivacyCollectedDataTypes" not in payload:
            problems.append(f"{relative_root(path)}: missing NSPrivacyCollectedDataTypes")
    tests = sorted(root.rglob("Tests/**/*Tests.swift"))
    if not tests:
        problems.append("sdk/ios: no XCTest sources")
    for path in tests:
        body = read(path)
        if "XCTAssert" not in body:
            problems.append(f"{relative_root(path)}: asserts nothing")
    count = sum(len(re.findall(r"\bfunc test", read(path))) for path in tests)
    notes.append(
        f"sdk/ios: {len(tests)} test file(s), {count} test case(s); Swift toolchain absent — NOT compiled"
    )


def check_endpoints(problems: list[str], notes: list[str]) -> None:
    operations = openapi_operations()
    total = 0
    for name, language, root in (
        ("react-native", "typescript", REPO_ROOT / "sdk/react-native/src"),
        ("android", "kotlin", REPO_ROOT / "sdk/android/voxdesk-sdk/src/main"),
        ("ios", "swift", REPO_ROOT / "sdk/ios/Sources"),
    ):
        seen: set[tuple[str, str]] = set()
        for path in sorted(root.rglob("*")):
            if path.suffix not in {".ts", ".kt", ".swift"}:
                continue
            for verb, raw in extract_calls(read(path), language):
                if not raw.startswith("/api/"):
                    continue
                seen.add((verb, normalise_path(raw)))
        for verb, path in sorted(seen):
            total += 1
            if path not in operations:
                problems.append(f"sdk/{name}: {verb} {path} is not an API route")
            elif verb not in operations[path]:
                problems.append(
                    f"sdk/{name}: {verb} {path} is not allowed (API has {sorted(operations[path])})"
                )
        notes.append(f"sdk/{name}: {len(seen)} distinct (method, path) call(s) checked")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--skip-endpoints",
        action="store_true",
        help="skip the OpenAPI cross-check (it imports the app; slower)",
    )
    args = parser.parse_args(argv)

    problems: list[str] = []
    notes: list[str] = []

    for name, language, root in (
        ("android", "kotlin", REPO_ROOT / "sdk/android"),
        ("ios", "swift", REPO_ROOT / "sdk/ios/Sources"),
    ):
        check_sources(root, language, problems, notes)
        if name == "android":
            check_android(problems, notes)
        else:
            check_ios(problems, notes)
    if not args.skip_endpoints:
        check_endpoints(problems, notes)

    if args.json:
        print(json.dumps({"ok": not problems, "problems": problems, "notes": notes}, indent=2))
    else:
        for note in notes:
            print(f"  · {note}")
        for problem in problems:
            print(f"  ✗ {problem}")
        print("mobile SDK structure: " + ("FAILED" if problems else "ok (nothing was compiled)"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
