"""Security quality gate for the pipeline.

Reads the SAST (Bandit), SCA (pip-audit) and container (Trivy) reports,
prints a summary (also to the GitHub job summary) and exits non-zero when
the policy below is violated:

* Bandit: no HIGH severity findings
* pip-audit: no known vulnerabilities in runtime dependencies
* Trivy: no CRITICAL vulnerabilities that already have a fix
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reports import parse_bandit, parse_pip_audit, parse_trivy  # noqa: E402


def evaluate(bandit: dict | None, pip_audit: dict | None, trivy: dict | None) -> tuple[list[str], list[str]]:
    rows, violations = [], []
    if bandit is not None:
        rows.append(f"| Bandit (SAST) | {bandit['high']} | {bandit['medium']} | {bandit['low']} |")
        if bandit["high"] > 0:
            violations.append(f"Bandit found {bandit['high']} HIGH severity issue(s)")
    if pip_audit is not None:
        rows.append(f"| pip-audit (SCA) | {pip_audit['vulnerabilities']} vulnerable | - | - |")
        if pip_audit["vulnerabilities"] > 0:
            names = ", ".join(f"{p['name']}=={p['version']}" for p in pip_audit["packages"])
            violations.append(
                f"pip-audit found {pip_audit['vulnerabilities']} known vulnerabilities ({names})"
            )
    if trivy is not None:
        rows.append(
            f"| Trivy (image) | {trivy['critical']} critical / {trivy['high']} high | "
            f"{trivy['medium']} | {trivy['low']} |"
        )
        if trivy["critical_fixable"] > 0:
            violations.append(f"Trivy found {trivy['critical_fixable']} fixable CRITICAL vulnerabilities")
    return rows, violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bandit", type=Path)
    parser.add_argument("--pip-audit", dest="pip_audit", type=Path)
    parser.add_argument("--trivy", type=Path)
    args = parser.parse_args()

    def existing(path):
        return path if path and path.is_file() else None

    rows, violations = evaluate(
        parse_bandit(existing(args.bandit)),
        parse_pip_audit(existing(args.pip_audit)),
        parse_trivy(existing(args.trivy)),
    )
    lines = [
        "### Security gate",
        "",
        "| Scanner | High / Critical | Medium | Low |",
        "|---|---|---|---|",
        *rows,
        "",
    ]
    lines += [f"- :x: {v}" for v in violations] or ["- :white_check_mark: No policy violations"]
    report = "\n".join(lines)
    print(report)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
