#!/usr/bin/env python3
"""Compare C and Python normalizer outputs for parity testing.

This script validates that the C telemetryd normalizer produces the same
category, entity, and fingerprint results as the Python normalizer.
"""

import json
import sys
from pathlib import Path
from typing import Any


def load_c_events(path: Path) -> list[dict[str, Any]]:
    """Load events from C telemetryd NDJSON file."""
    events = []
    with open(path) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


def load_python_events(path: Path) -> list[dict[str, Any]]:
    """Load events from Python normalizer NDJSON file."""
    events = []
    with open(path) as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


def compare_events(c_events: list[dict], py_events: list[dict]) -> dict[str, Any]:
    """Compare C and Python event lists.

    Returns comparison results.
    """
    results = {
        "c_count": len(c_events),
        "py_count": len(py_events),
        "category_matches": 0,
        "category_mismatches": [],
        "entity_matches": 0,
        "entity_mismatches": [],
        "fingerprint_matches": 0,
        "fingerprint_mismatches": [],
    }

    # Index Python events by fingerprint for comparison
    py_by_fp = {e.get("fingerprint"): e for e in py_events}

    for c_event in c_events:
        c_fp = c_event.get("fingerprint")
        py_event = py_by_fp.get(c_fp)

        if not py_event:
            # Try matching by message
            c_msg = c_event.get("message", "")
            for pe in py_events:
                if pe.get("message") == c_msg:
                    py_event = pe
                    break

        if not py_event:
            continue  # No matching Python event

        # Compare category
        c_cat = c_event.get("category")
        py_cat = py_event.get("category")
        if c_cat == py_cat:
            results["category_matches"] += 1
        else:
            results["category_mismatches"].append({
                "message": c_event.get("message", "")[:100],
                "c_category": c_cat,
                "py_category": py_cat,
            })

        # Compare entity
        c_ent = c_event.get("entity")
        py_ent = py_event.get("entity")
        if c_ent == py_ent:
            results["entity_matches"] += 1
        else:
            results["entity_mismatches"].append({
                "message": c_event.get("message", "")[:100],
                "c_entity": c_ent,
                "py_entity": py_ent,
            })

        # Compare fingerprint
        if c_fp == py_event.get("fingerprint"):
            results["fingerprint_matches"] += 1
        else:
            results["fingerprint_mismatches"].append({
                "message": c_event.get("message", "")[:100],
                "c_fingerprint": c_fp,
                "py_fingerprint": py_event.get("fingerprint"),
            })

    return results


def print_results(results: dict[str, Any]) -> bool:
    """Print comparison results and return success status."""
    print("Normalizer Comparison Results")
    print("=" * 60)
    print(f"C events:      {results['c_count']}")
    print(f"Python events: {results['py_count']}")
    print()

    # Category results
    total_cat = results["category_matches"] + len(results["category_mismatches"])
    print(f"Category matches: {results['category_matches']}/{total_cat}")
    if results["category_mismatches"]:
        print("  Mismatches:")
        for m in results["category_mismatches"][:5]:  # Show first 5
            print(f"    - {m['message'][:50]}...")
            print(f"      C: {m['c_category']}, Python: {m['py_category']}")
    print()

    # Entity results
    total_ent = results["entity_matches"] + len(results["entity_mismatches"])
    print(f"Entity matches: {results['entity_matches']}/{total_ent}")
    if results["entity_mismatches"]:
        print("  Mismatches:")
        for m in results["entity_mismatches"][:5]:
            print(f"    - {m['message'][:50]}...")
            print(f"      C: {m['c_entity']}, Python: {m['py_entity']}")
    print()

    # Fingerprint results
    total_fp = results["fingerprint_matches"] + len(results["fingerprint_mismatches"])
    print(f"Fingerprint matches: {results['fingerprint_matches']}/{total_fp}")
    if results["fingerprint_mismatches"]:
        print("  Mismatches:")
        for m in results["fingerprint_mismatches"][:5]:
            print(f"    - {m['message'][:50]}...")
            print(f"      C: {m['c_fingerprint']}, Python: {m['py_fingerprint']}")
    print()

    # Overall result
    success = (
        len(results["category_mismatches"]) == 0 and
        len(results["entity_mismatches"]) == 0 and
        len(results["fingerprint_mismatches"]) == 0
    )

    if success:
        print("PASS: All normalizer outputs match")
    else:
        print("FAIL: Normalizer outputs differ")

    return success


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <c_events.jsonl> <python_events.jsonl>")
        print()
        print("To generate test data:")
        print("  1. Start elled-telemetryd and capture events:")
        print("     nc -U /run/elle/telemetry.sock > c_events.jsonl &")
        print()
        print("  2. Run Python normalizer on same input and save output")
        print()
        print("  3. Compare:")
        print(f"     {sys.argv[0]} c_events.jsonl python_events.jsonl")
        sys.exit(1)

    c_path = Path(sys.argv[1])
    py_path = Path(sys.argv[2])

    if not c_path.exists():
        print(f"Error: C events file not found: {c_path}")
        sys.exit(1)

    if not py_path.exists():
        print(f"Error: Python events file not found: {py_path}")
        sys.exit(1)

    c_events = load_c_events(c_path)
    py_events = load_python_events(py_path)

    results = compare_events(c_events, py_events)
    success = print_results(results)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
