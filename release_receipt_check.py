"""Compare a reviewed release record with a separately captured observation."""
import argparse
from datetime import datetime, timezone
import json
import re
import sys
from pathlib import Path

STATUS_CODE = {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}
SHA256 = re.compile(r"[0-9a-f]{64}")
TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})")


def parse_time(value):
    if not isinstance(value, str) or TIMESTAMP.fullmatch(value) is None:
        return None
    if value.endswith("-00:00"):
        return None  # RFC 3339 unknown offset is not acceptable evidence here.
    try:
        if value[-1] != "Z" and (int(value[-2:]) > 59 or int(value[-5:-3]) > 23):
            return None
        timestamp = value[:-1] + "+00:00" if value.endswith("Z") else value
        if "." in timestamp:
            local, offset = timestamp[:-6], timestamp[-6:]
            start, fraction = local.split(".", 1)
            timestamp = start + "." + fraction.ljust(6, "0") + offset
        return datetime.fromisoformat(timestamp).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def read_json(path):
    def no_duplicates(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError("duplicate JSON key")
            values[key] = value
        return values
    def no_constants(_):
        raise ValueError("non-JSON numeric value")
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle, object_pairs_hook=no_duplicates, parse_constant=no_constants)


def check(reviewed, observed):
    failures, unknown = [], []

    def get(obj, key, valid, label):
        if not isinstance(obj, dict):
            return None
        if key not in obj:
            unknown.append(f"{label}: missing")
            return None
        value = obj[key]
        if not valid(value):
            failures.append(f"{label}: invalid")
            return None
        return value

    def text(value):
        return isinstance(value, str) and bool(value) and value.strip() == value

    def digest(value):
        return isinstance(value, str) and SHA256.fullmatch(value) is not None

    def named_list(value):
        return (isinstance(value, list) and bool(value) and all(map(text, value))
                and len(set(value)) == len(value))

    def match(expected, actual, label):
        if expected is not None and actual is not None and expected != actual:
            failures.append(f"{label}: mismatch")

    def recorded_time(obj, key, label):
        value = get(obj, key, lambda v: parse_time(v) is not None, label)
        return parse_time(value)

    def not_later(event_time, observation_time, label):
        if event_time is not None and observation_time is not None and event_time > observation_time:
            failures.append(f"{label}: later than observation")

    for record, label in ((reviewed, "reviewed"), (observed, "observed")):
        if not isinstance(record, dict):
            failures.append(f"{label}: expected object")
        get(record, "schema_version", lambda v: type(v) is int and v == 2,
            f"{label}.schema_version")

    subject = (("deployment_id", text), ("environment", text), ("revision", text),
               ("artifact_sha256", digest))
    for key, valid in subject:
        expected = get(reviewed, key, valid, f"reviewed.{key}")
        actual = get(observed, key, valid, f"observed.{key}")
        match(expected, actual, key)
    get(observed, "evidence_ref", text, "observed.evidence_ref")
    obs_time = recorded_time(observed, "observed_at", "observed.observed_at")
    author = get(reviewed, "author", text, "reviewed.author")
    review = get(reviewed, "review", lambda v: isinstance(v, dict), "reviewed.review")
    reviewer = get(review, "reviewer", text, "reviewed.review.reviewer")
    if author is not None and reviewer is not None and author.casefold() == reviewer.casefold():
        failures.append("reviewed.review.reviewer: same recorded identity as author")
    get(review, "evidence_ref", text, "reviewed.review.evidence_ref")
    verdict = get(review, "verdict", lambda v: v in ("PASS", "BLOCK", "UNKNOWN"),
                  "reviewed.review.verdict")
    if verdict == "BLOCK":
        failures.append("reviewed.review.verdict: blocked")
    elif verdict == "UNKNOWN":
        unknown.append("reviewed.review.verdict: unknown")
    review_time = recorded_time(review, "reviewed_at", "reviewed.review.reviewed_at")
    not_later(review_time, obs_time, "reviewed.review.reviewed_at")
    for key, valid in subject:
        expected = reviewed.get(key) if isinstance(reviewed, dict) else None
        if not valid(expected):
            expected = None
        actual = get(review, key, valid, f"reviewed.review.{key}")
        match(expected, actual, f"reviewed.review.{key}")

    rollback = get(reviewed, "rollback", lambda v: isinstance(v, dict), "reviewed.rollback")
    previous = get(rollback, "artifact_sha256", digest, "reviewed.rollback.artifact_sha256")
    target = reviewed.get("artifact_sha256") if isinstance(reviewed, dict) else None
    if previous is not None and previous == target:
        failures.append("reviewed.rollback.artifact_sha256: same as release")
    get(rollback, "reference", text, "reviewed.rollback.reference")

    names = get(reviewed, "required_checks", named_list, "reviewed.required_checks")
    results = get(observed, "checks", lambda v: isinstance(v, dict), "observed.checks")
    for name in names or []:
        entry = get(results, name, lambda v: isinstance(v, dict), f"check {name!r}")
        if entry is None:
            continue
        outcome = get(entry, "outcome", lambda v: v in ("PASS", "FAIL", "SKIPPED", "UNKNOWN"),
                      f"check {name!r}.outcome")
        if outcome == "FAIL":
            failures.append(f"check {name!r}: failed")
        elif outcome in ("SKIPPED", "UNKNOWN"):
            unknown.append(f"check {name!r}: no successful execution recorded")
        if outcome in ("PASS", "FAIL"):
            exit_code = get(entry, "test_exit_code", lambda v: type(v) is int and 0 <= v <= 255,
                            f"check {name!r}.test_exit_code")
            if exit_code is not None and (exit_code == 0) != (outcome == "PASS"):
                failures.append(f"check {name!r}: outcome contradicts test exit")
            get(entry, "evidence_ref", text, f"check {name!r}.evidence_ref")
            completed = recorded_time(entry, "completed_at", f"check {name!r}.completed_at")
            not_later(completed, obs_time, f"check {name!r}.completed_at")
            for key, valid in subject:
                expected = reviewed.get(key)
                actual = get(entry, key, valid, f"check {name!r}.{key}")
                match(expected if valid(expected) else None, actual, f"check {name!r}.{key}")
    return {"status": "FAIL" if failures else "UNKNOWN" if unknown else "PASS",
            "failures": failures, "unknown": unknown}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reviewed_release")
    parser.add_argument("observed_deployment")
    args = parser.parse_args()
    records, unreadable, malformed = [], [], []
    for label, path in (("reviewed", args.reviewed_release), ("observed", args.observed_deployment)):
        try:
            records.append(read_json(path))
        except OSError:
            unreadable.append(f"{label}: unreadable")
            records.append({})
        except (ValueError, RecursionError):
            malformed.append(f"{label}: invalid JSON")
            records.append({})
    report = check(*records)
    report["failures"].extend(malformed)
    report["unknown"].extend(unreadable)
    report["status"] = "FAIL" if report["failures"] else "UNKNOWN" if report["unknown"] else "PASS"
    print(json.dumps(report))
    return STATUS_CODE[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
