# Security Policy

## Supported versions

Security fixes are provided for the latest released minor version.

| Version | Supported |
|---|---|
| 0.1.x | Yes |
| Earlier | No |

## Reporting a vulnerability

Use the repository's private vulnerability reporting feature when available. If private reporting is unavailable, open a public issue requesting a private contact channel without including exploit details, secrets, personal data, or sensitive files.

Include the affected version, operating system, Python version, reproduction conditions, impact, and any proposed mitigation.

Do not attach private diagrams or task inputs to a public report.

## Execution model

The Skill invokes a local Python script with the permissions of the calling agent. Review downloaded Skill instructions and scripts before use, and keep untrusted input files isolated from sensitive working directories.
